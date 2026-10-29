"""Rend docs/architecture.excalidraw en PNG (Pillow). Usage : python docs/render_png.py"""
import json, math
from PIL import Image, ImageDraw, ImageFont

S = 2; PAD = 40
data = json.load(open("docs/architecture.excalidraw", encoding="utf-8"))
els = data["elements"]
byid = {e["id"]: e for e in els}
W = int(max(e["x"] + max(e["width"], 0) for e in els) + PAD * 2)
H = int(max(e["y"] + max(e["height"], 0) for e in els) + PAD * 2)
img = Image.new("RGB", (W * S, H * S), "white")
d = ImageDraw.Draw(img)
font_cache = {}
def font(size):
    k = int(size * S)
    if k not in font_cache:
        font_cache[k] = ImageFont.truetype("C:/Windows/Fonts/comic.ttf", k)
    return font_cache[k]
def P(x, y): return ((x + PAD) * S, (y + PAD) * S)

for e in els:
    if e["type"] == "rectangle":
        x, y = P(e["x"], e["y"])
        d.rounded_rectangle([x, y, x + e["width"] * S, y + e["height"] * S], radius=16 * S,
                            fill=e["backgroundColor"], outline=e["strokeColor"], width=3 * S // 2 + 1)
for e in els:
    if e["type"] == "arrow":
        (px, py) = e["points"][-1]
        x1, y1 = P(e["x"], e["y"]); x2, y2 = P(e["x"] + px, e["y"] + py)
        d.line([x1, y1, x2, y2], fill="#1e1e1e", width=3 * S // 2 + 1)
        a = math.atan2(y2 - y1, x2 - x1); L = 16 * S
        for s in (-0.45, 0.45):
            d.line([x2, y2, x2 - L * math.cos(a + s), y2 - L * math.sin(a + s)], fill="#1e1e1e", width=3 * S // 2 + 1)
    elif e["type"] == "text":
        f = font(e["fontSize"] * 0.85)
        lines = e["text"].split("\n")
        lh = e["fontSize"] * 1.25 * S * 0.85
        cont = byid.get(e.get("containerId")) if e.get("containerId") else None
        if cont:
            cx, cy = P(cont["x"] + cont["width"] / 2, cont["y"] + cont["height"] / 2)
            y0 = cy - lh * len(lines) / 2
            for i, ln in enumerate(lines):
                w = d.textlength(ln, font=f)
                d.text((cx - w / 2, y0 + i * lh), ln, font=f, fill=e.get("strokeColor", "#1e1e1e"))
        else:
            x, y = P(e["x"], e["y"])
            for i, ln in enumerate(lines):
                d.text((x, y + i * lh), ln, font=f, fill=e.get("strokeColor", "#1e1e1e"))
img.save("docs/architecture.png")
print(img.size)
