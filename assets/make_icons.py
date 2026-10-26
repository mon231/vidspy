"""Generate all vidspy icons from one geometry (108x108 adaptive-icon grid).

    pip install pillow && python assets/make_icons.py

Writes: Android platform logos (from backend/platforms.py), assets/icon.svg, icon.png, icon.ico, desktop/web/favicon.png,
Android adaptive vectors + legacy mipmap PNGs.
"""
import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "backend"))
from vidspy_backend import platforms  # noqa: E402
BG, EYE, IRIS_C, PLAY = "#3B2A78", "#FFFFFF", "#26C6DA", "#FFFFFF"

# 108x108 grid; artwork stays inside the 66dp adaptive-icon safe zone (x/y 21..87)
EYE_PATH = "M24,54 Q54,22 84,54 Q54,86 24,54 Z"
IRIS = (54, 54, 14)                       # cx, cy, r
TRI = [(49, 46), (49, 62), (62, 54)]


def _bezier(p0, p1, p2, n=48):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1])
            for t in (i / n for i in range(n + 1))]


EYE_POLY = _bezier((24, 54), (54, 22), (84, 54)) + _bezier((84, 54), (54, 86), (24, 54))


def render(size, shape="square", scale=1.0):
    """shape: square (rounded), circle, full (no mask)."""
    ss = 4
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if shape == "circle":
        d.ellipse([0, 0, S - 1, S - 1], fill=BG)
    elif shape == "square":
        d.rounded_rectangle([0, 0, S - 1, S - 1], radius=S * 0.22, fill=BG)
    else:
        d.rectangle([0, 0, S, S], fill=BG)
    k = S / 108 * scale
    off = S / 2 - 54 * k

    def pt(p):
        return (p[0] * k + off, p[1] * k + off)

    d.polygon([pt(p) for p in EYE_POLY], fill=EYE)
    cx, cy, r = IRIS
    d.ellipse([pt((cx - r, cy - r)), pt((cx + r, cy + r))], fill=IRIS_C)
    d.polygon([pt(p) for p in TRI], fill=PLAY)
    return img.resize((size, size), Image.LANCZOS)



def write(path, data, mode="w"):
    path = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8", "newline": "\n"})) as f:
        f.write(data)


def vector(layers):
    return ('<?xml version="1.0" encoding="utf-8"?>\n'
            '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
            '    android:width="108dp"\n    android:height="108dp"\n'
            '    android:viewportWidth="108"\n    android:viewportHeight="108">\n'
            + "".join(f'    <path\n        android:fillColor="{c}"\n        android:pathData="{p}" />\n'
                      for c, p in layers) + '</vector>\n')


def circle_path(cx, cy, r):
    return f"M{cx - r},{cy} a{r},{r} 0 1,0 {2 * r},0 a{r},{r} 0 1,0 {-2 * r},0 Z"


def main():
    tri = "M{},{} L{},{} L{},{} Z".format(*[v for p in TRI for v in p])
    cx, cy, r = IRIS
    # Android adaptive icon
    write("app/src/main/res/drawable/ic_launcher_background.xml", vector([(BG, "M0,0h108v108h-108z")]))
    write("app/src/main/res/drawable-v24/ic_launcher_foreground.xml",
          vector([(EYE, EYE_PATH), (IRIS_C, circle_path(cx, cy, r)), (PLAY, tri)]))
    # SVG master
    write("assets/icon.svg",
          f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108">\n'
          f'  <rect width="108" height="108" rx="24" fill="{BG}"/>\n'
          f'  <path d="{EYE_PATH}" fill="{EYE}"/>\n'
          f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="{IRIS_C}"/>\n'
          f'  <path d="{tri}" fill="{PLAY}"/>\n</svg>\n')
    # Platform logos for the Android Accounts screen (generated from backend/platforms.py)
    for name, info in platforms.PLATFORMS.items():
        write(f"app/src/main/res/drawable/ic_platform_{name}.xml",
              '<?xml version="1.0" encoding="utf-8"?>\n'
              '<vector xmlns:android="http://schemas.android.com/apk/res/android"\n'
              '    android:width="24dp"\n    android:height="24dp"\n'
              '    android:viewportWidth="24"\n    android:viewportHeight="24">\n'
              f'    <path\n        android:fillColor="#FFFFFF"\n        android:pathData="{info["icon"]}" />\n'
              '</vector>\n')
    # Legacy launcher PNGs (no safe zone -> draw artwork larger)
    for name, px in {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}.items():
        render(px, "square", 1.25).save(os.path.join(ROOT, f"app/src/main/res/mipmap-{name}/ic_launcher.png"))
        render(px, "circle", 1.25).save(os.path.join(ROOT, f"app/src/main/res/mipmap-{name}/ic_launcher_round.png"))
    # Desktop / web
    big = render(1024, "square", 1.25)
    big.save(os.path.join(ROOT, "assets/icon.png"))
    render(256, "square", 1.25).save(os.path.join(ROOT, "desktop/web/favicon.png"))
    big.save(os.path.join(ROOT, "assets/icon.ico"), sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("icons written")


if __name__ == "__main__":
    main()
