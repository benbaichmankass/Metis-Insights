#!/usr/bin/env python3
"""Fanotify FAN_ATTRIB watcher for /dev/null — names the process that strips its mode.

BL-20260629-DEVNULL-OCI-SOURCE-KILL. **Root-only, read-only observation.** Blocks on a
fanotify fd; on every attribute change to /dev/null it snapshots the acting pid's /proc
(comm / exe / cmdline / ppid + parent comm) SYNCHRONOUSLY and appends one JSON line to
DEVNULL_ATTRIB_LOG. Near-zero CPU when idle (a blocking read()); no mutation of any kind.

Why this is the right net after 7 failed rounds: rounds 5-7 of vm-devnull-source-diagnose
armed b64 chmod/fchmod/fchmodat/fchmodat2/setxattr audit rules and captured ZERO events at
strip time. fanotify FAN_ATTRIB fires at the VFS layer (fsnotify_change() from
notify_change()), which EVERY mode-change path funnels through — including a compat/b32
chmod a b64 syscall rule can't see. So it catches the strip regardless of the syscall or
process context that produced it, and reports the culprit's TGID in the event metadata.

Interpreting the log: FAN_ATTRIB fires on any metadata change, so both the STRIP
(mode -> 0644/0444, the culprit) and the ict-devnull-guard's own RESTORE (mode -> 0666)
are captured. Distinguish by the recorded `devnull_mode` after the event and by `comm`
(the guard restore shows the guard's chmod; the strip shows the real mutator). A strip
event whose /proc is already gone still yields the TGID + a `proc_gone` flag — itself a
strong clue. If a confirmed strip (guard journal shows a 0666 restore) produces NO
fanotify event at all, that is also decisive: the change bypasses the VFS notify path
entirely, pointing at a kernel-internal / devtmpfs mechanism rather than any process.
"""
import ctypes
import ctypes.util
import json
import os
import struct
import sys
import time

libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)

# fanotify_init flags
FAN_CLASS_NOTIF = 0x00000000
FAN_REPORT_FID = 0x00000200  # required for inode events like FAN_ATTRIB (Linux >= 5.1)
O_RDONLY = 0
# fanotify_mark
FAN_MARK_ADD = 0x00000001
FAN_ATTRIB = 0x00000004  # a file/dir had its metadata changed
AT_FDCWD = -100

# struct fanotify_event_metadata (native alignment; mask is __aligned_u64):
#   __u32 event_len; __u8 vers; __u8 reserved; __u16 metadata_len;
#   __aligned_u64 mask; __s32 fd; __s32 pid;
_META_FMT = "IBBHQii"
_META_SIZE = struct.calcsize(_META_FMT)  # == 24


def _read_text(path, limit=4096):
    try:
        with open(path, "rb") as fh:
            return fh.read(limit).decode("utf-8", "replace").strip()
    except Exception:
        return None


def _proc_snapshot(pid):
    """Best-effort synchronous /proc read for the acting pid (may already be gone)."""
    base = f"/proc/{pid}"
    if not os.path.isdir(base):
        return {"proc_gone": True}
    snap = {"proc_gone": False}
    snap["comm"] = _read_text(f"{base}/comm")
    try:
        snap["exe"] = os.readlink(f"{base}/exe")
    except Exception:
        snap["exe"] = None
    cmd = _read_text(f"{base}/cmdline")
    snap["cmdline"] = cmd.replace("\x00", " ").strip() if cmd else None
    status = _read_text(f"{base}/status", limit=2048) or ""
    ppid = None
    for line in status.splitlines():
        if line.startswith("PPid:"):
            ppid = line.split()[1] if len(line.split()) > 1 else None
            break
    snap["ppid"] = ppid
    snap["parent_comm"] = _read_text(f"/proc/{ppid}/comm") if ppid else None
    return snap


def _devnull_mode():
    try:
        st = os.stat("/dev/null")
        return {"mode": oct(st.st_mode & 0o7777), "inode": st.st_ino}
    except Exception as exc:  # pragma: no cover
        return {"mode": None, "error": str(exc)}


def main():
    logpath = os.environ.get("DEVNULL_ATTRIB_LOG", "/var/log/ict-devnull-attrib.jsonl")
    target = os.environ.get("DEVNULL_ATTRIB_TARGET", "/dev/null")

    fan_fd = libc.fanotify_init(FAN_CLASS_NOTIF | FAN_REPORT_FID, O_RDONLY)
    if fan_fd < 0:
        err = ctypes.get_errno()
        sys.stderr.write(f"fanotify_init failed: {os.strerror(err)} (errno {err})\n")
        return 2
    # mask is __u64 -> pass as c_uint64 so the value isn't truncated/mis-widened
    rc = libc.fanotify_mark(
        fan_fd, FAN_MARK_ADD, ctypes.c_uint64(FAN_ATTRIB), AT_FDCWD, target.encode()
    )
    if rc < 0:
        err = ctypes.get_errno()
        sys.stderr.write(f"fanotify_mark failed: {os.strerror(err)} (errno {err})\n")
        return 3

    startup = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": "watch_started",
        "target": target,
        "pid_self": os.getpid(),
        "devnull": _devnull_mode(),
    }
    with open(logpath, "a") as lf:
        lf.write(json.dumps(startup) + "\n")
        lf.flush()
    sys.stderr.write(f"devnull_attrib_watch: armed on {target}, logging to {logpath}\n")
    sys.stderr.flush()

    while True:
        buf = os.read(fan_fd, 8192)  # blocks; ~zero CPU when idle
        off = 0
        n = len(buf)
        while off + _META_SIZE <= n:
            event_len, _vers, _res, _mlen, mask, _fd, pid = struct.unpack_from(
                _META_FMT, buf, off
            )
            if event_len < _META_SIZE:
                break
            # Snapshot the culprit IMMEDIATELY (it may exit right after the chmod).
            rec = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "event": "attrib_change",
                "mask": hex(mask),
                "pid": pid,
                "proc": _proc_snapshot(pid),
                "devnull_after": _devnull_mode(),
            }
            with open(logpath, "a") as lf:
                lf.write(json.dumps(rec) + "\n")
                lf.flush()
            off += event_len


if __name__ == "__main__":
    sys.exit(main())
