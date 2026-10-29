"""Genere docs/architecture.excalidraw (ouvrir sur excalidraw.com ou dans l'extension VS Code)."""
import json, random

els = []
def seed(): return random.randint(1, 2**31 - 1)

def base(t, x, y, w, h, **kw):
    e = dict(id=f"el{len(els)}", type=t, x=x, y=y, width=w, height=h, angle=0,
             strokeColor="#1e1e1e", backgroundColor="transparent", fillStyle="solid",
             strokeWidth=2, strokeStyle="solid", roughness=1, opacity=100, groupIds=[],
             frameId=None, roundness=None, seed=seed(), version=1, versionNonce=seed(),
             isDeleted=False, boundElements=None, updated=1, link=None, locked=False)
    e.update(kw); els.append(e); return e

def box(x, y, w, h, label, bg="#a5d8ff", stroke="#1971c2"):
    r = base("rectangle", x, y, w, h, backgroundColor=bg, strokeColor=stroke, roundness={"type": 3})
    t = base("text", x + 10, y + h / 2 - 12 * (label.count("\n") + 1), w - 20, 24 * (label.count("\n") + 1),
             text=label, fontSize=18, fontFamily=1, textAlign="center", verticalAlign="middle",
             containerId=r["id"], originalText=label, lineHeight=1.25, autoResize=True)
    r["boundElements"] = [{"type": "text", "id": t["id"]}]
    return r

def arrow(x1, y1, x2, y2, label=None):
    base("arrow", x1, y1, x2 - x1, y2 - y1, points=[[0, 0], [x2 - x1, y2 - y1]], endArrowhead="arrow",
         startArrowhead=None, roundness={"type": 2}, startBinding=None, endBinding=None, lastCommittedPoint=None)
    if label:
        base("text", (x1 + x2) / 2 - 60, (y1 + y2) / 2 - 34, 120, 22, text=label, fontSize=14, fontFamily=1,
             textAlign="center", verticalAlign="top", containerId=None, originalText=label, lineHeight=1.25,
             autoResize=True, strokeColor="#868e96")

base("text", 40, 10, 700, 36, text="pipeline-doctor : architecture", fontSize=28, fontFamily=1,
     textAlign="left", verticalAlign="top", containerId=None, originalText="pipeline-doctor : architecture",
     lineHeight=1.25, autoResize=True)

box(40, 120, 190, 80, "Build CI\n(GitHub Actions)", "#ffc9c9", "#e03131")
box(300, 120, 190, 80, "Collecteur\nlogs + diff", "#a5d8ff", "#1971c2")
box(560, 120, 190, 80, "Sanitizer\nmasque secrets", "#ffec99", "#f08c00")
box(820, 120, 190, 80, "Routeur LLM", "#d0bfff", "#6741d9")
arrow(230, 160, 300, 160); base("text", 236, 118, 60, 22, text="echec", fontSize=14, fontFamily=1, textAlign="center", verticalAlign="top", containerId=None, originalText="echec", lineHeight=1.25, autoResize=True, strokeColor="#e03131"); arrow(490, 160, 560, 160); arrow(750, 160, 820, 160)

box(700, 320, 190, 80, "Ollama local\n(1er passage)", "#b2f2bb", "#2f9e44")
box(940, 320, 190, 80, "DeepSeek / Claude\n(si confiance basse)", "#ffd8a8", "#e8590c")
arrow(890, 200, 795, 320); arrow(950, 200, 1035, 320)

box(820, 500, 190, 80, "Notifier Slack\ncause + preuve + fix", "#99e9f2", "#0c8599")
arrow(795, 400, 880, 500); arrow(1035, 400, 950, 500)
box(560, 500, 190, 80, "Journal des\ndiagnostics", "#e9ecef", "#868e96")
arrow(820, 540, 750, 540)

base("text", 40, 330, 560, 120, text="Principes\n- lecture seule au depart\n- secrets masques avant tout envoi cloud\n- chaque diagnostic est trace",
     fontSize=18, fontFamily=1, textAlign="left", verticalAlign="top", containerId=None,
     originalText="Principes\n- lecture seule au depart\n- secrets masques avant tout envoi cloud\n- chaque diagnostic est trace",
     lineHeight=1.25, autoResize=True)

json.dump({"type": "excalidraw", "version": 2, "source": "hermes", "elements": els,
           "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None}, "files": {}},
          open("docs/architecture.excalidraw", "w", encoding="utf-8"), indent=1)
print(len(els), "elements")
