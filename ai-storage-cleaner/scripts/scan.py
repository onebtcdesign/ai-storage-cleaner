#!/usr/bin/env python3
"""Read-only storage scanner (macOS + Windows).

Collects disk usage, system info, and per-directory size breakdowns for the
hot spots that typically eat disk, and emits one JSON blob to stdout for Claude
to interpret and classify. Auto-detects the OS and scans the right locations.

STRICTLY READ-ONLY: only sizes/lists/reads metadata. Never creates, moves, or
deletes anything.

Output shape (same on both OSes):
{
  "generated_at", "scan_seconds",
  "system": {os, build, arch, user, home, computer_name, filesystem,
             disk_total, disk_used, disk_free, purgeable,
             disks: [{name, mount, total, used, free, external}]},   # all volumes/drives
  "groups": { "<group>": [{name, path, size_kb, size_h}], ... },
  "big_files": [{name, path, size_kb, size_h, ext}],          # largest single files
  "duplicates": [{size_kb, size_h, count, wasted_kb, wasted_h, paths:[...]}]
}
"""
import hashlib
import json
import os
import shutil
import sys
import time

HOME = os.path.expanduser("~")


def human(kb):
    """KB number -> human string like '12.3 GB'."""
    n = float(kb) * 1024
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit not in ("B", "KB") else f"{int(n)} {unit}"
        n /= 1024


def safe_key(name):
    return "".join(ch if ch.isalnum() else "_" for ch in name).strip("_") or "volume"


# ======================================================================
# macOS
# ======================================================================
import re
import subprocess


def run(cmd, timeout=180):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def du_children(path, min_kb=51200, limit=40):
    """Size every immediate child of `path`, sorted desc. macOS.

    One `du -k -d 1` call sizes all direct children at once (much faster than
    spawning du per child). The parent's own line is dropped; symlinked
    children are skipped to match the old behavior.
    """
    if not os.path.isdir(path):
        return []
    try:
        # Probe readability first so we still emit a clear denied marker.
        os.listdir(path)
    except PermissionError:
        return [{"name": "(permission denied)", "path": path,
                 "size_kb": 0, "size_h": "?", "denied": True}]
    # -d 1: one level deep; -k: KB. Errors on unreadable subdirs go to stderr
    # and are ignored, the readable children still come back on stdout.
    out = run(["du", "-k", "-d", "1", path], timeout=600)
    root = os.path.realpath(path)
    results = []
    for line in out.splitlines():
        m = re.match(r"\s*(\d+)\s+(.*)$", line)
        if not m:
            continue
        kb = int(m.group(1))
        child = m.group(2)
        if os.path.realpath(child) == root:
            continue  # the parent total line
        if os.path.dirname(child.rstrip("/")) != path.rstrip("/"):
            continue  # defensive: keep only direct children
        if os.path.islink(child):
            continue
        if kb < min_kb:
            continue
        results.append({"name": os.path.basename(child.rstrip("/")), "path": child,
                        "size_kb": kb, "size_h": human(kb)})
    results.sort(key=lambda r: r["size_kb"], reverse=True)
    return results[:limit]


MAC_TARGETS = [
    ("home", HOME, 102400),
    ("library", os.path.join(HOME, "Library"), 51200),
    ("caches", os.path.join(HOME, "Library/Caches"), 51200),
    ("containers", os.path.join(HOME, "Library/Containers"), 51200),
    ("group_containers", os.path.join(HOME, "Library/Group Containers"), 51200),
    ("app_support", os.path.join(HOME, "Library/Application Support"), 51200),
    ("applications", "/Applications", 102400),
    ("downloads", os.path.join(HOME, "Downloads"), 51200),
    ("dev_caches", None, 51200),
]

MAC_DEV_CACHE_PATHS = [
    "~/Library/Caches/pip", "~/Library/Caches/uv", "~/.cache", "~/.cargo",
    "~/.npm", "~/.pnpm-store", "~/.gradle", "~/.m2",
    "~/Library/Developer/Xcode/DerivedData", "~/Library/Developer/CoreSimulator",
    "~/Library/Developer/Xcode/iOS DeviceSupport", "~/Library/pnpm",
    "~/go/pkg", "~/.docker",
]


def dev_caches_macos():
    results = []
    for p in MAC_DEV_CACHE_PATHS:
        path = os.path.expanduser(p)
        if not os.path.isdir(path):
            continue
        out = run(["du", "-sk", path], timeout=180)
        m = re.match(r"\s*(\d+)", out)
        if not m:
            continue
        kb = int(m.group(1))
        if kb < 51200:
            continue
        results.append({"name": os.path.basename(path.rstrip("/")) or path,
                        "path": path, "size_kb": kb, "size_h": human(kb)})
    results.sort(key=lambda r: r["size_kb"], reverse=True)
    return results


def system_info_macos():
    info = {}
    info["os"] = "macOS " + run(["sw_vers", "-productVersion"]).strip()
    info["build"] = run(["sw_vers", "-buildVersion"]).strip()
    info["computer_name"] = run(["scutil", "--get", "ComputerName"]).strip() or run(["hostname"]).strip()
    arch = run(["uname", "-m"]).strip()
    brand = run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    info["arch"] = (f"Apple Silicon (arm64){' / ' + brand if brand else ''}"
                    if arch == "arm64" else f"{arch}{' / ' + brand if brand else ''}")
    info["user"] = os.environ.get("USER", "") or run(["whoami"]).strip()
    info["home"] = HOME
    total, used, free = "?", "?", "?"
    try:
        t, u, f = shutil.disk_usage("/")
        total, used, free = human(t // 1024), human(u // 1024), human(f // 1024)
    except Exception:
        pass
    info["disk_total"], info["disk_used"], info["disk_free"] = total, used, free
    dinfo = run(["diskutil", "info", "/"])
    fs = re.search(r"File System Personality:\s*(.+)", dinfo)
    info["filesystem"] = fs.group(1).strip() if fs else "APFS"
    pm = re.search(r"Purgeable Space:\s*([\d.,]+ \w+)", dinfo)
    info["purgeable"] = pm.group(1).strip() if pm else ""
    info["disk_name"] = "Macintosh HD"
    info["disks"] = list_disks_macos(total, used, free)
    return info


def list_external_volumes_macos():
    vols = []
    root = "/Volumes"
    try:
        entries = sorted(os.listdir(root))
    except Exception:
        return vols
    for name in entries:
        mount = os.path.join(root, name)
        if os.path.islink(mount) or not os.path.isdir(mount):
            continue
        try:
            st = os.stat(mount)
            # Skip the root volume alias and unmounted stubs.
            if st.st_dev == os.stat("/").st_dev:
                continue
            t, u, f = shutil.disk_usage(mount)
        except Exception:
            continue
        vols.append({
            "name": name,
            "mount": mount,
            "total": human(t // 1024),
            "used": human(u // 1024),
            "free": human(f // 1024),
            "external": True,
        })
    return vols


def list_disks_macos(total, used, free):
    disks = [{
        "name": "Macintosh HD",
        "mount": "/",
        "total": total,
        "used": used,
        "free": free,
        "external": False,
    }]
    disks.extend(list_external_volumes_macos())
    return disks


def scan_macos():
    system = system_info_macos()
    groups = {}
    for key, path, floor in MAC_TARGETS:
        groups[key] = dev_caches_macos() if key == "dev_caches" else du_children(path, min_kb=floor)
    external_volumes = [d for d in system.get("disks", []) if d.get("external") and d.get("mount")]
    groups["external_volumes"] = [
        {
            "name": d["name"],
            "path": d["mount"],
            "size_kb": 0,
            "size_h": d.get("used", "?"),
            "total_h": d.get("total", "?"),
            "free_h": d.get("free", "?"),
            "external": True,
        }
        for d in external_volumes
    ]
    for d in external_volumes:
        groups["volume_" + safe_key(d["name"])] = du_children(d["mount"], min_kb=51200, limit=80)
    return system, groups


# ======================================================================
# Windows  (UNTESTED on this build — stdlib only: os, shutil, ctypes)
# ======================================================================
def dir_size_bytes(path):
    """Recursive size in bytes via os.scandir. Skips symlinks and unreadable."""
    total = 0
    try:
        with os.scandir(path) as it:
            for e in it:
                try:
                    if e.is_symlink():
                        continue
                    if e.is_file(follow_symlinks=False):
                        total += e.stat(follow_symlinks=False).st_size
                    elif e.is_dir(follow_symlinks=False):
                        total += dir_size_bytes(e.path)
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass
    return total


def scandir_children(path, min_kb=51200, limit=40):
    """Size every immediate child of `path` via os.scandir. Windows."""
    if not path or not os.path.isdir(path):
        return []
    results = []
    try:
        entries = sorted(os.listdir(path))
    except PermissionError:
        return [{"name": "(permission denied)", "path": path,
                 "size_kb": 0, "size_h": "?", "denied": True}]
    for name in entries:
        child = os.path.join(path, name)
        if os.path.islink(child):
            continue
        try:
            kb = (os.path.getsize(child) if os.path.isfile(child)
                  else dir_size_bytes(child)) // 1024
        except (PermissionError, OSError):
            continue
        if kb < min_kb:
            continue
        results.append({"name": name, "path": child, "size_kb": kb, "size_h": human(kb)})
    results.sort(key=lambda r: r["size_kb"], reverse=True)
    return results[:limit]


def list_drives_windows():
    drives = []
    import string
    for letter in string.ascii_uppercase:
        root = f"{letter}:\\"
        if os.path.exists(root):
            try:
                t, u, f = shutil.disk_usage(root)
                drives.append({"name": root, "mount": root, "total": human(t // 1024),
                               "used": human(u // 1024), "free": human(f // 1024),
                               "external": root.upper() != os.environ.get("SystemDrive", "C:").upper() + "\\"})
            except Exception:
                continue
    return drives


def system_info_windows():
    import platform
    info = {}
    info["os"] = platform.system() + " " + platform.release()
    info["build"] = platform.version()
    info["computer_name"] = platform.node()
    info["arch"] = os.environ.get("PROCESSOR_ARCHITECTURE", platform.machine())
    info["user"] = os.environ.get("USERNAME", "")
    info["home"] = os.environ.get("USERPROFILE", HOME)
    sysdrive = os.environ.get("SystemDrive", "C:") + "\\"
    total, used, free = "?", "?", "?"
    try:
        t, u, f = shutil.disk_usage(sysdrive)
        total, used, free = human(t // 1024), human(u // 1024), human(f // 1024)
    except Exception:
        pass
    info["disk_total"], info["disk_used"], info["disk_free"] = total, used, free
    info["filesystem"] = "NTFS"
    info["purgeable"] = ""
    info["disk_name"] = sysdrive
    info["disks"] = list_drives_windows()
    return info


def scan_windows():
    system = system_info_windows()
    profile = os.environ.get("USERPROFILE", HOME)
    local = os.environ.get("LOCALAPPDATA", os.path.join(profile, "AppData", "Local"))
    roaming = os.environ.get("APPDATA", os.path.join(profile, "AppData", "Roaming"))
    targets = [
        ("user_profile", profile, 102400),
        ("appdata_local", local, 51200),
        ("appdata_roaming", roaming, 51200),
        ("temp", os.environ.get("TEMP", os.path.join(local, "Temp")), 51200),
        ("downloads", os.path.join(profile, "Downloads"), 51200),
        ("program_files", os.environ.get("ProgramFiles", r"C:\Program Files"), 102400),
        ("program_files_x86", os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), 102400),
    ]
    groups = {}
    for key, path, floor in targets:
        groups[key] = scandir_children(path, min_kb=floor)

    dev_paths = [
        os.path.join(profile, ".cache"), os.path.join(profile, ".npm"),
        os.path.join(profile, ".gradle"), os.path.join(profile, ".m2"),
        os.path.join(profile, ".nuget", "packages"), os.path.join(profile, ".cargo"),
        os.path.join(local, "pip", "Cache"), os.path.join(local, "Yarn"),
        os.path.join(local, "uv"), os.path.join(local, "ms-playwright"),
        os.path.join(local, "go-build"),
    ]
    dev = []
    for path in dev_paths:
        if not os.path.isdir(path):
            continue
        try:
            kb = dir_size_bytes(path) // 1024
        except (PermissionError, OSError):
            continue
        if kb < 51200:
            continue
        dev.append({"name": os.path.basename(path.rstrip("\\/")) or path,
                    "path": path, "size_kb": kb, "size_h": human(kb)})
    dev.sort(key=lambda r: r["size_kb"], reverse=True)
    groups["dev_caches"] = dev
    drives = [d for d in system.get("disks", []) if d.get("external")]
    groups["external_volumes"] = [
        {
            "name": d["name"],
            "path": d["mount"],
            "size_kb": 0,
            "size_h": d.get("used", "?"),
            "total_h": d.get("total", "?"),
            "free_h": d.get("free", "?"),
            "external": True,
        }
        for d in drives
    ]
    for d in drives:
        groups["volume_" + safe_key(d["name"])] = scandir_children(d["mount"], min_kb=51200, limit=80)
    return system, groups


# ======================================================================
# Big single files + duplicate detection (cross-platform)
# ======================================================================
BIG_FILE_MIN_KB = 51200        # 50 MB: floor for "large single file" candidates
BIG_FILE_TOP = 40              # how many big files to surface in the report
DUP_MIN_KB = 51200             # only dedup files this large (where it pays off)
DUP_MAX_GROUPS = 40


def _file_ext(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return ext or "(no ext)"


def find_big_files_macos(roots, min_kb=BIG_FILE_MIN_KB):
    """List candidate big files via find (C-speed), then stat each for size.

    find does not follow symlinks by default (no loops). -x keeps it on the
    starting filesystem, so a home scan won't descend into mounted volumes.
    """
    seen, out = set(), []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        res = run(["find", "-x", root, "-type", "f", "-size", "+%dk" % min_kb], timeout=600)
        for path in res.splitlines():
            if not path or path in seen:
                continue
            seen.add(path)
            try:
                if os.path.islink(path):
                    continue
                kb = os.path.getsize(path) // 1024
            except OSError:
                continue
            if kb < min_kb:
                continue
            out.append({"name": os.path.basename(path), "path": path,
                        "size_kb": kb, "size_h": human(kb), "ext": _file_ext(path)})
    out.sort(key=lambda r: r["size_kb"], reverse=True)
    return out


def walk_big_files(roots, min_kb=BIG_FILE_MIN_KB):
    """Pure os.walk variant (Windows): collect files >= min_kb."""
    seen, out = set(), []
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirs, filenames in os.walk(root):
            for name in filenames:
                p = os.path.join(dirpath, name)
                if p in seen:
                    continue
                try:
                    if os.path.islink(p):
                        continue
                    kb = os.path.getsize(p) // 1024
                except OSError:
                    continue
                if kb < min_kb:
                    continue
                seen.add(p)
                out.append({"name": name, "path": p, "size_kb": kb,
                            "size_h": human(kb), "ext": _file_ext(p)})
    out.sort(key=lambda r: r["size_kb"], reverse=True)
    return out

def _chunk_hash(path, size_bytes, chunk=262144):
    """Cheap content fingerprint: sha1 of head + tail chunks (+ exact size).

    Reading only the first and last 256 KB makes this fast on huge media files
    while still being reliable enough to flag genuine duplicates for the user to
    review. We never claim certainty — the report tells users to verify.
    """
    h = hashlib.sha1()
    h.update(str(size_bytes).encode())
    try:
        with open(path, "rb") as f:
            h.update(f.read(chunk))
            if size_bytes > chunk * 2:
                f.seek(-chunk, os.SEEK_END)
                h.update(f.read(chunk))
    except OSError:
        return None
    return h.hexdigest()


def find_duplicates(big_files, min_kb=DUP_MIN_KB, max_groups=DUP_MAX_GROUPS):
    """Group big_files by identical size, then confirm with a chunk hash.

    Reuses the already-collected big_files list (no extra disk walk). Only files
    >= min_kb are considered, since duplicate detection pays off on large files.
    Returns groups of >= 2 matching paths, sorted by wasted space desc.
    """
    by_size = {}
    for f in big_files:
        if f["size_kb"] < min_kb:
            continue
        by_size.setdefault(f["size_kb"], []).append(f)
    groups = []
    for size_kb, files in by_size.items():
        if len(files) < 2:
            continue
        by_hash = {}
        for f in files:
            digest = _chunk_hash(f["path"], size_kb * 1024)
            if digest is None:
                continue
            by_hash.setdefault(digest, []).append(f["path"])
        for paths in by_hash.values():
            if len(paths) < 2:
                continue
            wasted_kb = size_kb * (len(paths) - 1)  # keep one copy
            groups.append({
                "size_kb": size_kb, "size_h": human(size_kb),
                "count": len(paths), "wasted_kb": wasted_kb,
                "wasted_h": human(wasted_kb), "paths": sorted(paths),
            })
    groups.sort(key=lambda g: g["wasted_kb"], reverse=True)
    return groups[:max_groups]


def big_file_roots(system):
    """Where to hunt for big single files: home + each external volume root."""
    roots = [HOME]
    for d in system.get("disks", []):
        if d.get("external") and d.get("mount"):
            roots.append(d["mount"])
    return roots


# ======================================================================
def main():
    started = time.time()
    if sys.platform == "darwin":
        system, groups = scan_macos()
        big_files = find_big_files_macos(big_file_roots(system))
    elif sys.platform.startswith("win"):
        system, groups = scan_windows()
        big_files = walk_big_files(big_file_roots(system))
    else:
        print(json.dumps({"error": "unsupported_platform", "platform": sys.platform,
                          "message": "scan.py supports macOS and Windows only."},
                         ensure_ascii=False))
        return
    duplicates = find_duplicates(big_files)
    data = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "system": system,
        "groups": groups,
        "big_files": big_files[:BIG_FILE_TOP],
        "duplicates": duplicates,
        "scan_seconds": round(time.time() - started, 1),
    }
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
