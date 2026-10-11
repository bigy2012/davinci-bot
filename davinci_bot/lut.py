"""Build a colour-only "natural skin" 3D LUT (.cube) for CapCut / Resolve / Premiere.

A LUT can only remap colours, so this does the "clear skin" part of the natural-skin look:
skin-coloured pixels get a little brighter, less red and slightly peachier. Smoothing (spatial)
and the face window (positional) only exist in the Resolve .drx preset.

The skin mask lives in BT.709 YCbCr: hue angle of (Cb, Cr) + chroma + luma, with soft edges.
Measured on the user's footage: skin hue 113-120 deg, beige wall 134-151, curtain 136, lamp 172.
"""

import numpy as np

KR, KB = 0.2126, 0.0722


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def to_ycc(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = KR * r + (1 - KR - KB) * g + KB * b
    return y, (b - y) / (2 * (1 - KB)), (r - y) / (2 * (1 - KR))


def from_ycc(y, cb, cr):
    r = y + 2 * (1 - KR) * cr
    b = y + 2 * (1 - KB) * cb
    g = (y - KR * r - KB * b) / (1 - KR - KB)
    return np.stack([r, g, b], axis=-1)


def skin_mask(rgb, hue_center=116.5, hue_inner=7.0, hue_outer=15.0):
    y, cb, cr = to_ycc(rgb)
    hue = np.degrees(np.arctan2(cr, cb)) % 360
    chroma = np.hypot(cb, cr)
    dh = np.abs((hue - hue_center + 180) % 360 - 180)
    m_hue = 1 - smoothstep(hue_inner, hue_outer, dh)
    m_chroma = smoothstep(0.015, 0.045, chroma) * (1 - smoothstep(0.20, 0.28, chroma))
    m_luma = smoothstep(0.10, 0.20, y) * (1 - smoothstep(0.85, 0.95, y))
    return m_hue * m_chroma * m_luma


def natural_skin(rgb, strength=1.0, lift=0.10, desat=0.08, warm_shift=3.0):
    """rgb in [0,1] (Rec.709 display). Returns graded rgb."""
    m = skin_mask(rgb) * strength
    # brighter: per-channel gamma < 1 on skin (≈ +8/255 at mid-grey)
    out = np.power(np.clip(rgb, 0, 1), 1 - lift * m[..., None])
    y, cb, cr = to_ycc(out)
    chroma = np.hypot(cb, cr)
    hue = np.arctan2(cr, cb) + np.radians(warm_shift) * m  # away from red, toward peach
    chroma = chroma * (1 - desat * m)
    return np.clip(from_ycc(y, chroma * np.cos(hue), chroma * np.sin(hue)), 0, 1)


def write_cube(path, size=33, title="Natural Skin (davinci-bot)", **kw):
    grid = np.linspace(0, 1, size)
    # .cube order: red changes fastest, then green, then blue
    b, g, r = np.meshgrid(grid, grid, grid, indexing="ij")
    rgb = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    out = natural_skin(rgb, **kw)
    with open(path, "w") as f:
        f.write(f'TITLE "{title}"\n')
        f.write(f"LUT_3D_SIZE {size}\n")
        f.write("DOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1.0 1.0 1.0\n")
        for v in out:
            f.write(f"{v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
    return path
