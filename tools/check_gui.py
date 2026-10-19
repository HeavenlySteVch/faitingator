# -*- coding: utf-8 -*-
"""Проверка логики GUI без ручного просмотра."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gui
from wow_damage_app import CombatLogParser

res = CombatLogParser().parse(r"tools\sample_logs\WoWCombatLog_sample.txt")
app = gui.DamageApp(default_log=r"tools\sample_logs\WoWCombatLog_sample.txt")
app.result = res
app._refresh_runs()
app.update()

rows = app.tree_runs.get_children()
print("RUN ROWS:", len(rows))
for iid in rows:
    print("  ", app.tree_runs.item(iid, "values"))

print("GROUP ROWS:", len(app.tree_group.get_children()))
for iid in app.tree_group.get_children()[:3]:
    print("  ", app.tree_group.item(iid, "values"))

print("STATS ROWS:", len(app.tree_stats.get_children()))
for iid in app.tree_stats.get_children()[:2]:
    print("  ", app.tree_stats.item(iid, "values"))

print("ABILITY ROWS:", len(app.tree_ab.get_children()))
for iid in app.tree_ab.get_children()[:3]:
    print("  ", app.tree_ab.item(iid, "values"))

app.destroy()
print("OK")
