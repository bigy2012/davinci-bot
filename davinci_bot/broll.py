"""Find licensed B-roll / inserts / memes online, keep track of licences, and download what the user approved.

Sources (no API key needed): Openverse (CC images from Flickr, Wikimedia, museums...), Wikimedia Commons,
Internet Archive (archival film/news), NASA (public domain), Imgflip (meme templates - fair use only).
Optional with free keys in env: PEXELS_API_KEY, PIXABAY_API_KEY (stock video/photos).
"""

import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

import httpx

from . import media

# Wikimedia only serves clients whose User-Agent carries contact info; set WIKIMEDIA_CONTACT (url or email)
WIKIMEDIA_CONTACT = os.environ.get("WIKIMEDIA_CONTACT", "").strip()
UA = {"User-Agent": "davinci-bot/0.1 (personal video-editing assistant; DaVinci Resolve MCP"
                    + (f"; {WIKIMEDIA_CONTACT}" if WIKIMEDIA_CONTACT else "") + ")"}
CAND_FILE = media.CACHE / "broll" / "candidates.json"
LIBRARY = Path(os.environ.get("DAVINCI_BOT_BROLL_DIR", "~/Movies/DaVinci Bot B-roll")).expanduser()
MAX_DOWNLOAD = 600 * 1024 * 1024

# A = free to reuse (PD / CC0 / Pexels / Pixabay), B = reuse with credit (CC BY / BY-SA),
# C = fair use or unknown (memes, archive items without a stated licence) - commentary only, use sparingly
# X = not allowed on a monetised channel (NC / ND) - never returned
LICENSE_CLASS = {"cc0": "A", "pdm": "A", "pd": "A", "public domain": "A", "nasa": "A", "pexels": "A",
                 "pixabay": "A", "by": "B", "by-sa": "B", "nc": "X", "nd": "X"}


def _client():
    return httpx.Client(timeout=30, headers=UA, follow_redirects=True)


def classify(license_name):
    name = (license_name or "").lower()
    if "nc" in re.split(r"[-\s]", name) or "nd" in re.split(r"[-\s]", name):
        return "X"
    for key in ("cc0", "pdm", "public domain", "nasa", "pexels", "pixabay"):
        if key in name:
            return "A"
    if name.startswith("pd") or name == "pd":
        return "A"
    if name.startswith("cc by") or name in ("by", "by-sa") or name.startswith("by"):
        return "B"
    return "C"


def _cid(source, key):
    return source[:2] + hashlib.sha1(f"{source}:{key}".encode()).hexdigest()[:8]


def _strip_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()


# ---------- sources ----------

def _openverse(c, q, kind, limit):
    if kind == "video":
        return []
    r = c.get("https://api.openverse.org/v1/images/",
              params={"q": q, "page_size": limit, "license_type": "commercial", "mature": "false"})
    r.raise_for_status()
    out = []
    for x in r.json().get("results", []):
        lic = f"CC {x['license'].upper()} {x.get('license_version') or ''}".strip()
        if x["license"] in ("cc0", "pdm"):
            lic = x["license"].upper()
        out.append({"source": f"openverse/{x.get('source')}", "kind": "image", "title": x.get("title") or q,
                    "thumb": x.get("thumbnail") or x["url"], "url": x["url"], "page": x.get("foreign_landing_url"),
                    "license": lic, "author": x.get("creator") or "", "attribution": x.get("attribution") or "",
                    "width": x.get("width"), "height": x.get("height"), "size": x.get("filesize")})
    return out


def _commons(c, q, kind, limit):
    ftype = {"image": "filetype:bitmap", "video": "filetype:video"}.get(kind, "")
    params = {"action": "query", "format": "json", "generator": "search", "gsrsearch": f"{q} {ftype}".strip(),
              "gsrnamespace": 6, "gsrlimit": limit, "prop": "imageinfo",
              "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 3840}
    for attempt in range(3):
        r = c.get("https://commons.wikimedia.org/w/api.php", params=params)
        if r.status_code != 429:
            break
        time.sleep(2 * (attempt + 1))
    r.raise_for_status()
    out = []
    for p in (r.json().get("query") or {}).get("pages", {}).values():
        ii = p["imageinfo"][0]
        meta = ii.get("extmetadata", {})
        mime = ii.get("mime", "")
        k = "video" if mime.startswith("video") else "image"
        lic = _strip_html(meta.get("LicenseShortName", {}).get("value"))
        out.append({"source": "wikimedia", "kind": k, "title": p["title"].removeprefix("File:"),
                    "thumb": ii.get("thumburl") if k == "image" else ii.get("thumburl") or ii["url"],
                    # images: a 3840px rendition instead of 50-megapixel originals
                    "url": (ii.get("thumburl") or ii["url"]) if k == "image" else ii["url"],
                    "page": ii.get("descriptionurl"), "license": lic,
                    "author": _strip_html(meta.get("Artist", {}).get("value")),
                    "width": ii.get("width"), "height": ii.get("height"), "size": ii.get("size")})
    return out


def _archive(c, q, kind, limit):
    if kind == "image":
        return []
    r = c.get("https://archive.org/advancedsearch.php",
              params={"q": f"({q}) AND mediatype:(movies)", "fl[]": ["identifier", "title", "licenseurl", "year",
                                                                       "collection"],
                      "rows": limit, "output": "json"})
    r.raise_for_status()
    out = []
    for d in r.json()["response"]["docs"]:
        coll = d.get("collection") or []
        coll = coll if isinstance(coll, list) else [coll]
        url = d.get("licenseurl") or ""
        m = re.search(r"creativecommons\.org/licenses/([a-z\-]+)/([\d.]+)", url)
        if m:
            lic = f"CC {m.group(1).upper()} {m.group(2)}"
        elif "publicdomain/zero" in url:
            lic = "CC0"
        elif "publicdomain" in url or "prelinger" in coll:
            lic = "Public Domain" + (" (Prelinger Archives)" if "prelinger" in coll else "")
        else:
            lic = "unknown - check page"
        out.append({"source": "internet-archive", "kind": "video", "title": d.get("title") or d["identifier"],
                    "thumb": f"https://archive.org/services/img/{d['identifier']}",
                    "url": None, "ia_id": d["identifier"], "page": f"https://archive.org/details/{d['identifier']}",
                    "license": lic, "author": "", "year": d.get("year")})
    return out


def _nasa(c, q, kind, limit):
    types = {"image": "image", "video": "video"}.get(kind, "image,video")
    r = c.get("https://images-api.nasa.gov/search", params={"q": q, "media_type": types})
    r.raise_for_status()
    out = []
    for it in r.json()["collection"]["items"][:limit]:
        d = it["data"][0]
        thumb = next((l["href"] for l in it.get("links", []) if l.get("rel") == "preview"), None)
        out.append({"source": "nasa", "kind": d["media_type"], "title": d.get("title", ""), "thumb": thumb,
                    "url": None, "nasa_id": d["nasa_id"], "page": f"https://images.nasa.gov/details/{d['nasa_id']}",
                    "license": "NASA (public domain, no endorsement)", "author": d.get("center") or "NASA"})
    return out


def _imgflip(c, q, kind, limit):
    if kind == "video":
        return []
    memes = c.get("https://api.imgflip.com/get_memes").json()["data"]["memes"]
    words = [w for w in q.lower().split() if w not in ("meme", "memes")]
    hits = [m for m in memes if all(w in m["name"].lower() for w in words)] if words else memes
    return [{"source": "imgflip", "kind": "image", "title": m["name"], "thumb": m["url"], "url": m["url"],
             "page": f"https://imgflip.com/memetemplate/{m['id']}", "license": "meme template - fair use only",
             "author": "", "width": m["width"], "height": m["height"]} for m in hits[:limit]]


def _pexels(c, q, kind, limit):
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return []
    out = []
    if kind in ("video", "any"):
        r = c.get("https://api.pexels.com/videos/search", params={"query": q, "per_page": limit},
                  headers={"Authorization": key})
        for v in r.json().get("videos", []):
            best = max((f for f in v["video_files"] if (f.get("height") or 0) <= 2160),
                       key=lambda f: f.get("height") or 0, default=None)
            if best:
                out.append({"source": "pexels", "kind": "video", "title": v.get("url", "").rsplit("/", 2)[-2],
                            "thumb": v["image"], "url": best["link"], "page": v["url"], "license": "Pexels",
                            "author": v["user"]["name"], "width": best.get("width"), "height": best.get("height"),
                            "duration": v.get("duration")})
    if kind in ("image", "any"):
        r = c.get("https://api.pexels.com/v1/search", params={"query": q, "per_page": limit},
                  headers={"Authorization": key})
        for p in r.json().get("photos", []):
            out.append({"source": "pexels", "kind": "image", "title": p.get("alt") or q, "thumb": p["src"]["medium"],
                        "url": p["src"]["original"], "page": p["url"], "license": "Pexels",
                        "author": p["photographer"], "width": p["width"], "height": p["height"]})
    return out


def _pixabay(c, q, kind, limit):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return []
    out = []
    if kind in ("video", "any"):
        r = c.get("https://pixabay.com/api/videos/", params={"key": key, "q": q, "per_page": max(3, limit)})
        for v in r.json().get("hits", []):
            f = v["videos"].get("large") or v["videos"].get("medium")
            out.append({"source": "pixabay", "kind": "video", "title": v.get("tags", q), "thumb": f.get("thumbnail"),
                        "url": f["url"], "page": v["pageURL"], "license": "Pixabay", "author": v.get("user", ""),
                        "width": f.get("width"), "height": f.get("height"), "duration": v.get("duration")})
    if kind in ("image", "any"):
        r = c.get("https://pixabay.com/api/", params={"key": key, "q": q, "per_page": max(3, limit)})
        for p in r.json().get("hits", []):
            out.append({"source": "pixabay", "kind": "image", "title": p.get("tags", q), "thumb": p["webformatURL"],
                        "url": p.get("largeImageURL"), "page": p["pageURL"], "license": "Pixabay",
                        "author": p.get("user", ""), "width": p.get("imageWidth"), "height": p.get("imageHeight")})
    return out


SOURCES = {"openverse": _openverse, "wikimedia": _commons, "archive": _archive, "nasa": _nasa,
           "imgflip": _imgflip, "pexels": _pexels, "pixabay": _pixabay}
DEFAULT_SOURCES = ["pexels", "pixabay", "openverse", "archive", "nasa"] + (["wikimedia"] if WIKIMEDIA_CONTACT else [])


def _load_cands():
    return json.loads(CAND_FILE.read_text()) if CAND_FILE.exists() else {}


def _save_cands(cands):
    CAND_FILE.parent.mkdir(parents=True, exist_ok=True)
    CAND_FILE.write_text(json.dumps(cands, ensure_ascii=False))


def search(query, kind="any", sources=None, limit=8, allow_fair_use=False):
    """Search all sources; returns (candidates, errors). Candidates are remembered for fetch()."""
    sources = sources or DEFAULT_SOURCES
    found, errors = [], {}
    with _client() as c:
        def run(name):
            try:
                return name, SOURCES[name](c, query, kind, limit)
            except Exception as e:  # one failing source must not sink the search
                return name, e
        with ThreadPoolExecutor(max_workers=len(sources)) as ex:
            for name, res in ex.map(run, sources):
                if isinstance(res, Exception):
                    errors[name] = f"{type(res).__name__}: {str(res)[:120]}"
                else:
                    found += res
    out = []
    for x in found:
        x["class"] = classify(x["license"]) if x["source"] != "imgflip" else "C"
        if x["source"].startswith("openverse") and x["license"].startswith("CC BY"):
            x["class"] = "B"
        if x["class"] == "X" or (x["class"] == "C" and not allow_fair_use and x["source"] != "internet-archive"):
            continue
        if "wikimedia.org" in (x.get("url") or "") and not WIKIMEDIA_CONTACT:
            continue  # would 403 on download
        x["id"] = _cid(x["source"], x.get("url") or x.get("ia_id") or x.get("nasa_id") or x["title"])
        x["query"] = query
        out.append(x)
    cands = _load_cands()
    cands.update({x["id"]: x for x in out})
    _save_cands(cands)
    return out, errors


def contact_sheet(cands, out_path, cols=4, tile=300):
    """Numbered thumbnail grid so the agent can actually look at candidates before choosing."""
    from PIL import Image, ImageDraw, ImageFont
    rows = (len(cands) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * tile, rows * (tile + 34)), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    try:
        f = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 18)
    except OSError:
        f = ImageFont.load_default()

    def get(x):
        try:
            with _client() as c:
                r = c.get(x["thumb"], timeout=20)
                r.raise_for_status()
                return Image.open(BytesIO(r.content)).convert("RGB")
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        thumbs = list(ex.map(get, cands))
    for i, (x, im) in enumerate(zip(cands, thumbs)):
        cx, cy = (i % cols) * tile, (i // cols) * (tile + 34)
        if im:
            im.thumbnail((tile - 8, tile - 8))
            sheet.paste(im, (cx + (tile - im.width) // 2, cy + 4 + (tile - 8 - im.height) // 2))
        label = f"{x['id']} {x['kind'][0].upper()} [{x['class']}]"
        d.text((cx + 6, cy + tile + 6), label, fill=(255, 220, 80), font=f)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, quality=85)
    return str(out_path)


# ---------- download ----------

def _resolve_url(c, x):
    if x.get("url"):
        return x["url"]
    if x["source"] == "internet-archive":
        files = c.get(f"https://archive.org/metadata/{x['ia_id']}").json()["files"]
        vids = [f for f in files if f["name"].lower().endswith(".mp4")]
        vids.sort(key=lambda f: (f.get("format") != "h.264", -int(f.get("size") or 0)))
        if not vids:
            raise RuntimeError("no mp4 file in this Internet Archive item")
        return f"https://archive.org/download/{x['ia_id']}/{vids[0]['name']}"
    if x["source"] == "nasa":
        assets = c.get(f"https://images-api.nasa.gov/asset/{x['nasa_id']}").json()["collection"]["items"]
        hrefs = [a["href"] for a in assets]
        pref = ("~orig.mp4", "~large.mp4", "~medium.mp4") if x["kind"] == "video" else ("~large.jpg", "~orig.jpg")
        for p in pref:
            for h in hrefs:
                if h.endswith(p):
                    return h.replace("http://", "https://")
        return hrefs[0]
    raise RuntimeError("no download url")


def credit_line(x):
    if x["class"] == "A" and x["source"] in ("pexels", "pixabay", "nasa"):
        return f"{x['title']} - {x.get('author') or x['source']} ({x['license']}) {x.get('page') or ''}".strip()
    if x.get("attribution"):
        return x["attribution"]
    who = x.get("author") or "unknown author"
    return f"\"{x['title']}\" by {who}, {x['license']} - {x.get('page') or ''}".strip()


def fetch(ids, folder=None):
    """Download approved candidates into the B-roll library and append credits."""
    cands = _load_cands()
    dest = Path(folder).expanduser() if folder else LIBRARY
    dest.mkdir(parents=True, exist_ok=True)
    results = []
    with _client() as c:
        for cid in ids:
            x = cands.get(cid)
            if not x:
                results.append({"id": cid, "error": "unknown id - run search_broll again"})
                continue
            try:
                url = _resolve_url(c, x)
                ext = Path(url.split("?")[0]).suffix.lower() or (".mp4" if x["kind"] == "video" else ".jpg")
                safe = re.sub(r"[^\w\-]+", "_", x["title"])[:50].strip("_") or cid
                path = dest / f"{cid}_{safe}{ext}"
                if not path.exists():
                    with c.stream("GET", url, timeout=120) as r:
                        r.raise_for_status()
                        size = int(r.headers.get("content-length") or 0)
                        if size > MAX_DOWNLOAD:
                            raise RuntimeError(f"file too large ({size / 1e6:.0f} MB)")
                        tmp = path.with_suffix(path.suffix + ".part")
                        got = 0
                        with open(tmp, "wb") as f:
                            for chunk in r.iter_bytes(1 << 20):
                                got += len(chunk)
                                if got > MAX_DOWNLOAD:
                                    raise RuntimeError("file too large")
                                f.write(chunk)
                        tmp.rename(path)
                    with open(dest / "CREDITS.md", "a", encoding="utf-8") as f:
                        f.write(f"- [{x['class']}] {credit_line(x)}  (file: {path.name})\n")
                x["local_path"] = str(path)
                results.append({"id": cid, "path": str(path), "kind": x["kind"], "class": x["class"],
                                "license": x["license"], "credit": credit_line(x),
                                "size_mb": round(path.stat().st_size / 1e6, 1)})
            except Exception as e:
                results.append({"id": cid, "error": f"{type(e).__name__}: {str(e)[:160]}"})
    _save_cands(cands)
    return {"folder": str(dest), "credits_file": str(dest / "CREDITS.md"), "files": results}


def short_credit(x):
    """On-screen credit, e.g. 'ภาพ: Jane Doe / CC BY 2.0'."""
    who = (x.get("author") or x["source"]).strip()[:40]
    return f"ภาพ: {who} / {x['license']}"


def candidate(cid):
    return _load_cands().get(cid)
