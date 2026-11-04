"""Genere docs/architecture.excalidraw (ouvrir sur excalidraw.com ou dans l'extension VS Code).

Usage : python docs/make_diagram.py && python docs/render_png.py
Architecture actuelle : LangGraph + Ollama local + Langfuse auto-heberge, sans escalade cloud
(ADR-0004, ADR-0005), et les cas d'utilisation.
"""
import json
import random

els = []


def seed():
    return random.randint(1, 2**31 - 1)


def base(t, x, y, w, h, **kw):
    e = dict(id=f"el{len(els)}", type=t, x=x, y=y, width=w, height=h, angle=0,
             strokeColor="#1e1e1e", backgroundColor="transparent", fillStyle="solid",
             strokeWidth=2, strokeStyle="solid", roughness=1, opacity=100, groupIds=[],
             frameId=None, roundness=None, seed=seed(), version=1, versionNonce=seed(),
             isDeleted=False, boundElements=None, updated=1, link=None, locked=False)
    e.update(kw)
    els.append(e)
    return e


def text(x, y, s, size=18, color="#1e1e1e"):
    n = s.count("\n") + 1
    return base("text", x, y, 20 + max(len(ln) for ln in s.split("\n")) * size * 0.55, n * size * 1.25,
                text=s, fontSize=size, fontFamily=1, textAlign="left", verticalAlign="top",
                containerId=None, originalText=s, lineHeight=1.25, autoResize=True, strokeColor=color)


def box(x, y, w, h, label, bg, stroke, size=18, dashed=False):
    r = base("rectangle", x, y, w, h, backgroundColor=bg, strokeColor=stroke, roundness={"type": 3},
             strokeStyle="dashed" if dashed else "solid")
    n = label.count("\n") + 1
    t = base("text", x + 10, y + h / 2 - size * 1.25 * n / 2, w - 20, size * 1.25 * n, text=label,
             fontSize=size, fontFamily=1, textAlign="center", verticalAlign="middle", containerId=r["id"],
             originalText=label, lineHeight=1.25, autoResize=True, strokeColor=stroke)
    r["boundElements"] = [{"type": "text", "id": t["id"]}]
    return r


def frame(x, y, w, h, label, stroke, bg="transparent"):
    base("rectangle", x, y, w, h, strokeColor=stroke, backgroundColor=bg, strokeStyle="dashed",
         roundness={"type": 3})
    text(x + 16, y + 10, label, 18, stroke)


def arrow(x1, y1, x2, y2, label=None, dashed=False, color="#1e1e1e", lx=0, ly=0):
    base("arrow", x1, y1, x2 - x1, y2 - y1, points=[[0, 0], [x2 - x1, y2 - y1]], endArrowhead="arrow",
         startArrowhead=None, roundness={"type": 2}, startBinding=None, endBinding=None,
         lastCommittedPoint=None, strokeStyle="dashed" if dashed else "solid", strokeColor=color)
    if label:
        text((x1 + x2) / 2 + lx, (y1 + y2) / 2 + ly, label, 14, "#495057")


text(20, 0, "pipeline-doctor : comment un log CI en echec devient un diagnostic", 28)
text(20, 40, "LangGraph orchestre les etapes, Ollama raisonne, Langfuse observe et mesure. Tout tourne en local.",
     16, "#495057")

box(20, 190, 230, 100, "Build CI en echec\n(GitHub Actions)", "#ffc9c9", "#c92a2a")
box(1300, 455, 240, 100, "Slack / terminal\ncause, preuves,\ncorrectif, confiance", "#99e9f2", "#0b7285", 16)

frame(290, 80, 960, 820, "Machine locale : aucun log ne sort (ADR-0004)", "#495057", "#f8f9fa")
frame(320, 120, 900, 470, "LangGraph : graphe d'etats (graph.py)", "#5f3dc4", "#f3f0ff")

box(350, 200, 180, 75, "sanitize\nmasque les secrets", "#ebfbee", "#2b8a3e", 16)
box(570, 200, 180, 75, "classify\nregles : maven, oom...", "#ebfbee", "#2b8a3e", 16)
box(790, 200, 180, 75, "diagnose\nappel au modele", "#e7f5ff", "#1864ab", 16)
box(1010, 200, 180, 75, "focus_context\nzoom sur les erreurs", "#e7f5ff", "#1864ab", 16)
box(570, 360, 180, 75, "fallback_rules\ndiagnostic par regles", "#ebfbee", "#2b8a3e", 16)
box(790, 480, 180, 75, "finalize\ncalcule les scores", "#ebfbee", "#2b8a3e", 16)
box(1010, 360, 180, 75, "human_review\npause : o / N ?", "#fff4e6", "#d9480f", 16, dashed=True)

arrow(250, 240, 350, 237, "log du job", lx=-40, ly=-30)
arrow(530, 237, 570, 237)
arrow(750, 237, 790, 237)
arrow(970, 225, 1010, 225, "doute", lx=-22, ly=-38)
arrow(1010, 255, 970, 255, "re-essai", lx=-30, ly=14)
arrow(840, 275, 700, 360, "Ollama KO", lx=-95, ly=-10)
arrow(750, 410, 820, 480)
arrow(880, 275, 880, 480, "fiable", lx=8, ly=-10)
arrow(970, 500, 1010, 420, "--review", lx=-80, ly=-8)
arrow(1190, 397, 1300, 490)
arrow(880, 555, 880, 590)
text(890, 562, "fin", 14, "#495057")

box(350, 640, 500, 230,
    "Langfuse (auto-heberge, 6 conteneurs Docker)\n\n"
    "Traces : 1 span par noeud du graphe\n"
    "Generations : prompt, tokens, latence\n"
    "Scores : confidence, evidence_grounded\n"
    "Prompts versionnes  |  Datasets  |  Experiences",
    "#f3f0ff", "#5f3dc4", 17)
box(940, 640, 260, 90, "Ollama local\nqwen2.5-coder:3b", "#d3f9d8", "#2b8a3e")
box(940, 780, 260, 90, "evals.py\nlogs + expected.json", "#fff9db", "#e67700", 16)

arrow(600, 590, 600, 640, "traces OTel, secrets masques", dashed=True, color="#5f3dc4", lx=10, ly=-12)
arrow(990, 275, 990, 640, "HTTP localhost", dashed=True, color="#2b8a3e", lx=8, ly=100)
arrow(940, 825, 850, 800, "experience", dashed=True, color="#e67700", lx=-30, ly=10)

text(20, 930, "Cas d'utilisation", 24)
for i, (who, need, where) in enumerate([
    ("Developpeur", "Ma CI est rouge : pourquoi ?", "message Slack avec cause,\nlignes de preuve, correctif"),
    ("Equipe plateforme", "Le modele invente-t-il ?", "trace Langfuse + score\nevidence_grounded"),
    ("Mainteneur IA", "Ce nouveau prompt est-il meilleur ?", "prompt versionne + experience\nsur le dataset de reference"),
    ("Responsable", "Je valide avant toute action", "human_review : le graphe\nse met en pause"),
]):
    box(20 + i * 385, 980, 365, 140, f"{who}\n{need}\n\n-> {where}", "#ffffff", "#343a40", 16)

json.dump({"type": "excalidraw", "version": 2, "source": "pipeline-doctor", "elements": els,
           "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None}, "files": {}},
          open("docs/architecture.excalidraw", "w", encoding="utf-8"), indent=1)
print(len(els), "elements")
