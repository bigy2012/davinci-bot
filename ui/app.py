"""DaVinci Bot web UI: a local chat page that drives Claude (Agent SDK) with the davinci-bot tools and skills.

Run:  uv run --directory <project> python ui/app.py      -> http://127.0.0.1:8765
Only listens on 127.0.0.1. Every API call needs the per-launch token embedded in the page, so other
websites open in the browser cannot drive the agent.
"""

import asyncio
import json
import mimetypes
import os
import secrets
import sys
import uuid
from pathlib import Path

import uvicorn
import claude_agent_sdk as sdk
from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient, PermissionResultAllow,
                              PermissionResultDeny, ResultMessage, StreamEvent, TextBlock, ToolResultBlock,
                              ToolUseBlock, UserMessage)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from davinci_bot import bridge, media  # noqa: E402

PORT = int(os.environ.get("DAVINCI_BOT_PORT", "8765"))
TOKEN = secrets.token_urlsafe(24)
UV = os.environ.get("UV_BIN", str(Path("~/.local/bin/uv").expanduser()))
SKILLS = ["davinci-rough-cut", "davinci-subtitles", "davinci-broll", "davinci-audio", "davinci-look",
          "davinci-creator-analysis", "davinci-script"]  # action-story-channel-script is read by path (see below)
AGENT_M = Path("~/Desktop/Agent-M").expanduser()


def newest_script_skill():
    """The user's script-writing skill exists locally and synced from claude.ai; always use the newest copy."""
    cands = list(Path("~/.claude/skills").expanduser().glob("synced/*/action-story-channel-script/SKILL.md"))
    # the desktop app refreshes its own copy first (the synced copy can lag behind a claude.ai edit)
    cands += list(Path("~/Library/Application Support/Claude/local-agent-mode-sessions/skills-plugin").expanduser()
                  .glob("*/*/skills/action-story-channel-script/SKILL.md"))
    cands += [p for p in Path("~/.claude/skills").expanduser().glob("*/SKILL.md")
              if "name: action-story-channel-script" in p.read_text(encoding="utf-8", errors="ignore")[:300]
              or 'name: "action-story-channel-script"' in p.read_text(encoding="utf-8", errors="ignore")[:300]]
    return str(max(cands, key=lambda p: p.stat().st_mtime)) if cands else None
FILE_ROOTS = [ROOT, media.CACHE, Path("~/Desktop/Agent-M").expanduser(),
              Path("~/Movies/DaVinci Bot B-roll").expanduser()]

# tools that run without asking: read-only + the editing tools whose plans are approved in chat (skills enforce it)
AUTO_ALLOW_PREFIX = "mcp__davinci-bot__"
ASK_DAVINCI = {"mcp__davinci-bot__fetch_broll"}  # downloads files -> always confirm in the UI


def needs_confirm(name, tool_input):
    """official_doc only downloads when given a URL it has not cached yet."""
    if name in ASK_DAVINCI:
        return True
    if name == "mcp__davinci-bot__official_doc" and str(tool_input.get("source", "")).startswith(("http://", "https://")):
        import hashlib
        from davinci_bot import docs
        return not (docs.DOC_CACHE / (hashlib.sha1(tool_input["source"].encode()).hexdigest()[:16] + ".pdf")).exists()
    return False
AUTO_ALLOW = {"Read", "Glob", "Grep", "Skill", "TodoWrite", "WebSearch", "WebFetch"}
WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
WRITE_ROOTS = [AGENT_M, ROOT / "reports"]  # scripts, briefings, reports: written without asking

UI_PROMPT = """You are DaVinci Bot, running inside a local web app (not a terminal). The user is Thai; reply in Thai
and end polite sentences with "ครับ" (never "คะ/ค่ะ").
- The page renders Markdown. Absolute paths you print to images (.jpg/.png), audio (.mp3/.wav), video and .md
  reports become previews or links automatically, so always print the full absolute path of previews you create.
- Follow the davinci-* skills: ask for the brief, show plans and previews, and wait for approval before changing
  timelines. Never switch Resolve projects unless the user asks.
- If resolve_status fails, tell the user to open Resolve and click Workspace > Scripts > DaVinci Bot Bridge.
- Keep answers compact; use tables for plans.
- Function 7 (scripts): follow the davinci-script skill; the newest action-story-channel-script rules are in
  __SCRIPT_SKILL__ (Read it fully before writing). Save scripts under ~/Desktop/Agent-M/<topic>/."""

TOOL_NAMES_TH = {
    "resolve_status": "เช็คสถานะ Resolve", "analyze_speech": "วิเคราะห์เสียงพูด", "refine_speech": "หาขอบคำ",
    "preview_rough_cut": "ทำเสียงตัวอย่าง rough cut", "build_rough_cut": "สร้าง rough cut ใน Resolve",
    "draft_captions": "ร่างซับ", "preview_captions": "ทำภาพตัวอย่างซับ", "render_captions": "ใส่ซับลง timeline",
    "search_broll": "ค้นหาภาพ B-roll", "fetch_broll": "ดาวน์โหลด B-roll", "preview_broll": "ทำภาพตัวอย่าง B-roll",
    "place_broll": "วาง B-roll", "audio_library": "อ่านคลังเสียง", "sfx_cue_map": "หาจังหวะใส่เสียง",
    "preview_audio_mix": "มิกซ์เสียงตัวอย่าง", "place_audio": "วางเพลง/SFX", "preview_look": "ทำภาพเทียบก่อน/หลัง",
    "apply_look": "ใส่ look", "creator_footage_metrics": "วัดการนำเสนอจากฟุตเทจ", "practice_feedback": "ฟีดแบ็กการฝึก",
    "timeline_info": "อ่าน timeline", "make_prompter": "สร้าง teleprompter", "official_doc": "อ่านเอกสารทางการ", "script_digest": "นับความยาวสคริปต์", "list_timelines": "ดูรายชื่อ timeline", "transcribe": "ถอดเสียง",
}


UI_TAG = "davinci-ui"  # only chats started from this web app are listed (the folder also holds Claude Code chats)


def ui_sessions():
    out = []
    for info in sdk.list_sessions(directory=str(ROOT)):
        if info.tag != UI_TAG:
            continue
        out.append({"id": info.session_id, "title": info.custom_title or info.first_prompt or info.summary or "แชท",
                    "updated": info.last_modified, "created": info.created_at})
    return sorted(out, key=lambda x: x["updated"] or 0, reverse=True)


def is_ui_session(sid):
    info = sdk.get_session_info(sid, directory=str(ROOT))
    return bool(info and info.tag == UI_TAG)


def session_view(sid):
    """Transcript simplified for the page: user prompts (with the uuid to fork before them) and assistant
    text/tool chips. Tool results only mark the chip ok/error."""
    raw = sdk.get_session_messages(sid, directory=str(ROOT))
    items, status, prev_uuid = [], {}, None
    for m in raw:
        msg = m.message if isinstance(m.message, dict) else {}
        content = msg.get("content")
        if m.type == "user":
            if isinstance(content, str) or (isinstance(content, list) and any(b.get("type") == "text" for b in content)):
                text = content if isinstance(content, str) else "".join(b.get("text", "") for b in content if b.get("type") == "text")
                if text.strip() and not text.startswith("<"):
                    items.append({"role": "user", "text": text, "uuid": m.uuid, "fork_from": prev_uuid})
            if isinstance(content, list):
                for b in content:
                    if b.get("type") == "tool_result":
                        status[b.get("tool_use_id")] = bool(b.get("is_error"))
        elif m.type == "assistant" and isinstance(content, list):
            if not items or items[-1]["role"] != "assistant":
                items.append({"role": "assistant", "blocks": []})
            for b in content:
                if b.get("type") == "text" and b.get("text", "").strip():
                    items[-1]["blocks"].append({"text": b["text"]})
                elif b.get("type") == "tool_use":
                    short = b.get("name", "").split("__")[-1]
                    items[-1]["blocks"].append({"tool": short, "label": TOOL_NAMES_TH.get(short, short), "id": b.get("id")})
        prev_uuid = m.uuid
    for it in items:
        for b in it.get("blocks", []):
            if "tool" in b:
                b["error"] = status.get(b["id"])
    return items


class Session:
    def __init__(self):
        self.client = None
        self.session_id = None   # chat currently shown
        self.resume_id = None    # chat to resume on the next connect
        self.listeners: set[asyncio.Queue] = set()
        self.pending: dict[str, asyncio.Future] = {}
        self.busy = False
        self.task = None

    def emit(self, event):
        for q in list(self.listeners):
            q.put_nowait(event)

    async def can_use_tool(self, name, tool_input, ctx):
        if name in AUTO_ALLOW or (name.startswith(AUTO_ALLOW_PREFIX) and not needs_confirm(name, tool_input)):
            return PermissionResultAllow()
        if name in WRITE_TOOLS:
            target = Path(str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")).expanduser()
            if target.is_absolute() and any(target.resolve().is_relative_to(r.resolve()) for r in WRITE_ROOTS):
                return PermissionResultAllow()
        pid = uuid.uuid4().hex[:10]
        fut = asyncio.get_running_loop().create_future()
        self.pending[pid] = fut
        self.emit({"type": "permission", "id": pid, "tool": name,
                   "input": json.dumps(tool_input, ensure_ascii=False, indent=1)[:3000]})
        try:
            allow = await asyncio.wait_for(fut, timeout=900)
        except asyncio.TimeoutError:
            allow = False
        finally:
            self.pending.pop(pid, None)
        return PermissionResultAllow() if allow else PermissionResultDeny(message="ผู้ใช้ไม่อนุญาตคำสั่งนี้")

    def options(self):
        return ClaudeAgentOptions(
            cwd=str(ROOT),
            mcp_servers={"davinci-bot": {"type": "stdio", "command": UV,
                                         "args": ["run", "--directory", str(ROOT), "server.py"]}},
            strict_mcp_config=True,
            setting_sources=["user"],
            skills=SKILLS,
            system_prompt={"type": "preset", "preset": "claude_code",
                           "append": UI_PROMPT.replace("__SCRIPT_SKILL__", newest_script_skill() or "(not found)")},
            permission_mode="default",
            can_use_tool=self.can_use_tool,
            include_partial_messages=True,
            add_dirs=[str(p) for p in FILE_ROOTS if p.exists()],
            resume=self.resume_id,
        )

    async def ensure(self):
        if self.client is None:
            self.client = ClaudeSDKClient(options=self.options())
            await self.client.connect()
            self.resume_id = None

    async def reset(self):
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.busy = False

    async def run(self, text):
        self.busy = True
        self.emit({"type": "busy", "busy": True})
        tool_names = {}
        try:
            await self.ensure()
            await self.client.query(text)
            async for msg in self.client.receive_response():
                if isinstance(msg, StreamEvent):
                    ev = msg.event
                    if ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                        self.emit({"type": "delta", "text": ev["delta"]["text"]})
                elif isinstance(msg, AssistantMessage):
                    for b in msg.content:
                        if isinstance(b, TextBlock):
                            self.emit({"type": "text", "text": b.text})
                        elif isinstance(b, ToolUseBlock):
                            short = b.name.split("__")[-1]
                            tool_names[b.id] = short
                            self.emit({"type": "tool", "id": b.id, "name": short,
                                       "label": TOOL_NAMES_TH.get(short, short)})
                elif isinstance(msg, UserMessage) and isinstance(msg.content, list):
                    for b in msg.content:
                        if isinstance(b, ToolResultBlock):
                            self.emit({"type": "tool_done", "id": b.tool_use_id, "error": bool(b.is_error)})
                elif isinstance(msg, ResultMessage):
                    sid = getattr(msg, "session_id", None)
                    if sid and sid != self.session_id:
                        self.session_id = sid
                        try:
                            sdk.tag_session(sid, UI_TAG, directory=str(ROOT))
                        except Exception:
                            pass
                    self.emit({"type": "done", "cost": msg.total_cost_usd, "error": msg.is_error,
                               "session_id": self.session_id})
        except Exception as e:
            self.emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
            await self.reset()
        finally:
            self.busy = False
            self.emit({"type": "busy", "busy": False})


S = Session()


def _authed(request: Request):
    return secrets.compare_digest(request.headers.get("x-dvb-token") or request.query_params.get("t", ""), TOKEN)


def _deny():
    return JSONResponse({"error": "forbidden"}, status_code=403)


async def index(request):
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8").replace("__TOKEN__", TOKEN)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


async def events(request):
    if not _authed(request):
        return _deny()
    q: asyncio.Queue = asyncio.Queue()
    S.listeners.add(q)

    async def stream():
        try:
            yield f"data: {json.dumps({'type': 'busy', 'busy': S.busy})}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            S.listeners.discard(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


async def send(request):
    if not _authed(request):
        return _deny()
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "empty"}, status_code=400)
    if S.busy:
        return JSONResponse({"error": "busy"}, status_code=409)
    S.emit({"type": "user", "text": text})
    S.task = asyncio.create_task(S.run(text))
    return JSONResponse({"ok": True})


async def permission(request):
    if not _authed(request):
        return _deny()
    body = await request.json()
    fut = S.pending.get(body.get("id"))
    if fut and not fut.done():
        fut.set_result(bool(body.get("allow")))
    return JSONResponse({"ok": True})


async def interrupt(request):
    if not _authed(request):
        return _deny()
    if S.client is not None and S.busy:
        try:
            await S.client.interrupt()
        except Exception:
            pass
    return JSONResponse({"ok": True})


async def new_chat(request):
    if not _authed(request):
        return _deny()
    if S.task and not S.task.done():
        S.task.cancel()
    await S.reset()
    S.session_id = S.resume_id = None
    S.emit({"type": "reset"})
    return JSONResponse({"ok": True})


async def sessions(request):
    if not _authed(request):
        return _deny()
    q = request.query_params.get("q", "").strip().lower()
    items = await asyncio.to_thread(ui_sessions)
    if q:
        items = [i for i in items if q in i["title"].lower()]
    return JSONResponse({"items": items, "current": S.session_id})


async def open_chat(request):
    if not _authed(request):
        return _deny()
    sid = (await request.json()).get("id")
    if S.busy:
        return JSONResponse({"error": "busy"}, status_code=409)
    if not sid or not await asyncio.to_thread(is_ui_session, sid):
        return JSONResponse({"error": "not found"}, status_code=404)
    await S.reset()
    S.session_id, S.resume_id = sid, sid
    return JSONResponse({"id": sid, "items": await asyncio.to_thread(session_view, sid)})


async def rename_chat(request):
    if not _authed(request):
        return _deny()
    body = await request.json()
    if not await asyncio.to_thread(is_ui_session, body.get("id", "")):
        return JSONResponse({"error": "not found"}, status_code=404)
    await asyncio.to_thread(sdk.rename_session, body["id"], body.get("title", "").strip()[:80] or "แชท",
                            directory=str(ROOT))
    return JSONResponse({"ok": True})


async def delete_chat(request):
    if not _authed(request):
        return _deny()
    sid = (await request.json()).get("id", "")
    if not await asyncio.to_thread(is_ui_session, sid):
        return JSONResponse({"error": "not found"}, status_code=404)
    if sid == S.session_id:
        await S.reset()
        S.session_id = S.resume_id = None
    await asyncio.to_thread(sdk.delete_session, sid, directory=str(ROOT))
    return JSONResponse({"ok": True})


async def edit_message(request):
    """Edit an earlier prompt: branch the chat right before it and send the new text there (original stays)."""
    if not _authed(request):
        return _deny()
    body = await request.json()
    sid, fork_from, text = body.get("id"), body.get("fork_from"), (body.get("text") or "").strip()
    if S.busy:
        return JSONResponse({"error": "busy"}, status_code=409)
    if not text or not sid or not await asyncio.to_thread(is_ui_session, sid):
        return JSONResponse({"error": "bad request"}, status_code=400)
    await S.reset()
    if fork_from:
        info = await asyncio.to_thread(sdk.get_session_info, sid, directory=str(ROOT))
        title = f"{(info.custom_title or info.first_prompt or 'แชท')[:60]} (แก้ไข)"
        res = await asyncio.to_thread(sdk.fork_session, sid, directory=str(ROOT), up_to_message_id=fork_from,
                                      title=title)
        new_id = getattr(res, "session_id", None) or getattr(res, "new_session_id", None)
        await asyncio.to_thread(sdk.tag_session, new_id, UI_TAG, directory=str(ROOT))
        S.session_id, S.resume_id = new_id, new_id
        items = await asyncio.to_thread(session_view, new_id)
    else:  # editing the very first prompt = a fresh chat
        S.session_id = S.resume_id = None
        new_id, items = None, []
    async def later():
        await asyncio.sleep(0.4)  # let the page render the branch before the reply streams in
        await S.run(text)
    S.task = asyncio.create_task(later())
    return JSONResponse({"id": new_id, "items": items})


async def view_chat(request):
    if not _authed(request):
        return _deny()
    sid = request.query_params.get("id", "")
    if not await asyncio.to_thread(is_ui_session, sid):
        return JSONResponse({"error": "not found"}, status_code=404)
    info = await asyncio.to_thread(sdk.get_session_info, sid, directory=str(ROOT))
    return JSONResponse({"id": sid, "title": info.custom_title or info.first_prompt or "แชท",
                         "items": await asyncio.to_thread(session_view, sid)})


def _status_sync():
    state, detail = bridge.bridge_state()
    out = {"bridge": state, "detail": detail}
    if state == "idle":
        try:
            out.update(bridge.call("ping", timeout=8))
            out["timelines"] = bridge.call("list_timelines", timeout=8)
        except Exception as e:
            out["bridge"], out["detail"] = "dead", str(e)[:120]
    return out


async def status(request):
    if not _authed(request):
        return _deny()
    return JSONResponse(await asyncio.to_thread(_status_sync))


async def looks(request):
    if not _authed(request):
        return _deny()
    p = ROOT / "presets"
    names = [x.stem for x in sorted(p.glob("*.drx"))] + [x.stem for x in sorted((p / "luts").glob("*.cube"))]
    return JSONResponse(names)


async def file(request):
    if not _authed(request):
        return _deny()
    path = Path(request.query_params.get("path", "")).expanduser().resolve()
    if not path.is_file() or not any(path.is_relative_to(r.resolve()) for r in FILE_ROOTS if r.exists()):
        return Response("not found", status_code=404)
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix.lower() in (".html", ".htm") and not path.name.endswith("_prompter.html"):
        ctype = "text/plain; charset=utf-8"  # only our generated prompter pages may run as HTML here
    if path.suffix.lower() in (".md", ".txt", ".srt", ".json", ".cube"):
        ctype = "text/plain; charset=utf-8"
    return FileResponse(path, media_type=ctype)


async def shutdown(request):
    if not _authed(request):
        return _deny()
    await S.reset()
    asyncio.get_running_loop().call_later(0.3, lambda: os._exit(0))
    return JSONResponse({"ok": True})


app = Starlette(routes=[
    Route("/", index), Route("/api/events", events), Route("/api/send", send, methods=["POST"]),
    Route("/api/permission", permission, methods=["POST"]), Route("/api/interrupt", interrupt, methods=["POST"]),
    Route("/api/new", new_chat, methods=["POST"]), Route("/api/status", status), Route("/api/looks", looks),
    Route("/api/file", file), Route("/api/shutdown", shutdown, methods=["POST"]),
    Route("/api/sessions", sessions), Route("/api/open", open_chat, methods=["POST"]),
    Route("/api/rename", rename_chat, methods=["POST"]), Route("/api/delete", delete_chat, methods=["POST"]),
    Route("/api/edit", edit_message, methods=["POST"]), Route("/api/view", view_chat),
])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
