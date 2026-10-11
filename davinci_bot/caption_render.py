"""Frame renderer for animated Thai captions -> transparent ProRes 4444 clips (one clip per caption).

Run as a subprocess: python -m davinci_bot.caption_render job.json
(needs DYLD_LIBRARY_PATH=/opt/homebrew/lib so Pillow finds libraqm for correct Thai mark placement)
"""

import json
import math
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from davinci_bot.captions import CLINGERS, tokenize, visible_len

FONT_DIRS = [Path.home() / "Library/Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts"),
             Path("/System/Library/Fonts/Supplemental")]


def find_font(name):
    p = Path(name).expanduser()
    if p.exists():
        return str(p)
    for d in FONT_DIRS:
        for ext in (".ttf", ".otf", ".ttc"):
            if (d / f"{name}{ext}").exists():
                return str(d / f"{name}{ext}")
    raise FileNotFoundError(f"font not found: {name}")


@lru_cache(maxsize=512)
def font(path, size):
    return ImageFont.truetype(path, max(4, int(size)), layout_engine=ImageFont.Layout.RAQM)


def rgba(hex_color, alpha=1.0):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) + (int(255 * alpha),)


def mix(c1, c2, k):
    return tuple(int(a + (b - a) * k) for a, b in zip(c1, c2))


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def ease_out_back(k, s=1.9):
    k -= 1
    return k * k * ((s + 1) * k + s) + 1


def ease_out_cubic(k):
    return 1 - (1 - k) ** 3


# ---------- layout ----------

def parse_words(text):
    """'ถูก *FBI* ตามล่า' -> [{"text","emph"}], spaces kept as width-only words."""
    words = []
    space = {"text": " ", "emph": False, "space": True}
    for i, part in enumerate(re.split(r"\*([^*]+)\*", text)):
        if part[:1].isspace() and words and not words[-1]["space"]:
            words.append(dict(space))
        if part.strip():
            for tok in tokenize(part.strip()):
                words.append({"text": tok, "emph": i % 2 == 1, "space": tok.isspace()})
        if part[-1:].isspace() and part.strip():
            words.append(dict(space))
    while words and words[0]["space"]:
        words.pop(0)
    while words and words[-1]["space"]:
        words.pop()
    return words


def break_lines(words, max_chars, max_lines):
    text_len = lambda ws: visible_len("".join(w["text"] for w in ws))  # noqa: E731
    if max_lines <= 1 or text_len(words) <= max_chars:
        return [words]
    best, best_score = [words], None
    for i in range(1, len(words)):
        if words[i]["text"] in CLINGERS:
            continue
        a, b = words[:i], words[i:]
        a = a[:-1] if a and a[-1]["space"] else a
        b = b[1:] if b and b[0]["space"] else b
        if not a or not b:
            continue
        score = max(text_len(a), text_len(b))
        if best_score is None or score < best_score:
            best, best_score = [a, b], score
    return best


def layout_caption(cap, st, W, H, fps):
    words = parse_words(cap["text"])
    lines = break_lines(words, st["max_chars"], st["max_lines"])
    fpath = find_font(st["font"])
    size = st["size"]
    max_w = st["max_width"] * W
    widths = [sum(font(fpath, size).getlength(w["text"]) for w in ln) for ln in lines]
    if max(widths) > max_w:  # shrink this caption instead of overflowing the frame
        size *= max_w / max(widths)
    f = font(fpath, size)
    ascent, descent = f.getmetrics()
    line_h = (ascent + descent) * st["line_spacing"]
    block_h = line_h * len(lines)
    cy = st["y"] * H
    top = clamp(cy - block_h / 2, H * 0.04, H * 0.96 - block_h)
    placed = []
    spoken = [w for ln in lines for w in ln if not w["space"]]
    total = sum(visible_len(w["text"]) for w in spoken) or 1
    dur = cap["end"] - cap["start"]
    t = 0.0
    for li, ln in enumerate(lines):
        lw = sum(f.getlength(w["text"]) for w in ln)
        x = W / 2 - lw / 2
        base = top + li * line_h + ascent + (line_h - ascent - descent) / 2
        for w in ln:
            wl = f.getlength(w["text"])
            item = dict(w, cx=x + wl / 2, base=base, w=wl, line=li)
            if not w["space"]:
                d = dur * visible_len(w["text"]) / total
                item["t0"], item["t1"] = t, t + d
                t += d
            placed.append(item)
            x += wl
    boxes = []
    for li, ln in enumerate(lines):
        lw = sum(f.getlength(w["text"]) for w in ln)
        y0 = top + li * line_h
        boxes.append((W / 2 - lw / 2, y0, W / 2 + lw / 2, y0 + line_h))
    return {"words": placed, "size": size, "font": fpath, "boxes": boxes,
            "center": (W / 2, top + block_h / 2)}


# ---------- drawing ----------

def draw_frame(cap, lay, st, W, H, t, fade_out):
    dur = cap["end"] - cap["start"]
    alpha, scale, dy = 1.0, 1.0, 0.0
    enter = st["enter"]
    if enter == "pop":
        k = clamp(t / 0.2)
        scale = 0.6 + 0.4 * ease_out_back(k)
        alpha = clamp(t / 0.06)
    elif enter == "slide":
        k = clamp(t / 0.22)
        dy = (1 - ease_out_cubic(k)) * H * 0.025
        alpha = k
    elif enter == "fade":
        alpha = clamp(t / 0.12)
    if fade_out:
        alpha *= clamp((dur - t) / 0.1)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if alpha <= 0.01:
        return layer
    d = ImageDraw.Draw(layer)
    cx, cy = lay["center"]

    def tf(x, y):  # caption-level scale around the block centre + slide offset
        return cx + (x - cx) * scale, cy + (y - cy) * scale + dy

    base_col = rgba(st["color"])
    hl_col = rgba(st["highlight_color"])
    emph_col = rgba(st["emph_color"])
    stroke_col = rgba(st["stroke_color"])
    # per-frame reflow: a word that grows (emphasis / pop) pushes its neighbours instead of overlapping
    lines = {}
    for w in lay["words"]:
        lines.setdefault(w["line"], []).append(w)
    jobs, boxes = [], []
    for li in sorted(lines):
        row = []
        for w in lines[li]:
            col, ws = base_col, 1.0
            if not w["space"]:
                active = w["t0"] <= t < w["t1"]
                past = t >= w["t1"]
                col = emph_col if w["emph"] else base_col
                if st["highlight"] == "word" and active:
                    col = hl_col
                elif st["highlight"] == "karaoke" and not (active or past):
                    col = mix(col, (130, 130, 130, 255), 0.55)
                if active and st["word_pop"]:
                    ws = 1 + 0.12 * math.sin(math.pi * clamp((t - w["t0"]) / 0.16) / 2)
                if w["emph"]:
                    ws *= st["emph_scale"]
            f = font(lay["font"], lay["size"] * scale * ws)
            row.append((w, f, f.getlength(w["text"]), col))
        line_w = sum(r[2] for r in row)
        _, y = tf(0, lines[li][0]["base"])
        x = cx - line_w / 2
        for w, f, wl, col in row:
            if not w["space"]:
                jobs.append((x, y, w["text"], f, col))
            x += wl
        f0 = font(lay["font"], lay["size"] * scale)
        asc, desc = f0.getmetrics()
        boxes.append((cx - line_w / 2, y - asc, cx + line_w / 2, y + desc))

    if st.get("box"):
        colors = st["box_colors"]
        col = rgba(colors[(cap["id"] - 1) % len(colors)])
        pad_x, pad_y = lay["size"] * scale * 0.35, lay["size"] * scale * 0.1
        for (x0, y0, x1, y1) in boxes:
            d.rounded_rectangle((x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y),
                                radius=lay["size"] * 0.28 * scale, fill=col)

    if st["shadow"]:
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sh)
        off = max(2, lay["size"] * 0.05)
        for x, y, text, f, _ in jobs:
            sd.text((x + off, y + off), text, font=f, anchor="ls", fill=(0, 0, 0, 170),
                    stroke_width=int(st["stroke"]), stroke_fill=(0, 0, 0, 170))
        sh = sh.filter(ImageFilter.GaussianBlur(lay["size"] * 0.06))
        layer = Image.alpha_composite(sh, layer)
        d = ImageDraw.Draw(layer)
    sw = int(round(st["stroke"] * scale))
    if sw > 0:  # strokes first so a neighbour's outline never covers a letter
        for x, y, text, f, _ in jobs:
            d.text((x, y), text, font=f, anchor="ls", fill=stroke_col, stroke_width=sw, stroke_fill=stroke_col)
    for x, y, text, f, col in jobs:
        d.text((x, y), text, font=f, anchor="ls", fill=col)
    if alpha < 0.999:
        a = layer.getchannel("A").point(lambda v: int(v * alpha))
        layer.putalpha(a)
    return layer


def render_clip(args):
    cap, st, W, H, fps, out_path, fade_out = args
    lay = layout_caption(cap, st, W, H, fps)
    n = max(1, round(cap["end"] * fps) - round(cap["start"] * fps))
    rate = {23.976: "24000/1001", 29.97: "30000/1001", 59.94: "60000/1001"}.get(round(fps, 3), str(fps))
    proc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", rate,
         "-i", "-", "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le", "-vendor", "apl0",
         "-an", out_path], stdin=subprocess.PIPE)
    for i in range(n):
        proc.stdin.write(draw_frame(cap, lay, st, W, H, i / fps, fade_out).tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed for caption {cap['id']}")
    return {"id": cap["id"], "path": out_path, "record_frame": round(cap["start"] * fps), "frames": n}


def safe_zones(img, W, H):
    """Shade where Shorts/Reels/TikTok UI covers the video (vertical only)."""
    if H <= W:
        return img
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    red = (255, 40, 40, 70)
    d.rectangle((0, H * 0.80, W, H), fill=red)          # caption/title + buttons area
    d.rectangle((W * 0.86, H * 0.45, W, H * 0.80), fill=red)  # like/comment/share column
    d.rectangle((0, 0, W, H * 0.08), fill=red)          # top bar
    return Image.alpha_composite(img, ov)


def preview(job):
    W, H, st = job["width"], job["height"], job["style"]
    caps = job["captions"]
    tiles = []
    for cap in caps:
        lay = layout_caption(cap, st, W, H, job["fps"])
        dur = cap["end"] - cap["start"]
        t = min(dur * 0.55, max(0.25, dur - 0.05))
        bg_path = cap.get("frame_path")
        if bg_path:
            bg = Image.open(bg_path).convert("RGBA")
            s = max(W / bg.width, H / bg.height)
            bg = bg.resize((int(bg.width * s), int(bg.height * s)))
            bg = bg.crop(((bg.width - W) // 2, (bg.height - H) // 2, (bg.width - W) // 2 + W,
                          (bg.height - H) // 2 + H))
        else:
            bg = Image.new("RGBA", (W, H), (60, 60, 60, 255))
        if job.get("safe_zones", True):
            bg = safe_zones(bg, W, H)
        tiles.append(Image.alpha_composite(bg, draw_frame(cap, lay, st, W, H, t, False)))
    # contact sheet, each tile scaled to 540px high
    th = 540
    tw = int(W * th / H)
    sheet = Image.new("RGB", (tw * len(tiles) + 12 * (len(tiles) - 1), th), (20, 20, 20))
    for i, tile in enumerate(tiles):
        sheet.paste(tile.convert("RGB").resize((tw, th)), (i * (tw + 12), 0))
    Path(job["out"]).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(job["out"], quality=90)
    return {"preview": job["out"]}


def main():
    if not features.check("raqm"):
        sys.exit("libraqm not available - run: brew install libraqm (Thai marks would be misplaced)")
    job = json.loads(Path(sys.argv[1]).read_text())
    if job.get("preview"):
        print(json.dumps(preview(job), ensure_ascii=False))
        return
    W, H, fps, st = job["width"], job["height"], job["fps"], job["style"]
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    caps = job["captions"]
    tasks = []
    for i, cap in enumerate(caps):
        nxt = caps[i + 1]["start"] if i + 1 < len(caps) else None
        fade_out = nxt is None or nxt - cap["end"] > 0.1
        tasks.append((cap, st, W, H, fps, str(out_dir / f"cap_{cap['id']:04d}.mov"), fade_out))
    with ProcessPoolExecutor(max_workers=6) as ex:
        clips = list(ex.map(render_clip, tasks))
    print(json.dumps({"clips": clips, "fps": fps, "out_dir": str(out_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
