"""Turn downloaded images/videos into timeline-ready B-roll clips.

Run as a subprocess: python -m davinci_bot.broll_render job.json   (DYLD_LIBRARY_PATH must reach libraqm)

layouts:  full  - fills the frame (Ken Burns on stills, blurred backdrop when the aspect differs)
          pip   - rounded card beside the speaker (16:9) with shadow, pops in/out
          top   - card in the upper part of a vertical frame
          meme  - centred card that pops in with a tilt
"""

import json
import math
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, features

from davinci_bot.caption_render import clamp, ease_out_back, ease_out_cubic, find_font

RATES = {23.976: "24000/1001", 29.97: "30000/1001", 59.94: "60000/1001"}


def rate(fps):
    return RATES.get(round(fps, 3), str(fps))


def encoder(path, W, H, fps, alpha):
    codec = ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le"] if alpha else \
        ["-c:v", "prores_ks", "-profile:v", "1", "-pix_fmt", "yuv422p10le"]
    return subprocess.Popen(["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
                             "-r", rate(fps), "-i", "-", *codec, "-vendor", "apl0", "-an", str(path)],
                            stdin=subprocess.PIPE)


def video_frames(path, src_start, n, fps, size, mode):
    """Decode n frames scaled to size ('cover' crops to fill, 'contain' fits inside). Holds the last frame."""
    w, h = size
    if mode == "cover":
        vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    else:
        vf = f"scale={w}:{h}:force_original_aspect_ratio=decrease"
    vf += f",fps={rate(fps)},tpad=stop_mode=clone:stop_duration=600"
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip().split(",")
    sw, sh = int(probe[0]), int(probe[1])
    if mode == "contain":
        s = min(w / sw, h / sh)
        w, h = int(sw * s) // 2 * 2, int(sh * s) // 2 * 2
        vf = f"scale={w}:{h},fps={rate(fps)},tpad=stop_mode=clone:stop_duration=600"
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{src_start:.3f}", "-i", path, "-vf", vf,
                             "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                            stdout=subprocess.PIPE)
    for _ in range(n):
        buf = proc.stdout.read(w * h * 3)
        if len(buf) < w * h * 3:
            break
        yield Image.frombytes("RGB", (w, h), buf)
    proc.stdout.close()
    proc.wait()


def cover(img, W, H, extra=1.0):
    s = max(W / img.width, H / img.height) * extra
    return img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)


def contain_size(w, h, bw, bh):
    s = min(bw / w, bh / h)
    return max(2, int(w * s)), max(2, int(h * s))


def blurred_backdrop(img, W, H):
    bg = cover(img.convert("RGB"), W // 4, H // 4).crop((0, 0, W // 4, H // 4))
    bg = bg.filter(ImageFilter.GaussianBlur(8)).resize((W, H), Image.BILINEAR)
    return Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.35)


def ken_burns(src, W, H, k, motion):
    """Crop window over an oversized cover image; k = 0..1 progress."""
    e = 0.5 - math.cos(math.pi * k) / 2  # ease in-out
    zw, zh = src.width, src.height
    zoom = {"in": 1 + 0.10 * e, "out": 1.10 - 0.10 * e}.get(motion, 1.06)
    cw, ch = zw / zoom, zh / zoom
    # keep aspect of the frame
    if cw / ch > W / H:
        cw = ch * W / H
    else:
        ch = cw * H / W
    cx, cy = zw / 2, zh / 2
    span_x = (zw - cw) / 2
    if motion == "left":
        cx += span_x * (0.8 - 1.6 * e)
    elif motion == "right":
        cx -= span_x * (0.8 - 1.6 * e)
    box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
    return src.transform((W, H), Image.EXTENT, box, Image.BICUBIC)


def card_box(layout, W, H, custom=None):
    """(cx, cy, max_w, max_h) of the inset area."""
    if custom:
        return custom[0] * W, custom[1] * H, custom[2] * W, custom[3] * H
    if layout == "top":
        return W * 0.5, H * 0.25, W * 0.88, H * 0.34
    if layout == "meme":
        return W * 0.5, H * 0.42, W * 0.72, H * 0.46
    if H > W:  # pip on a vertical frame behaves like top
        return W * 0.5, H * 0.25, W * 0.88, H * 0.34
    return W * 0.74, H * 0.44, W * 0.42, H * 0.62  # right of a centred speaker


def make_card(content, radius, border):
    """Rounded card with white border + soft shadow; returns (rgba, pad)."""
    w, h = content.size
    bw, bh = w + 2 * border, h + 2 * border
    pad = int(radius * 1.4) + 12
    out = Image.new("RGBA", (bw + 2 * pad, bh + 2 * pad), (0, 0, 0, 0))
    shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((pad + 6, pad + 10, pad + bw + 6, pad + bh + 10),
                                             radius=radius + border, fill=(0, 0, 0, 150))
    out = Image.alpha_composite(out, shadow.filter(ImageFilter.GaussianBlur(pad / 3)))
    frame = Image.new("RGBA", (bw, bh), (255, 255, 255, 255) if border else (0, 0, 0, 0))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=max(1, radius), fill=255)
    frame.paste(content.convert("RGB"), (border, border), mask)
    fmask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(fmask).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=radius + border, fill=255)
    out.paste(frame, (pad, pad), fmask)
    return out, pad


def credit_layer(text, W, H, anchor="left"):
    if not text:
        return None
    f = ImageFont.truetype(find_font("NotoSansThai-Regular"), max(12, int(min(W, H) * 0.022)),
                           layout_engine=ImageFont.Layout.RAQM)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x = W * 0.025 if anchor == "left" else W * 0.975
    y = H * 0.965
    d.text((x, y), text, font=f, anchor="ls" if anchor == "left" else "rs", fill=(255, 255, 255, 230),
           stroke_width=2, stroke_fill=(0, 0, 0, 160))
    return layer


def render_item(args):
    it, W, H, fps, out_dir = args
    n = max(1, round(it["end"] * fps) - round(it["start"] * fps))
    dur = n / fps
    layout = it.get("layout", "full")
    motion = it.get("motion") or ("in" if it["kind"] == "image" else "none")
    out = Path(out_dir) / f"broll_{it['idx']:03d}_{layout}.mov"
    credit = credit_layer(it.get("credit"), W, H)
    is_video = it["kind"] == "video"

    if layout == "full":
        enc = encoder(out, W, H, fps, alpha=False)
        if is_video:
            frames = video_frames(it["path"], it.get("src_start", 0), n, fps, (W, H), "cover")
            for fr in frames:
                im = fr.convert("RGBA")
                if credit:
                    im = Image.alpha_composite(im, credit)
                enc.stdin.write(im.tobytes())
        else:
            img = ImageOps.exif_transpose(Image.open(it["path"])).convert("RGB")
            aspect_gap = abs(math.log((img.width / img.height) / (W / H)))
            if aspect_gap > 0.35:  # e.g. portrait photo in a 16:9 frame: blurred backdrop + whole photo
                bg = blurred_backdrop(img, W, H).convert("RGBA")
                fw, fh = contain_size(img.width, img.height, W * 0.92, H * 0.92)
                fg = img.resize((int(fw * 1.12), int(fh * 1.12)), Image.LANCZOS)
                for i in range(n):
                    k = i / max(1, n - 1)
                    z = 1 + 0.06 * (0.5 - math.cos(math.pi * k) / 2)
                    w2, h2 = int(fw * z), int(fh * z)
                    im = bg.copy()
                    im.paste(fg.resize((w2, h2), Image.BILINEAR), ((W - w2) // 2, (H - h2) // 2))
                    if credit:
                        im = Image.alpha_composite(im, credit)
                    enc.stdin.write(im.tobytes())
            else:
                src = cover(img, W, H, 1.15)
                for i in range(n):
                    im = ken_burns(src, W, H, i / max(1, n - 1), motion).convert("RGBA")
                    if credit:
                        im = Image.alpha_composite(im, credit)
                    enc.stdin.write(im.tobytes())
    else:
        enc = encoder(out, W, H, fps, alpha=True)
        cx, cy, bw, bh = card_box(layout, W, H, it.get("box"))
        radius = int(min(W, H) * 0.025)
        border = 0 if layout == "meme" else max(4, int(min(W, H) * 0.006))
        tilt = -3.0 if layout == "meme" else 0.0
        if is_video:
            frames = video_frames(it["path"], it.get("src_start", 0), n, fps, (int(bw), int(bh)), "contain")
        else:
            img = ImageOps.exif_transpose(Image.open(it["path"])).convert("RGB")
            cw, ch = contain_size(img.width, img.height, bw, bh)
            still = img.resize((cw, ch), Image.LANCZOS)
            static_card = make_card(still, radius, border)
            frames = (None for _ in range(n))
        i = -1
        for i, fr in enumerate(frames):
            t = i / fps
            card, pad = make_card(fr, radius, border) if is_video else static_card
            k_in = clamp(t / 0.28)
            k_out = clamp((dur - t) / 0.18)
            s = (0.82 + 0.18 * ease_out_back(k_in)) * (0.94 + 0.06 * ease_out_cubic(k_out))
            if not is_video:
                s *= 1 + 0.03 * (t / max(dur, 0.01))  # gentle drift on stills
            a = clamp(t / 0.12) * k_out
            c2 = card.resize((max(2, int(card.width * s)), max(2, int(card.height * s))), Image.BILINEAR)
            if tilt:
                c2 = c2.rotate(tilt * (1 - ease_out_cubic(k_in) * 0.4), resample=Image.BICUBIC, expand=True)
            if a < 0.999:
                c2.putalpha(c2.getchannel("A").point(lambda v: int(v * a)))
            im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            im.alpha_composite(c2, (int(cx - c2.width / 2), int(cy - c2.height / 2)))
            if credit:
                im = Image.alpha_composite(im, credit)
            enc.stdin.write(im.tobytes())
        written = i + 1 if n else 0
        for _ in range(n - written):  # source video ran short: pad with empty frames
            enc.stdin.write(Image.new("RGBA", (W, H), (0, 0, 0, 0)).tobytes())
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError(f"encode failed for item {it['idx']}")
    return {"idx": it["idx"], "path": str(out), "record_frame": round(it["start"] * fps), "frames": n,
            "layout": layout}


def preview(job):
    """Middle frame of each item composited over the timeline frame -> one contact sheet."""
    W, H, fps = job["width"], job["height"], job["fps"]
    tiles = []
    for k, it in enumerate(job["items"], 1):
        one = dict(it, idx=k, end=it["start"] + max(1.2, min(3.0, it["end"] - it["start"])))
        tmp = Path(job["out_dir"]) / f"pv_{k}.mov"
        r = render_item((one, W, H, fps, job["out_dir"]))
        Path(r["path"]).rename(tmp)
        mid = (one["end"] - one["start"]) * 0.6  # after the pop-in, before the fade-out
        frame = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{mid:.3f}", "-i", str(tmp), "-frames:v", "1",
                                "-f", "rawvideo", "-pix_fmt", "rgba", "-"], capture_output=True).stdout
        tmp.unlink(missing_ok=True)
        fg = Image.frombytes("RGBA", (W, H), frame)
        bg = Image.open(it["frame_path"]).convert("RGBA") if it.get("frame_path") else \
            Image.new("RGBA", (W, H), (60, 60, 60, 255))
        bg = cover(bg, W, H).crop((0, 0, W, H)) if bg.size != (W, H) else bg
        tiles.append(Image.alpha_composite(bg, fg).convert("RGB"))
    th = 360 if W > H else 540
    tw = int(W * th / H)
    sheet = Image.new("RGB", (tw * len(tiles) + 10 * (len(tiles) - 1), th), (20, 20, 20))
    for i, t in enumerate(tiles):
        sheet.paste(t.resize((tw, th)), (i * (tw + 10), 0))
    sheet.save(job["out"], quality=88)
    return {"preview": job["out"]}


def main():
    if not features.check("raqm"):
        sys.exit("libraqm not available - run: brew install libraqm")
    job = json.loads(Path(sys.argv[1]).read_text())
    Path(job["out_dir"]).mkdir(parents=True, exist_ok=True)
    if job.get("preview"):
        print(json.dumps(preview(job)))
        return
    W, H, fps = job["width"], job["height"], job["fps"]
    tasks = [(dict(it, idx=i + 1), W, H, fps, job["out_dir"]) for i, it in enumerate(job["items"])]
    with ProcessPoolExecutor(max_workers=6) as ex:
        clips = list(ex.map(render_item, tasks))
    print(json.dumps({"clips": clips}, ensure_ascii=False))


if __name__ == "__main__":
    main()
