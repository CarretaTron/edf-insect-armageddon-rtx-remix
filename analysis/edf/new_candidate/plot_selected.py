"""Plot measured mesh, UVs and software-executed VS projection; no raster image."""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Line3DCollection

root = Path(__file__).resolve().parent
report = json.loads((root / "validation.json").read_text())
reference = report["reference"]
vertices = reference["vertices"]
by_index = {v["index"]: v for v in vertices}
indices = reference["indices"]
triangles = [indices[i:i + 3] for i in range(0, len(indices), 3)]
fig = plt.figure(figsize=(15, 5.2), layout="constrained")
fig.suptitle("EDF — draw 952 / frame 755: reconstrução offline", fontsize=15)
ax = fig.add_subplot(131, projection="3d")
segments = []
for triangle in triangles:
    segments.append([by_index[v]["positionLocal"] for v in triangle + triangle[:1]])
ax.add_collection3d(Line3DCollection(segments, colors="#19758a", linewidths=0.85))
lo, hi = reference["decodedBounds"]["minimum"], reference["decodedBounds"]["maximum"]
for setter, a, b in zip((ax.set_xlim, ax.set_ylim, ax.set_zlim), lo, hi):
    setter(a, b)
ax.set_box_aspect([b - a for a, b in zip(lo, hi)])
ax.view_init(elev=24, azim=-36)
ax.set(xlabel="X (ancora fixa)", ylabel="Y (ancora fixa)", zlabel="Z (ancora fixa)", title="252 vértices · 144 triângulos")
uv = fig.add_subplot(132)
for triangle in triangles:
    points = [by_index[v]["uv"] for v in triangle + triangle[:1]]
    uv.plot(*zip(*points), color="#19758a", linewidth=0.65, alpha=0.8)
uv.set(xlim=(0, 1), ylim=(0, 1), xlabel="U", ylabel="V", title="UV = SHORT2.xy / 2048")
uv.set_aspect("equal")
uv.grid(alpha=0.15)
screen = fig.add_subplot(133)
for match, color in zip(report["matchedDraws"], ["#19758a", "#68a381", "#d05d33"]):
    values = {v["index"]: v["screen"] for v in match["vertices"]}
    for i, triangle in enumerate(triangles):
        points = [values[v] for v in triangle + triangle[:1]]
        screen.plot(*zip(*points), color=color, linewidth=0.6, alpha=0.7,
                    label=f"Frame {match['frame']} / draw {match['draw']}" if i == 0 else None)
screen.invert_yaxis()
screen.set_aspect("equal")
screen.set(xlabel="Pixel X", ylabel="Pixel Y", title="Projeção calculada pelo VS (zoom)")
screen.legend(fontsize=8, loc="upper right")
screen.grid(alpha=0.15)
fig.text(0.5, -0.015, "Sem pixels do jogo ou textura. A projeção é calculada; sobreposição com raster original ainda pendente.",
         ha="center", fontsize=9)
fig.savefig(root / "geometry_uv_projection.png", dpi=150, bbox_inches="tight")
fig.savefig(root / "geometry_uv_projection.svg", bbox_inches="tight")
