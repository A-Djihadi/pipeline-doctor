"""Rend docs/architecture.excalidraw en PNG (Pillow). Usage : python docs/render_png.py

Gere rectangles (pleins ou pointilles), fleches (couleur, pointilles) et textes ; la premiere
ligne d'un texte contenu dans une boite est legerement plus grande (titre de la boite).
"""
import json
import math

from PIL import Image, ImageDraw, ImageFont

S, PAD = 2, 30
data = json.load(open("docs/architecture.excalidraw", encoding="utf-8"))
els = data["elements"]
byid = {e["id"]: e for e in els}
W = int(max(e["x"] + max(e["width"], 0) for e in els) + PAD * 2)
H = int(max(e["y"] + max(e["height"], 0) for e in els) + PAD * 2)
img = Image.new("RGB", (W * S, H * S), "white")
d = ImageDraw.Draw(img)
LW = 3 * S // 2 + 1
font_cache = {}


def font(size):
    k = int(size * S)
    if k not in font_cache:
        font_cache[k] = ImageFont.truetype("C:/Windows/Fonts/comic.ttf", k)
    return font_cache[k]


def P(x, y):
    return ((x + PAD) * S, (y + PAD) * S)


def dashed(p1, p2, fill, dash=12 * S, gap=8 * S):
    (x1, y1), (x2, y2) = p1, p2
    length = math.hypot(x2 - x1, y2 - y1)
    if length == 0:
        return
    ux, uy = (x2 - x1) / length, (y2 - y1) / length
    t = 0
    while t < length:
        e = min(t + dash, length)
        d.line([x1 + ux * t, y1 + uy * t, x1 + ux * e, y1 + uy * e], fill=fill, width=LW)
        t += dash + gap


for e in els:
    if e["type"] != "rectangle":
        continue
    x, y = P(e["x"], e["y"])
    x2, y2 = x + e["width"] * S, y + e["height"] * S
    fill = None if e["backgroundColor"] == "transparent" else e["backgroundColor"]
    if e.get("strokeStyle") == "dashed":
        if fill:
            d.rounded_rectangle([x, y, x2, y2], radius=16 * S, fill=fill)
        for a, b in [((x, y), (x2, y)), ((x2, y), (x2, y2)), ((x2, y2), (x, y2)), ((x, y2), (x, y))]:
            dashed(a, b, e["strokeColor"])
    else:
        d.rounded_rectangle([x, y, x2, y2], radius=16 * S, fill=fill, outline=e["strokeColor"], width=LW)

for e in els:
    if e["type"] != "arrow":
        continue
    px, py = e["points"][-1]
    x1, y1 = P(e["x"], e["y"])
    x2, y2 = P(e["x"] + px, e["y"] + py)
    c = e["strokeColor"]
    if e.get("strokeStyle") == "dashed":
        dashed((x1, y1), (x2, y2), c)
    else:
        d.line([x1, y1, x2, y2], fill=c, width=LW)
    a, L = math.atan2(y2 - y1, x2 - x1), 14 * S
    d.polygon([(x2, y2), (x2 - L * math.cos(a - 0.4), y2 - L * math.sin(a - 0.4)),
               (x2 - L * math.cos(a + 0.4), y2 - L * math.sin(a + 0.4))], fill=c)

for e in els:
    if e["type"] != "text":
        continue
    lines = e["text"].split("\n")
    lh = e["fontSize"] * 1.25 * S * 0.85
    color = e.get("strokeColor", "#1e1e1e")
    cont = byid.get(e.get("containerId")) if e.get("containerId") else None
    if cont:
        cx, cy = P(cont["x"] + cont["width"] / 2, cont["y"] + cont["height"] / 2)
        y0 = cy - lh * len(lines) / 2
        for i, ln in enumerate(lines):
            f = font(e["fontSize"] * (0.95 if i == 0 else 0.85))
            d.text((cx - d.textlength(ln, font=f) / 2, y0 + i * lh), ln, font=f, fill=color)
    else:
        x, y = P(e["x"], e["y"])
        f = font(e["fontSize"] * 0.85)
        for i, ln in enumerate(lines):
            d.text((x, y + i * lh), ln, font=f, fill=color)

img.save("docs/architecture.png")
print(img.size)
