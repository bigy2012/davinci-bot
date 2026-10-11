"""Turn a channel script (.md) into a teleprompter: narration only, breath-sized lines, bold numbers.

Outputs a self-contained HTML prompter (auto-scroll, speed, mirror for beam-splitter rigs) and a plain .txt.
"""

import html
import re
from pathlib import Path

from .captions import chunk_text

STOP_HEADINGS = re.compile(r"^#{1,2}\s*(SHORTS|PRODUCTION|ภาคผนวก|FACT|Fact-sheet|หมายเหตุสำหรับผู้ผลิต|จุดตัด|Creative|CREATIVE)",
                           re.I)
SCENE = re.compile(r"^#{2,3}\s*(\[.*?\].*|.*ฉาก.*)$")


def extract_scenes(md_text):
    """[(scene title, [paragraphs])] - narration only (no visual cues, notes, quotes or tables)."""
    scenes, cur, started = [], None, False
    for raw in md_text.splitlines():
        line = raw.strip()
        if STOP_HEADINGS.match(line) and started:
            break
        m = SCENE.match(line)
        if m:
            started = True
            cur = (re.sub(r"\*\*|\[อัดใหม่.*?\]|\[เทคเดิม.*?\]", "", m.group(1)).strip(), [])
            scenes.append(cur)
            continue
        if not started or cur is None or not line:
            continue
        if line.startswith(("#", ">", "|", "---", "```")) or re.match(r"^\*\*\[.*\]\*\*$", line) or line.startswith("**["):
            continue
        line = re.sub(r"\*\*\[[^\]]*\]\*\*|\[ภาพ:[^\]]*\]|\[INTRO[^\]]*\]", "", line)
        line = re.sub(r"[*_`]", "", line).strip()
        if line.startswith(("- ", "• ")):
            line = line[2:]
        if line:
            cur[1].append(line)
    return [s for s in scenes if s[1]]


OPENER = "วันนี้เราจะมาพูดถึงเรื่อง"
HANDOFF = re.compile(r"(เดี๋ยว)?\s*(เรา)?\s*(ไป|มา)\s*(ดู|ฟัง|ลุย|เริ่ม)\s*กัน\s*(เลย|ต่อ)?\s*(ครับ|นะครับ)\s*[.!…]*$")


def check_hook(scenes):
    """Channel hook format: first line is the signature opener, last hook line hands off to the story."""
    if not scenes:
        return {"ok": False, "reason": "no scenes"}
    title, paras = scenes[0]
    first = paras[0] if paras else ""
    last = paras[-1] if paras else ""
    last_sentence = re.split(r"(?<=ครับ)\s+", last.strip())[-1]
    opener_ok = first.startswith(OPENER)
    handoff_ok = bool(HANDOFF.search(last_sentence))
    return {"ok": opener_ok and handoff_ok, "hook_scene": title,
            "opener_ok": opener_ok, "first_line": first[:80],
            "handoff_ok": handoff_ok, "last_line": last[-80:],
            "fix": [m for m, bad in [(f'ประโยคแรกต้องขึ้นต้นด้วย "{OPENER} ..."', not opener_ok),
                                      ('ประโยคสุดท้ายของ hook ต้องเป็นประโยคส่งเข้าเรื่อง เช่น "เดี๋ยวเราไปดูกันครับ" / "ไปฟังกันเลยครับ"', not handoff_ok)] if bad]}


def _bold_numbers(text):
    esc = html.escape(text)
    return re.sub(r"(\d[\d,.:]*(?:\s?(?:%|ปี|ล้าน|แสน|พัน|นาที|ชั่วโมง|ดอลลาร์|บาท|ใบ|คน|ชุด))?)", r"<b>\1</b>", esc)


def build(script_path, out_dir=None, max_chars=34):
    src = Path(script_path).expanduser()
    scenes = extract_scenes(src.read_text(encoding="utf-8"))
    if not scenes:
        raise ValueError("no narration scenes found (expected headings like '## [ฉาก 1] Hook')")
    out_dir = Path(out_dir).expanduser() if out_dir else src.parent
    stem = src.stem.replace("_script", "") + "_prompter"
    txt_lines, blocks, chars = [], [], 0
    for title, paras in scenes:
        txt_lines += ["", f"=== {title} ===", ""]
        lines_html = []
        for p in paras:
            chars += len(p)  # same convention as the script skill (wc -m: every character, marks and spaces)
            for chunk in chunk_text(p, max_chars):
                txt_lines.append(chunk)
                lines_html.append(f"<p>{_bold_numbers(chunk)}</p>")
            txt_lines.append("")
            lines_html.append('<p class="gap"></p>')
        blocks.append(f'<section><h2>{html.escape(title)}</h2>{"".join(lines_html)}</section>')
    txt_path = out_dir / f"{stem}.txt"
    txt_path.write_text("\n".join(txt_lines).strip() + "\n", encoding="utf-8")
    minutes = round(chars / 600, 1)
    page = PROMPTER_HTML.replace("__TITLE__", html.escape(src.stem)).replace("__BODY__", "\n".join(blocks)) \
        .replace("__META__", f"{len(scenes)} ฉาก · ~{chars:,} ตัวอักษร · ~{minutes} นาที (600 ตัวอักษร/นาที)")
    html_path = out_dir / f"{stem}.html"
    html_path.write_text(page, encoding="utf-8")
    return {"html": str(html_path), "txt": str(txt_path), "scenes": len(scenes), "narration_chars": chars,
            "est_minutes": minutes, "hook_check": check_hook(scenes), "scene_titles": [s[0] for s in scenes]}


PROMPTER_HTML = """<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Prompter · __TITLE__</title>
<style>
:root { --size: 54px; --speed: 1; }
html, body { margin: 0; background: #000; color: #f4f2ec; font-family: "IBM Plex Sans Thai", "Sukhumvit Set", "Thonburi", sans-serif; }
#scroller { height: 100vh; overflow-y: scroll; scrollbar-width: none; }
#scroller::-webkit-scrollbar { display: none; }
#text { padding: 45vh 8vw 80vh; font-size: var(--size); line-height: 1.45; max-width: 1400px; margin: 0 auto; }
body.mirror #text { transform: scaleX(-1); }
h2 { font-size: .42em; color: #f08a3c; letter-spacing: .04em; margin: 1.6em 0 .6em; font-weight: 600; }
p { margin: 0 0 .18em; } p.gap { height: .55em; } b { color: #ffd166; }
#marker { position: fixed; left: 0; right: 0; top: 38vh; height: calc(var(--size) * 1.5); border-top: 2px solid rgba(240,138,60,.55); border-bottom: 2px solid rgba(240,138,60,.55); pointer-events: none; }
#bar { position: fixed; left: 0; right: 0; bottom: 0; display: flex; gap: 14px; align-items: center; padding: 10px 18px; background: rgba(20,20,22,.92); font-size: 15px; color: #aaa; transition: opacity .4s; }
body.playing #bar { opacity: .15; } #bar:hover { opacity: 1 !important; }
#bar b { color: #fff; } button { background: #222; color: #eee; border: 1px solid #444; border-radius: 8px; padding: 6px 12px; font: inherit; cursor: pointer; }
#progress { position: fixed; top: 0; left: 0; height: 3px; background: #f08a3c; width: 0; }
</style></head><body>
<div id="progress"></div>
<div id="scroller"><div id="text">__BODY__</div></div>
<div id="marker"></div>
<div id="bar">
  <button id="play">▶ เริ่ม (Space)</button>
  <span>ความเร็ว <b id="spd">1.0</b> (↑/↓)</span>
  <span>ขนาด <b id="sz">54</b> (+/−)</span>
  <button id="mirror">กลับด้าน (M)</button>
  <span style="margin-left:auto">__META__</span>
</div>
<script>
const sc = document.getElementById("scroller"); let playing = false, speed = 1.0, size = 54, last = 0, acc = 0;
const ui = () => { document.getElementById("spd").textContent = speed.toFixed(1); document.getElementById("sz").textContent = size;
  document.documentElement.style.setProperty("--size", size + "px"); document.body.classList.toggle("playing", playing);
  document.getElementById("play").textContent = playing ? "❚❚ หยุด (Space)" : "▶ เริ่ม (Space)"; };
function tick(t) { if (playing) { const dt = last ? (t - last) / 1000 : 0; acc += dt * speed * size * 0.9; const px = Math.floor(acc); if (px) { sc.scrollTop += px; acc -= px; } }
  last = t; document.getElementById("progress").style.width = (100 * sc.scrollTop / (sc.scrollHeight - sc.clientHeight)) + "%"; requestAnimationFrame(tick); }
requestAnimationFrame(tick);
document.getElementById("play").onclick = () => { playing = !playing; ui(); };
document.getElementById("mirror").onclick = () => document.body.classList.toggle("mirror");
addEventListener("keydown", e => {
  if (e.code === "Space") { e.preventDefault(); playing = !playing; }
  else if (e.key === "ArrowUp") { e.preventDefault(); speed = Math.min(4, speed + 0.1); }
  else if (e.key === "ArrowDown") { e.preventDefault(); speed = Math.max(0.2, speed - 0.1); }
  else if (e.key === "+" || e.key === "=") size = Math.min(110, size + 4);
  else if (e.key === "-") size = Math.max(28, size - 4);
  else if (e.key.toLowerCase() === "m") document.body.classList.toggle("mirror");
  else if (e.key === "PageUp") sc.scrollTop -= innerHeight * .4; else if (e.key === "PageDown") sc.scrollTop += innerHeight * .4;
  ui(); });
ui();
</script></body></html>"""
