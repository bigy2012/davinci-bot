"""Client for the Lua bridge running inside DaVinci Resolve (free edition).

Free Resolve has no external scripting, so we talk to the in-app Lua bridge through files:
  request  -> IPC_DIR/req_<n>.lua     (a Lua table literal the bridge reads with dofile)
  reply    <- IPC_DIR/reply.prefs     (Fusion prefs dump containing Reply = "<n>|<hex json>")
"""

import json
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path

if sys.platform == "win32":
    IPC_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming")) / "davinci-bot" / "ipc"
else:
    IPC_DIR = Path("~/Library/Application Support/davinci-bot/ipc").expanduser()
REPLY = IPC_DIR / "reply.prefs"
BEAT = IPC_DIR / "beat.prefs"
BEAT_RE = re.compile(r'Beat = "(idle|busy)\|([^"]*)"')
REPLY_RE = re.compile(r'DVBot = \{[^}]*?Reply = "([^"|]*)\|(ack|[0-9a-f]*)"')
ACK_TIMEOUT = 4.0

NOT_RUNNING = (
    "เชื่อมต่อ DaVinci Resolve ไม่ได้: เปิด Resolve + เปิดโปรเจกต์ แล้วไปที่ "
    "Workspace > Scripts > DaVinci Bot Bridge"
)


class BridgeError(RuntimeError):
    pass


@contextmanager
def _ipc_lock():
    """Exclusive lock so several Claude sessions never write the same request number."""
    with open(IPC_DIR / "lock", "a+") as f:
        if sys.platform == "win32":
            import msvcrt
            while True:
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX)
            yield


def to_lua(v):
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        out = []
        for ch in v:
            if ch in '"\\':
                out.append("\\" + ch)
            elif ch == "\n":
                out.append("\\n")
            elif ord(ch) < 32:
                out.append("\\%03d" % ord(ch))
            else:
                out.append(ch)
        return '"' + "".join(out) + '"'
    if isinstance(v, (list, tuple)):
        return "{" + ",".join(to_lua(x) for x in v) + "}"
    if isinstance(v, dict):
        return "{" + ",".join(f"[{to_lua(str(k))}]={to_lua(x)}" for k, x in v.items() if x is not None) + "}"
    raise TypeError(f"cannot send {type(v).__name__} to Resolve")


def _read_reply():
    """Return (id, payload) from the reply file, or None while it is missing or half-written."""
    try:
        m = REPLY_RE.search(REPLY.read_text(encoding="utf-8", errors="replace"))
    except OSError:  # missing, or (Windows) locked while Resolve writes it
        return None
    return (m.group(1), m.group(2)) if m else None


def bridge_state():
    """("idle"|"busy"|"dead", detail). Idle bridges rewrite beat.prefs every second."""
    try:
        age = time.time() - BEAT.stat().st_mtime
        m = BEAT_RE.search(BEAT.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return "dead", "no heartbeat file"
    if m and m.group(1) == "busy":
        return "busy", m.group(2)
    if age < 3.5:
        return "idle", ""
    return "dead", f"last heartbeat {age:.0f}s ago"


def call(op, timeout=900, **args):
    IPC_DIR.mkdir(parents=True, exist_ok=True)
    with _ipc_lock():
        counter_file = IPC_DIR / "counter.lua"
        try:
            n = int(counter_file.read_text().split()[-1])
        except (FileNotFoundError, ValueError, IndexError):
            n = 1
        req = IPC_DIR / f"req_{n}.lua"
        tmp = req.with_suffix(".tmp")
        tmp.write_text("return " + to_lua({"op": op, "args": args}), encoding="utf-8")
        os.replace(tmp, req)  # bridge must never see a half-written file
        counter_file.write_text(f"return {n + 1}")

        acked, payload = False, None
        start = time.time()
        try:
            while True:
                got = _read_reply()
                if got and got[0] == str(n):
                    if got[1] == "ack":
                        acked = True
                    else:
                        try:
                            payload = bytes.fromhex(got[1]).decode("utf-8")
                            json.loads(payload)
                            break
                        except ValueError:
                            pass  # file still being written
                elapsed = time.time() - start
                if not acked and elapsed > ACK_TIMEOUT:
                    state, detail = bridge_state()
                    if state == "dead":  # busy = another request (maybe from another session) is running
                        raise BridgeError(f"{NOT_RUNNING} ({detail})")
                if elapsed > timeout:
                    raise BridgeError(f"Resolve ไม่ตอบกลับภายใน {timeout} วินาที (op={op})")
                time.sleep(0.03)
        except BridgeError:
            if not acked:
                # never let a stale request run later when the bridge starts
                req.unlink(missing_ok=True)
                counter_file.write_text(f"return {n}")
            raise
        req.unlink(missing_ok=True)

    reply = json.loads(payload)
    if not reply.get("ok"):
        raise BridgeError(reply.get("error", "unknown error"))
    return reply["result"]
