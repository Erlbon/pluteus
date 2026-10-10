"""One-off tool: turn the project artwork (an antique leather book photographed on chroma-key green)
into the web icons, with a gold-foil "P" stamped on the cover.

Needs Pillow (a development tool only, not a runtime dependency of pluteus):
    python tools/make_icon.py ARTWORK.jpg pluteus/static/icon           # write the icons
    python tools/make_icon.py ARTWORK.jpg pluteus/static/icon --debug   # also write debug.png (cover outline)

Steps: key out the green (soft matte + despill), crop square, warp a flat gold "P" onto the angled
cover (perspective), shade it with the leather's own light so it looks stamped, then export the sizes.
"""
import argparse
import os
import sys

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# The cover's four corners in the ORIGINAL 2752x1536 artwork (x, y), found by looking at the picture.
# Order: the corners of the flat letter plane, top-left, top-right, bottom-right, bottom-left, as they
# land on the cover. Adjust with --debug if the artwork changes.
COVER_QUAD = [(1383, 351), (2126, 509), (1431, 915), (647, 674)]
LETTER_BOX = (0.16, 0.02, 0.84, 0.98)       # where the P sits inside the cover (fractions of the plane)
FONTS = ["georgiab.ttf", "timesbd.ttf", "cambriab.ttf"]
SIZES = {"icon-512.png": 512, "icon-192.png": 192, "favicon-32.png": 32, "favicon-16.png": 16}


def key_out_green(img: Image.Image) -> Image.Image:
    """RGBA copy with the green background made transparent (soft edge, green spill removed)."""
    r, g, b = img.split()
    greenness = ImageChops.subtract(g, ImageChops.lighter(r, b))       # how much greener than red/blue
    lo, hi = 30, 110
    alpha = greenness.point(lambda v: 255 if v <= lo else 0 if v >= hi else int(255 * (hi - v) / (hi - lo)))
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    g2 = ImageChops.darker(g, ImageChops.lighter(r, b))                  # despill: green never above red/blue
    out = Image.merge("RGB", (r, g2, b)).convert("RGBA")
    out.putalpha(alpha)
    return out


def solve(a, b):
    """Solve the 8x8 system a.x = b by Gaussian elimination (no numpy needed)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                m[r] = [x - f * y for x, y in zip(m[r], m[col])]
    return [m[i][n] / m[i][i] for i in range(n)]


def perspective_coeffs(dest_quad, src_size):
    """Coefficients for Image.transform(PERSPECTIVE): maps each destination point back to the flat source."""
    w, h = src_size
    src = [(0, 0), (w, 0), (w, h), (0, h)]
    rows, rhs = [], []
    for (x, y), (u, v) in zip(dest_quad, src):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); rhs.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y]); rhs.append(v)
    return solve(rows, rhs)


def find_font(size):
    for name in FONTS:
        for folder in (os.environ.get("WINDIR", "C:/Windows") + "/Fonts", "/usr/share/fonts/truetype/dejavu"):
            path = os.path.join(folder, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def foil_letter(plane=(800, 1000)):
    """Flat RGBA plane holding a gold P, plus its mask and a soft shadow mask (all plane-sized)."""
    w, h = plane
    mask = Image.new("L", plane, 0)
    d = ImageDraw.Draw(mask)
    font = find_font(int(h * 0.92))
    box = d.textbbox((0, 0), "P", font=font)
    d.text(((w - (box[2] - box[0])) / 2 - box[0], (h - (box[3] - box[1])) / 2 - box[1]), "P", fill=255, font=font)
    mask = mask.filter(ImageFilter.GaussianBlur(1.2))
    gold = Image.new("RGB", plane)
    gd = ImageDraw.Draw(gold)
    stops = [(0.0, (255, 232, 160)), (0.45, (236, 182, 74)), (1.0, (176, 112, 30))]
    for y in range(h):
        t = y / (h - 1)
        for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
            if t0 <= t <= t1:
                k = (t - t0) / (t1 - t0)
                gd.line([(0, y), (w, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(c0, c1)))
    shadow = ImageChops.offset(mask, 10, 14).filter(ImageFilter.GaussianBlur(9))
    rim = ImageChops.subtract(ImageChops.offset(mask, -4, -4), mask).filter(ImageFilter.GaussianBlur(1.5))
    gold = Image.composite(Image.new("RGB", plane, (255, 244, 200)), gold, rim.point(lambda v: min(255, v * 2)))
    return gold, mask, shadow


def stamp(book: Image.Image, quad) -> Image.Image:
    """Warp the foil P onto the cover quad and blend it over the book using the leather's light."""
    plane = (800, 1000)
    gold, mask, shadow = foil_letter(plane)
    w, h = plane
    x0, y0, x1, y1 = LETTER_BOX
    # shrink the flat plane to the letter box, expressed on the cover quad by bilinear interpolation
    def point(u, v):
        top = ((1 - u) * quad[0][0] + u * quad[1][0], (1 - u) * quad[0][1] + u * quad[1][1])
        bot = ((1 - u) * quad[3][0] + u * quad[2][0], (1 - u) * quad[3][1] + u * quad[2][1])
        return ((1 - v) * top[0] + v * bot[0], (1 - v) * top[1] + v * bot[1])
    dest = [point(x0, y0), point(x1, y0), point(x1, y1), point(x0, y1)]
    coeffs = perspective_coeffs(dest, plane)
    size = book.size
    warp = lambda im, mode: im.transform(size, Image.PERSPECTIVE, coeffs, Image.BICUBIC)
    gold_w, mask_w, shadow_w = warp(gold, "RGB"), warp(mask, "L"), warp(shadow, "L")
    leather = book.convert("L").filter(ImageFilter.GaussianBlur(2))
    light = leather.point(lambda v: int(185 + v * 0.38))                   # 0.72 .. 1.0 of full brightness
    lit = ImageChops.multiply(gold_w, Image.merge("RGB", (light, light, light)))
    out = book.copy()
    dark = Image.new("RGBA", size, (20, 10, 0, 255))
    out = Image.alpha_composite(out, Image.composite(dark, Image.new("RGBA", size, (0, 0, 0, 0)),
                                                     shadow_w.point(lambda v: int(v * 0.62))))
    foil = lit.convert("RGBA")
    foil.putalpha(mask_w)
    return Image.alpha_composite(out, foil), dest


def square_crop(img: Image.Image, margin=0.08):
    bbox = img.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox()
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    side = max(bbox[2] - bbox[0], bbox[3] - bbox[1]) * (1 + 2 * margin)
    box = (int(cx - side / 2), int(cy - side / 2), int(cx + side / 2), int(cy + side / 2))
    canvas = Image.new("RGBA", (int(side), int(side)), (0, 0, 0, 0))
    canvas.paste(img.crop(box), (0, 0))
    return canvas


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("artwork")
    ap.add_argument("out_dir")
    ap.add_argument("--debug", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    book = key_out_green(Image.open(a.artwork).convert("RGB"))
    stamped, dest = stamp(book, COVER_QUAD)
    if a.debug:
        dbg = book.copy()
        d = ImageDraw.Draw(dbg)
        d.polygon(COVER_QUAD, outline=(255, 0, 255, 255), width=6)
        d.polygon(dest, outline=(0, 255, 255, 255), width=6)
        bg = Image.new("RGBA", dbg.size, (30, 30, 40, 255))
        bg.alpha_composite(dbg)
        bg.convert("RGB").resize((dbg.width // 2, dbg.height // 2)).save(os.path.join(a.out_dir, "debug.png"))
    master = square_crop(stamped).resize((1024, 1024), Image.LANCZOS)
    for name, px in SIZES.items():
        master.resize((px, px), Image.LANCZOS).save(os.path.join(a.out_dir, name), optimize=True)
    # iOS ignores transparency: a solid dark tile with the book at 82%
    tile = Image.new("RGBA", (180, 180), (20, 23, 31, 255))
    small = master.resize((148, 148), Image.LANCZOS)
    tile.alpha_composite(small, (16, 16))
    tile.convert("RGB").save(os.path.join(a.out_dir, "apple-touch-icon.png"), optimize=True)
    master.resize((48, 48), Image.LANCZOS).save(os.path.join(a.out_dir, "favicon.ico"),
                                                sizes=[(16, 16), (32, 32), (48, 48)])
    print("wrote icons to", a.out_dir)


if __name__ == "__main__":
    sys.exit(main())
