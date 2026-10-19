# -*- coding: utf-8 -*-
"""Проверка логики GUI без ручного просмотра."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gui
from wow_damage_app import CombatLogParser

LOG = sys.argv[1] if len(sys.argv) > 1 else r"tools\sample_logs\WoWCombatLog_sample.txt"

res = CombatLogParser().parse(LOG)
app = gui.DamageApp(default_log=LOG)
app.result = res
app._refresh_runs()
app.update()

rows = app.tree_runs.get_children()
print("RUN ROWS:", len(rows))
for iid in rows:
    print("  ", app.tree_runs.item(iid, "values"))

print("GROUP ROWS:", len(app.group_table.rows))
for i in range(min(3, len(app.group_table.rows))):
    print("  ", app.group_table.row_values(i))

print("STATS ROWS:", len(app.tree_stats.get_children()))
for iid in app.tree_stats.get_children()[:2]:
    print("  ", app.tree_stats.item(iid, "values"))

print("ABILITY ROWS:", len(app.tree_ab.get_children()))
for iid in app.tree_ab.get_children()[:3]:
    print("  ", app.tree_ab.item(iid, "values"))

# ---- проверка полос: у лучшего игрока заполнение 100% ----
app.update()
canvas = app.group_table.canvas
tracks = [canvas.coords(i) for i in canvas.find_withtag("track")]
fills = [canvas.coords(i) for i in canvas.find_withtag("bar")]
assert len(tracks) == len(app.group_table.rows), "на каждого игрока должна быть полоса"
ratios = []
for t, f in zip(tracks, fills):
    track_w = t[2] - t[0]
    fill_w = f[2] - f[0]
    ratios.append(round(fill_w / track_w * 100, 1))
print("BARS (% от лучшего):", ratios)
assert ratios[0] == 100.0, "у лучшего игрока полоса должна быть заполнена целиком"
assert ratios == sorted(ratios, reverse=True), "игроки идут по убыванию урона"
if len(ratios) > 1:
    dmg = [app.group_table.rows[i]["damage"]["bar"] for i in range(len(ratios))]
    assert abs(ratios[1] - round(dmg[1] / dmg[0] * 100, 1)) < 0.5, "доля полосы = доля урона"

# ---- заголовок виден и содержит iLvl ----
head_texts = [app.group_table.head_canvas.itemcget(i, "text")
              for i in app.group_table.head_canvas.find_all()
              if app.group_table.head_canvas.type(i) == "text"]
print("HEADERS:", head_texts)
assert "iLvl" in head_texts, "в заголовке должен быть столбец iLvl"

print("OK")
app.destroy()
