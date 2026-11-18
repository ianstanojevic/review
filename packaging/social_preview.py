"""GitHub social preview (the image Discord/Twitter show for the repo link),
in DRAFT's style: navy background, thin gold frame, the REVIEW wordmark and
tagline on the left, the app bleeding off the right edge.

    python packaging/social_preview.py SCREENSHOT.png [OUT.png]

SCREENSHOT is a full-window capture (e.g. from `python -m review.ui
--snapshot`). Writes docs/github/social-preview.png (1280x640) by default.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
W, H, SS = 1280, 640, 2                      # drawn at 2x, then downscaled
NAVY = (1, 10, 19)
PANEL = (9, 16, 32)
GOLD = (200, 170, 110)
GOLD_DIM = (120, 90, 40)
FG = (240, 230, 210)
DIM = (160, 155, 140)
TEAL = (10, 200, 185)
FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


def font(name, size):
    return ImageFont.truetype(str(FONTS / name), size * SS)


def spaced(d, xy, text, f, fill, spacing):
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + spacing
    return x


def main(shot: str, out: str | None = None) -> Path:
    out = Path(out or ROOT / "docs" / "github" / "social-preview.png")
    im = Image.new("RGB", (W * SS, H * SS), NAVY)
    # soft vignette glow behind the app
    glow = Image.new("L", im.size, 0)
    ImageDraw.Draw(glow).ellipse([int(W * 0.45 * SS), int(-H * 0.2 * SS),
                                  int(W * 1.4 * SS), int(H * 1.2 * SS)],
                                 fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(120 * SS))
    im.paste(Image.new("RGB", im.size, (20, 40, 60)), (0, 0), glow)

    # the app, right side, bleeding off the edge, with a gold hairline
    app = Image.open(shot).convert("RGB")
    app = app.crop((0, 48, app.width, app.height))      # drop the title bar
    ah = int(560 * SS)
    aw = int(app.width * ah / app.height)
    app = app.resize((aw, ah), Image.LANCZOS)
    ax, ay = int(560 * SS), int(40 * SS)
    aw = min(aw, (W - 23) * SS - ax)              # clipped by the frame
    app = app.crop((0, 0, aw, ah))
    shadow = Image.new("L", im.size, 0)
    ImageDraw.Draw(shadow).rectangle([ax - 6 * SS, ay - 6 * SS,
                                      ax + aw + 6 * SS, ay + ah + 6 * SS],
                                     fill=180)
    shadow = shadow.filter(ImageFilter.GaussianBlur(14 * SS))
    im.paste(Image.new("RGB", im.size, (0, 0, 0)), (0, 0), shadow)
    im.paste(app, (ax, ay))
    d = ImageDraw.Draw(im)
    d.rectangle([ax - SS, ay - SS, ax + aw, ay + ah], outline=GOLD_DIM,
                width=SS)

    # text block on the left
    x0 = 64 * SS
    title = font("georgiab.ttf", 74)
    end = spaced(d, (x0, 205 * SS), "REVIEW", title, GOLD, 9 * SS)
    ry = 312 * SS
    d.line([(x0, ry), (min(end, x0 + 430 * SS), ry)], fill=GOLD_DIM,
           width=2 * SS)
    d.line([(x0, ry), (x0 + 90 * SS, ry)], fill=GOLD, width=3 * SS)
    r = 6 * SS
    d.polygon([(x0, ry - r), (x0 + r, ry), (x0, ry + r), (x0 - r, ry)],
              fill=GOLD)
    body = font("segoeui.ttf", 30)
    for i, line in enumerate(("Ranked account analyzer",
                              "for League of Legends")):
        d.text((x0, (340 + i * 40) * SS), line, font=body, fill=FG)
    small = font("segoeuisb.ttf" if (FONTS / "segoeuisb.ttf").exists()
                 else "segoeuib.ttf", 21)
    d.text((x0, 446 * SS), "Compare yourself with Gold to Master+",
           font=small, fill=TEAL)
    d.text((x0, 478 * SS), "Free  ·  Windows  ·  no install",
           font=font("segoeui.ttf", 20), fill=DIM)

    # left fade so text never sits on the screenshot; thin gold frame
    fade = Image.new("L", im.size, 0)
    fd = ImageDraw.Draw(fade)
    for i in range(80 * SS):
        fd.line([(ax - 80 * SS + i, 0), (ax - 80 * SS + i, H * SS)],
                fill=max(0, 255 - int(255 * i / (80 * SS))))
    fd.rectangle([0, 0, ax - 80 * SS, H * SS], fill=255)
    base = Image.new("RGB", im.size, NAVY)
    left = im.crop((0, 0, ax, H * SS))
    im.paste(base, (0, 0), fade)
    im.paste(left, (0, 0), fade.crop((0, 0, ax, H * SS)).point(
        lambda v: 255 if v else 0))
    d = ImageDraw.Draw(im)
    m = 22 * SS
    d.rectangle([m, m, W * SS - m, H * SS - m], outline=GOLD_DIM, width=SS)
    k = 16 * SS                                  # DRAFT's notched corners
    d.line([(m, m + k), (m + k, m)], fill=GOLD, width=2 * SS)
    d.line([(W * SS - m - k, H * SS - m), (W * SS - m, H * SS - m - k)],
           fill=GOLD, width=2 * SS)

    im = im.resize((W, H), Image.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, optimize=True)
    print("wrote", out, "%d KB" % (out.stat().st_size // 1024))
    return out


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
