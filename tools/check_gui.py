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

# ---- список заходов ----
rows = app.runs_table.rows
print("RUN ROWS:", len(rows))
for i in range(min(6, len(rows))):
    print("  ", app.runs_table.row_values(i))
print("RUN HEADERS:", [c["title"] for c in app.runs_table.columns])

# у каждой строки есть цвет по уровню ключа, а участники — цветные фрагменты
from gui import KEY_COLOR_BLACK, key_level_color  # noqa: E402
for i, run in enumerate(app.runs):
    assert rows[i]["bg"] == key_level_color(run.key_level), "цвет строки = цвет ключа"
names = rows[0]["players"]
if isinstance(names, dict):
    assert "parts" in names, "имена участников должны быть цветными фрагментами"
    texts = [p["text"] for p in names["parts"]]
    colors = {p["color"] for p in names["parts"] if p["text"] != ", "}
    print("NAMES:", texts)
    assert texts.count(", ") == (len(texts) - 1) // 2 or len(texts) == 1, "имена через запятую"
    assert len(colors) >= 1, "у имён должен быть цвет класса"

# уровень ключа -> цвет: M0 синий, +2 зелёный, +20 красный, >20 чёрный
print("KEY COLORS:", {lv: key_level_color(lv) for lv in (0, 2, 11, 20, 25)})
assert key_level_color(0) != key_level_color(2), "M0 и +2 различаются"
assert key_level_color(25) == KEY_COLOR_BLACK, "выше +20 — чёрный"

# ---- выбранный заход сразу показан в деталях ----
assert app.runs_table.selected == 0, "первый заход выбран"
assert app.tree_stats.get_children(), "детали группы заполнены"

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

# ---- заголовок виден и содержит iLvl, но не содержит столбец «Класс» ----
head_texts = [app.group_table.head_canvas.itemcget(i, "text")
              for i in app.group_table.head_canvas.find_all()
              if app.group_table.head_canvas.type(i) == "text"]
print("HEADERS:", head_texts)
assert "iLvl" in head_texts, "в заголовке должен быть столбец iLvl"
assert "Класс" not in head_texts, "столбец «Класс» убран — класс виден по цвету имени"
assert "Специализация" in head_texts, "в заголовке должна быть специализация"

print("OK")
app.destroy()
