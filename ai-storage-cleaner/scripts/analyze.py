#!/usr/bin/env python3
"""Turn scan.py JSON into an interactive report analysis JSON.

This is the heuristic "agent brain" for ai-storage-cleaner. It does not delete
anything. It classifies scan results into green/yellow/red cleanup decisions and
keeps all cleanup paths explicit so server.py can safely allowlist them.

Usage:
    python3 scripts/analyze.py /tmp/storage_scan.json /tmp/storage_analysis.json
"""
import json
import os
import re
import sys
import time


def parse_gb(text):
    if text is None:
        return 0.0
    m = re.search(r"([\d.]+)\s*(TB|GB|MB|KB|B)?", str(text), re.I)
    if not m:
        return 0.0
    value = float(m.group(1))
    unit = (m.group(2) or "GB").upper()
    if unit == "TB":
        return value * 1024
    if unit == "MB":
        return value / 1024
    if unit == "KB":
        return value / 1024 / 1024
    if unit == "B":
        return value / 1024 / 1024 / 1024
    return value


def gb_from_item(item):
    if item.get("size_kb"):
        return float(item["size_kb"]) / 1024 / 1024
    return parse_gb(item.get("size_h") or item.get("size"))


def size_label(gb):
    if gb >= 1024:
        return "约 %.1f TB" % (gb / 1024)
    if gb >= 1:
        return "约 %.1f GB" % gb
    return "约 %.0f MB" % (gb * 1024)


def clean_name(path):
    return os.path.basename(path.rstrip("/\\")) or path


def entry(name, path, gb, level="light", procs=None, commands=None, paths=None):
    paths = paths or [path]
    return {
        "name": name,
        "path": path,
        "size_estimate": size_label(gb),
        "cleanup_level": level,
        "kill_processes": procs or [],
        "trash_paths": paths,
        "commands": commands or [{"label": "打开位置", "cmd": "open %r" % path}],
    }


def yellow(name, path, gb, profile, manual, disposal, risk, level="medium", trash_paths=None, open_note=None):
    item = {
        "name": name,
        "path": path,
        "size": size_label(gb),
        "cleanup_level": level,
        "content_profile": profile,
        "why_manual": manual,
        "disposal": disposal,
        "risk": risk,
    }
    if trash_paths:
        item["trash_paths"] = trash_paths
    if open_note:
        item["open_note"] = open_note
    return item


def is_cache_path(path):
    p = path.lower()
    words = ("cache", "caches", "缓存", "temporary", "temp", ".tmp", "deriveddata", "coresimulator",
             "node-gyp", "typescript", ".npm", ".pnpm-store", ".gradle", ".m2", ".cache", "homebrew")
    return any(w in p for w in words)


def is_installer(path):
    return os.path.splitext(path.lower())[1] in (".dmg", ".pkg", ".zip", ".rar", ".7z", ".iso")


def infer_process(path):
    p = path.lower()
    if "telegram" in p:
        return ["Telegram"]
    if "adobe" in p or "after effects" in p:
        return ["Adobe apps"]
    if "larkshell" in p or "lark" in p:
        return ["Lark"]
    if "google" in p or "chrome" in p:
        return ["Google Chrome"]
    if "coresimulator" in p:
        return ["Xcode", "Simulator"]
    if "jianying" in p:
        return ["JianyingPro", "CapCut"]
    return []


def group_items(scan):
    for group, items in (scan.get("groups") or {}).items():
        for item in items or []:
            yield group, item


def build_green(scan):
    greens = []
    seen = set()
    for group, item in group_items(scan):
        path = item.get("path")
        if not path or item.get("denied"):
            continue
        gb = gb_from_item(item)
        if gb < 0.08:
            continue
        external = group.startswith("volume_")
        if group in ("caches", "dev_caches") or is_cache_path(path) or is_installer(path):
            # Skip broad user-data app roots that happen to contain cache in a parent name.
            if any(x in path.lower() for x in ("application support/google/chrome/profile", "xwechat_files")):
                continue
            key = os.path.realpath(os.path.expanduser(path))
            if key in seen:
                continue
            seen.add(key)
            label = "安装包/压缩包" if is_installer(path) else ("外接盘缓存" if external else "缓存/临时文件")
            greens.append(entry(
                "%s：%s" % (label, clean_name(path)),
                path,
                gb,
                level="light",
                procs=infer_process(path),
                commands=[{"label": "查看大小", "cmd": "du -sh %r" % path}],
            ))
    greens.sort(key=lambda x: parse_gb(x.get("size_estimate")), reverse=True)
    return greens[:24]


def build_yellow(scan, green_paths):
    yellows = []
    important_groups = ("app_support", "containers", "group_containers", "documents", "movies", "home")
    green_roots = {os.path.realpath(os.path.expanduser(p)) for p in green_paths}
    for group, item in group_items(scan):
        path = item.get("path")
        if not path or item.get("denied"):
            continue
        rp = os.path.realpath(os.path.expanduser(path))
        if rp in green_roots:
            continue
        gb = gb_from_item(item)
        if gb < 1:
            continue
        p = path.lower()
        if group.startswith("volume_"):
            if is_cache_path(path) or is_installer(path):
                continue
            yellows.append(yellow(
                "外接盘大目录：%s" % clean_name(path),
                path,
                gb,
                "素材、导出、旧项目或备份。",
                "需要确认是否仍在使用。",
                "先打开确认，确定不用再移到废纸篓。",
                "可能影响旧项目或素材。",
                level="medium",
                trash_paths=[path],
                open_note="优先处理重复导出、缓存、安装包和已备份素材。"
            ))
        elif group in important_groups or any(x in p for x in ("chrome", "wechat", "xinwechat", "telegram", "jianying", "movies", "documents")):
            if "chrome" in p:
                name = "Chrome 用户数据：%s" % clean_name(path)
                profile = "浏览器资料、扩展、登录态和站点数据。"
                disposal = "优先在 Chrome 设置里清缓存。"
            elif "xinwechat" in p or "wechat" in p:
                name = "微信数据：%s" % clean_name(path)
                profile = "聊天附件、图片、视频和文件。"
                disposal = "优先用微信的存储空间管理。"
            elif "telegram" in p:
                name = "Telegram 数据：%s" % clean_name(path)
                profile = "聊天媒体和应用缓存。"
                disposal = "优先用 Telegram 的 Storage Usage。"
            else:
                name = "需确认的大目录：%s" % clean_name(path)
                profile = "用户文件、素材、项目或应用数据。"
                disposal = "打开确认后再处理。"
            yellows.append(yellow(
                name,
                path,
                gb,
                profile,
                "包含用户数据。",
                disposal,
                "整包删除可能丢数据。",
                level="medium",
            ))
    yellows.sort(key=lambda x: parse_gb(x.get("size")), reverse=True)
    return yellows[:28]


def build_red(scan):
    reds = []
    apps = (scan.get("groups") or {}).get("applications") or []
    app_paths = []
    total = 0.0
    for item in apps:
        path = item.get("path", "")
        gb = gb_from_item(item)
        if gb < 0.4:
            continue
        app_paths.append(path)
        total += gb
    if app_paths:
        reds.append({
            "name": "可考虑卸载的应用候选",
            "path": "/Applications",
            "size": size_label(total),
            "cleanup_level": "deep",
            "why_keep": "应用本体不是缓存，先确认是否还在用。",
            "indirect_release": "不用的应用请用 Finder、启动台或自带卸载器处理。",
            "auto_reclaim": "卸载后可再扫残留。",
            "app_paths": app_paths[:20],
        })
    return reds


def top5(green, yellow_items, red):
    rows = []
    for tier, items, size_key in (("green", green, "size_estimate"), ("yellow", yellow_items, "size"), ("red", red, "size")):
        for item in items:
            rows.append({
                "tier": tier,
                "size": item.get(size_key, ""),
                "type": "可清理缓存" if tier == "green" else ("需人工判断" if tier == "yellow" else "谨慎清理"),
                "name": item.get("name", ""),
                "path": item.get("path", ""),
                "note": item.get("content_profile") or item.get("why_keep") or "可再生缓存",
                "_gb": parse_gb(item.get(size_key)),
            })
    rows.sort(key=lambda x: x["_gb"], reverse=True)
    out = []
    for i, row in enumerate(rows[:5], 1):
        row = dict(row)
        row.pop("_gb", None)
        row["rank"] = i
        out.append(row)
    return out


def denied(scan):
    out = []
    for _, item in group_items(scan):
        if item.get("denied") and item.get("path"):
            out.append(item["path"])
    return out[:30]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/storage_analysis.json"
    with open(src, encoding="utf-8") as f:
        scan = json.load(f)
    green = build_green(scan)
    green_paths = [p for item in green for p in item.get("trash_paths", [])]
    yellow_items = build_yellow(scan, green_paths)
    red = build_red(scan)
    g = sum(parse_gb(x.get("size_estimate")) for x in green)
    y = sum(parse_gb(x.get("size")) for x in yellow_items)
    r = sum(parse_gb(x.get("size")) for x in red)
    system = scan.get("system") or {}
    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scan_seconds": scan.get("scan_seconds"),
        "report": {
            "title": "%s 的存储分析报告" % (system.get("computer_name") or system.get("user") or "这台电脑"),
            "computer_name": system.get("computer_name") or "",
        },
        "cleanup_profile": {"default_mode": "medium", "selected_label": "中度清理"},
        "system": system,
        "top5": top5(green, yellow_items, red),
        "green": green,
        "yellow": yellow_items,
        "red": red,
        "denied": denied(scan),
        "summary": {
            "overview": "优先处理绿灯缓存，预计可释放 %s。" % size_label(g),
            "tier_stats": {
                "green": size_label(g),
                "yellow": size_label(y),
                "red": size_label(r),
            },
            "priority": [
                "轻度清理：先处理绿灯缓存、临时文件、安装包和开发缓存。",
                "中度清理：打开黄灯目录，确认素材/项目/聊天数据是否已备份或不用。",
                "清理干净：再处理旧应用、重复备份和长期不用的大目录。",
            ],
            "long_term": [
                "视频剪辑、设计、开发工具的缓存建议定期清理。",
                "外接盘建议按年份/项目归档，并定期查重和清理重复导出。",
                "系统目录和应用核心数据不要强行处理，让应用内清理入口优先。",
            ],
        },
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("分析已生成: %s" % out)


if __name__ == "__main__":
    main()
