#!/usr/bin/env python3
"""Serve the storage report with a guarded one-click safe-clean API (macOS + Windows).

Starts on 127.0.0.1 + a random port + a random per-session token, serves the
interactive report, and exposes POST /action to move allowlisted paths to Trash.
Stop with Ctrl+C.

Usage:
    server.py <analysis.json>

SAFETY MODEL — read before changing:
- Allowlist: only paths listed in this report's green/yellow items
  `trash_paths` are accepted for Trash/Recycling. These paths may live under the
  home directory or on external volumes, but every request path is realpath-
  resolved and must exactly match the report allowlist. Anything else is
  rejected. This is the core guard — the endpoint cannot be used to delete
  arbitrary files.
- Bound to 127.0.0.1 only; every POST requires the session token; Host header
  must be 127.0.0.1 (blocks DNS-rebinding from a malicious page).
- Safety mode is always on by default: destructive actions are "trash" only
  (Finder / Recycle Bin, reversible). Hard delete is intentionally not exposed.
  The browser confirms each action before sending.
"""
import json
import os
import secrets
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE = os.path.join(HERE, "..", "assets", "report_template.html")
HOME = os.path.realpath(os.path.expanduser("~"))
TOKEN = secrets.token_urlsafe(24)

DATA = {}
TPL = ""
TRASH_ALLOW = set()
OPEN_ALLOW = set()
LOG_PATH = ""


def expand(p):
    return os.path.realpath(os.path.expanduser(p))


def load(src):
    with open(src, encoding="utf-8") as f:
        data = json.load(f)
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    # 两套白名单：
    #   trash = 绿灯 + 橙灯 trash_paths（全部只准移废纸篓/回收站）
    #   open  = trash 全集 + 橙灯 path + 红灯 app_paths（仅"在文件管理器打开"，非破坏性）
    trash_allow, open_allow = set(), set()
    for it in data.get("green", []):
        for p in (it.get("trash_paths") or []):
            rp = expand(p)
            trash_allow.add(rp); open_allow.add(rp)
    for it in data.get("yellow", []):
        for p in (it.get("trash_paths") or []):
            rp = expand(p)
            trash_allow.add(rp); open_allow.add(rp)
        if it.get("path"):
            rp = expand(it["path"])
            if os.path.exists(rp):
                open_allow.add(rp)
    # 红灯只允许"打开"（应用本体在 /Applications，删除让用户在访达里自己卸）
    for it in data.get("red", []):
        for p in (it.get("app_paths") or []):
            rp = expand(p)
            if os.path.exists(rp):
                open_allow.add(rp)
    return data, tpl, trash_allow, open_allow


def move_to_trash(path):
    if sys.platform == "darwin":
        _trash_macos(path)
    elif sys.platform.startswith("win"):
        _trash_windows(path)
    else:
        raise OSError("移到废纸篓仅支持 macOS / Windows")


def _trash_macos(path):
    # osascript Finder delete -> macOS Trash, recoverable. First run may prompt
    # for Finder automation permission. Fall back to ~/.Trash move if it fails.
    script = 'tell application "Finder" to delete (POSIX file %s as alias)' % json.dumps(path)
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        dest = os.path.join(HOME, ".Trash",
                            os.path.basename(path.rstrip("/")) + "." + time.strftime("%H%M%S"))
        shutil.move(path, dest)


def _trash_windows(path):
    # Send to Recycle Bin via SHFileOperationW with FOF_ALLOWUNDO (stdlib ctypes).
    # UNTESTED on this build — verify on a real Windows machine.
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x0040
    FOF_NOCONFIRMATION = 0x0010
    FOF_SILENT = 0x0004
    op = SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = os.path.abspath(path) + "\x00\x00"  # double-null terminated list
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0:
        raise OSError("SHFileOperation failed (code %d)" % rc)


def init_log(src):
    base = os.path.splitext(os.path.basename(src))[0] or "storage_cleanup"
    return os.path.join("/tmp", "%s_%s_log.json" % (base, time.strftime("%Y%m%d")))


def append_log(mode, done):
    if not LOG_PATH or not done:
        return
    payload = {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "actions": []}
    if os.path.exists(LOG_PATH):
        try:
            with open(LOG_PATH, encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            pass
    payload.setdefault("actions", [])
    for p in done:
        payload["actions"].append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,
            "path": p
        })
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def read_log():
    if not LOG_PATH or not os.path.exists(LOG_PATH):
        return {"actions": []}
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"actions": []}


def open_in_file_manager(path):
    # 非破坏性：在访达 / 资源管理器里打开该位置，方便用户自己审查删除
    target = path if os.path.isdir(path) else os.path.dirname(path)
    if sys.platform == "darwin":
        # .app 是 bundle，对它用 open 会"启动应用"而非显示；必须用 open -R 在访达里选中。
        if target.rstrip("/").endswith(".app"):
            r = subprocess.run(["open", "-R", target], capture_output=True, text=True)
            if r.returncode != 0:
                raise OSError((r.stderr or "open -R 失败").strip())
            return
        # 普通文件夹：先试直接打开看内容；沙盒容器（如微信）open 会报 -10814，
        # 退回 open -R 在父目录里选中它。两者都失败才算错。
        r = subprocess.run(["open", target], capture_output=True, text=True)
        if r.returncode != 0:
            r2 = subprocess.run(["open", "-R", target], capture_output=True, text=True)
            if r2.returncode != 0:
                raise OSError((r.stderr or r2.stderr or "open 失败").strip())
    elif sys.platform.startswith("win"):
        subprocess.run(["explorer", target])  # explorer 退出码不可靠，不据此判成败
    else:
        raise OSError("打开文件夹仅支持 macOS / Windows")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            blob = json.dumps(DATA, ensure_ascii=False)
            cfg = json.dumps({"token": TOKEN, "endpoint": "/action", "logEndpoint": "/log", "safeMode": True})
            html = TPL.replace("__REPORT_DATA__", blob).replace("__DELETE_CONFIG__", cfg)
            self._send(200, html, "text/html; charset=utf-8")
        elif self.path == "/log":
            self._send(200, json.dumps(read_log(), ensure_ascii=False))
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path != "/action":
            self._send(404, json.dumps({"ok": False, "error": "not found"}))
            return
        # DNS-rebinding guard: only accept local Host
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            self._send(403, json.dumps({"ok": False, "error": "host 不被允许"}))
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self._send(400, json.dumps({"ok": False, "error": "请求格式错误"}))
            return
        if req.get("token") != TOKEN:
            self._send(403, json.dumps({"ok": False, "error": "token 校验失败"}))
            return
        mode = req.get("mode")
        allow = {"trash": TRASH_ALLOW, "open": OPEN_ALLOW}.get(mode)
        if allow is None:
            self._send(400, json.dumps({"ok": False, "error": "安全模式只支持移到废纸篓/回收站或打开文件夹"}))
            return
        done = []
        for p in (req.get("paths") or []):
            rp = expand(p)
            if rp not in allow:
                self._send(403, json.dumps({"ok": False, "error": "路径不在白名单：%s" % p}))
                return
            if mode == "trash" and (rp == "/" or rp in ("/System", "/Library", "/usr", "/bin", "/sbin", "/private", "/Applications")):
                self._send(403, json.dumps({"ok": False, "error": "拒绝处理系统级路径：%s" % p}))
                return
            try:
                if mode == "open":
                    open_in_file_manager(rp)
                elif not os.path.exists(rp):
                    pass  # already gone, treat as success
                else:
                    move_to_trash(rp)
                done.append(p)
            except Exception as e:
                self._send(500, json.dumps({"ok": False, "error": str(e)}))
                return
        if mode == "trash":
            append_log(mode, done)
        self._send(200, json.dumps({"ok": True, "done": done, "log": read_log()}))


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    global DATA, TPL, TRASH_ALLOW, OPEN_ALLOW, LOG_PATH
    DATA, TPL, TRASH_ALLOW, OPEN_ALLOW = load(sys.argv[1])
    LOG_PATH = init_log(sys.argv[1])
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    url = "http://127.0.0.1:%d/" % port
    print("报告服务已启动：" + url)
    print("安全清理模式：所有清理动作只会移到废纸篓/回收站，不直接删除")
    print("可移废纸篓/回收站 %d 项 | 清理日志：%s" % (len(TRASH_ALLOW), LOG_PATH))
    print("用完按 Ctrl+C 停止服务（服务关掉后按钮即失效）")
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止服务。")


if __name__ == "__main__":
    main()
