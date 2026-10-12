"""Official documents ("เอกสารจริง"): find the exact page that says something, and turn it into a
highlighted page image the B-roll renderer can zoom into.
"""

import hashlib
import re
from pathlib import Path

import httpx
import pymupdf

from . import media

DOC_CACHE = media.CACHE / "docs"
UA = {"User-Agent": "davinci-bot/0.1 (personal video research; reads public official reports)"}
MAX_PDF = 300 * 1024 * 1024


def load(source):
    """Local PDF path, or URL (downloaded once into the cache). Returns (pymupdf.Document, local path)."""
    if re.match(r"^https?://", source):
        DOC_CACHE.mkdir(parents=True, exist_ok=True)
        path = DOC_CACHE / (hashlib.sha1(source.encode()).hexdigest()[:16] + ".pdf")
        if not path.exists():
            with httpx.stream("GET", source, headers=UA, follow_redirects=True, timeout=120) as r:
                r.raise_for_status()
                got, tmp = 0, path.with_suffix(".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        got += len(chunk)
                        if got > MAX_PDF:
                            raise RuntimeError("PDF larger than 300 MB")
                        f.write(chunk)
            tmp.rename(path)
    else:
        path = Path(source).expanduser().resolve()
    return pymupdf.open(str(path)), str(path)


def info(source):
    doc, path = load(source)
    meta = {k: v for k, v in (doc.metadata or {}).items() if v}
    toc = [{"level": lvl, "title": t, "page": p} for lvl, t, p in doc.get_toc()[:120]]
    return {"path": path, "pages": doc.page_count, "metadata": meta, "toc": toc,
            "has_text": any(doc[i].get_text().strip() for i in range(min(5, doc.page_count)))}


def search(source, query, max_hits=12, context=160):
    """Pages that contain the query (case-insensitive), with a snippet around each hit - for page-exact citations."""
    doc, path = load(source)
    q = query.lower()
    hits = []
    for i in range(doc.page_count):
        text = doc[i].get_text()
        low = text.lower()
        pos = low.find(q)
        while pos != -1 and len(hits) < max_hits:
            a, b = max(0, pos - context), min(len(text), pos + len(q) + context)
            hits.append({"page": i + 1, "snippet": " ".join(text[a:b].split())})
            pos = low.find(q, pos + len(q))
        if len(hits) >= max_hits:
            break
    return {"path": path, "query": query, "hits": hits}


def page_text(source, page):
    doc, path = load(source)
    return {"path": path, "page": page, "text": doc[page - 1].get_text()}


def page_image(source, page, highlight=None, out_dir=None, zoom=2.5):
    """Render one page; mark each highlight phrase with a yellow marker. Returns the image and a normalized
    focus box (x0, y0, x1, y1 in 0-1) around the highlights, for the zoom-in insert."""
    from PIL import Image, ImageDraw
    doc, path = load(source)
    pg = doc[page - 1]
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGBA")
    phrases = [highlight] if isinstance(highlight, str) else (highlight or [])
    rects, missing = [], []
    for ph in phrases:
        found = pg.search_for(ph)
        if not found:  # long quotes may wrap: fall back to the first words
            words = ph.split()
            found = pg.search_for(" ".join(words[:6])) if len(words) > 6 else []
        if found:
            rects += found
        else:
            missing.append(ph)
    if rects:
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        for r in rects:
            d.rectangle((r.x0 * zoom - 2, r.y0 * zoom - 1, r.x1 * zoom + 2, r.y1 * zoom + 1), fill=(255, 214, 0, 105))
        img = Image.alpha_composite(img, layer)
    W, H = pg.rect.width, pg.rect.height
    focus = None
    if rects:
        x0, y0 = min(r.x0 for r in rects), min(r.y0 for r in rects)
        x1, y1 = max(r.x1 for r in rects), max(r.y1 for r in rects)
        pad_x, pad_y = W * 0.06, H * 0.05
        focus = [round(max(0, (x0 - pad_x) / W), 4), round(max(0, (y0 - pad_y) / H), 4),
                 round(min(1, (x1 + pad_x) / W), 4), round(min(1, (y1 + pad_y) / H), 4)]
    out_dir = Path(out_dir).expanduser() if out_dir else DOC_CACHE / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{Path(path).stem}_p{page}{'_hl' if rects else ''}.png"
    img.convert("RGB").save(out)
    return {"image": str(out), "page": page, "focus": focus, "highlighted": len(rects),
            "not_found": missing, "source": path}
