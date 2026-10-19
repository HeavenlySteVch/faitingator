# -*- coding: utf-8 -*-
r"""
WoW Damage Parser — статистика урона из combat log.

Читает Logs\WoWCombatLog.txt (World of Warcraft), находит заходы в подземелья
и считает нанесённый урон, DPS и распределение по способностям.

Запуск GUI:        python wow_damage_app.py
Запуск из консоли: python wow_damage_app.py --cli "путь\к\WoWCombatLog.txt" [--player Имя]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# ----------------------------------------------------------------------------
# Константы
# ----------------------------------------------------------------------------
DAMAGE_EVENTS = {
    "SWING_DAMAGE",
    "SPELL_DAMAGE",
    "SPELL_PERIODIC_DAMAGE",
    "RANGE_DAMAGE",
    "SPELL_BUILDING_DAMAGE",
    "DAMAGE_SHIELD",
    "DAMAGE_SPLIT",
}

# события, по которым определяем формат лога (у них первый аргумент — GUID источника)
GUID_EVENTS = DAMAGE_EVENTS | {
    "SWING_MISSED", "SPELL_MISSED", "SPELL_PERIODIC_MISSED", "RANGE_MISSED",
    "SPELL_HEAL", "SPELL_PERIODIC_HEAL", "SPELL_SUMMON", "SPELL_CAST_SUCCESS",
    "SPELL_AURA_APPLIED", "UNIT_DIED", "PARTY_KILL",
}

ADVANCED_PREFIX = re.compile(r"^(Player|Creature|Pet|Vehicle|GameObject|Vignette|Item)-")
HEX_GUID = re.compile(r"^[0-9A-F]{16}$")

DEFAULT_LOG_PATHS = [
    r"C:\Firestorm\Battle for Azeroth Reforged\Logs\WoWCombatLog.txt",
]

# события, где нет названия способности (только белая атака)
MELEE_EVENTS = {"SWING_DAMAGE", "DAMAGE_SHIELD"}


# ----------------------------------------------------------------------------
# Модель данных
# ----------------------------------------------------------------------------
@dataclass
class AbilityStat:
    name: str
    damage: int = 0
    hits: int = 0
    crits: int = 0
    max_hit: int = 0


@dataclass
class Run:
    """Один заход (непрерывное пребывание в одной зоне)."""
    zone: str
    start: datetime | None = None
    end: datetime | None = None
    damage: int = 0
    hits: int = 0
    active_seconds: set = field(default_factory=set)
    abilities: dict = field(default_factory=dict)
    total_group_damage: int = 0

    @property
    def duration(self) -> timedelta:
        if self.start and self.end:
            return self.end - self.start
        return timedelta(0)

    @property
    def active_time(self) -> float:
        return float(len(self.active_seconds))

    @property
    def dps(self) -> float:
        t = self.active_time
        return self.damage / t if t > 0 else 0.0

    @property
    def share(self) -> float:
        return 100.0 * self.damage / self.total_group_damage if self.total_group_damage else 0.0


@dataclass
class ParseResult:
    player_guid: str = ""
    player_name: str = ""
    advanced: bool = False
    runs: list = field(default_factory=list)
    players: dict = field(default_factory=dict)   # guid -> name
    pets_of: dict = field(default_factory=dict)   # pet guid -> owner guid
    lines_total: int = 0
    lines_parsed: int = 0


# ----------------------------------------------------------------------------
# Утилиты разбора
# ----------------------------------------------------------------------------
TIME_RE = re.compile(r"^(\d+)/(\d+)\s+(\d+):(\d+):(\d+)\.(\d+)")


def split_csv(line: str) -> list:
    """Разбивает строку лога по запятым, учитывая кавычки."""
    fields: list = []
    buf: list = []
    in_quotes = False
    for c in line:
        if c == '"':
            in_quotes = not in_quotes
        elif c == "," and not in_quotes:
            fields.append("".join(buf))
            buf = []
        else:
            buf.append(c)
    fields.append("".join(buf))
    return fields


def parse_time(match: re.Match) -> datetime:
    month, day, hh, mm, ss, ms = (int(x) for x in match.groups())
    return datetime(2000, month, day, hh, mm, ss) + timedelta(milliseconds=ms)


def clean_name(name: str) -> str:
    if not name:
        return name
    # "Имя-Сервер" -> "Имя" (осторожно с именами вида "Y'sera")
    if "-" in name and not name.startswith("0x"):
        head, _, tail = name.partition("-")
        if head and head[0].isupper() and tail and all(ch.isalnum() or ch == "-" for ch in tail):
            return head
    return name


def to_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# ----------------------------------------------------------------------------
# Парсер
# ----------------------------------------------------------------------------
class _RawSource:
    __slots__ = ("name", "damage", "hits", "active", "abilities")

    def __init__(self, name: str):
        self.name = name
        self.damage = 0
        self.hits = 0
        self.active = set()
        self.abilities: dict = {}


class _RawRun:
    __slots__ = ("zone", "start", "end", "sources", "group_damage")

    def __init__(self, zone: str, start: datetime | None):
        self.zone = zone
        self.start = start
        self.end = start
        self.sources: dict = {}
        self.group_damage = 0


class CombatLogParser:
    def __init__(self, player_filter: str | None = None, include_pets: bool = True,
                 progress_cb=None):
        self.player_filter = (player_filter or "").strip().lower()
        self.include_pets = include_pets
        self.progress_cb = progress_cb
        self.advanced: bool | None = None

    # ------------------------------------------------------------------
    def parse(self, path: str | Path) -> ParseResult:
        path = Path(path)
        size = path.stat().st_size
        res = ParseResult()
        raw_runs: list = []
        current: _RawRun | None = None
        current_zone = ""

        def close_current():
            if current is not None:
                raw_runs.append(current)

        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for lineno, raw in enumerate(fh, 1):
                res.lines_total += 1
                if self.progress_cb and lineno % 50000 == 0:
                    try:
                        self.progress_cb(min(1.0, fh.tell() / size))
                    except Exception:
                        pass

                line = raw.rstrip("\r\n")
                if not line:
                    continue
                tm = TIME_RE.match(line)
                if not tm:
                    continue
                ts = parse_time(tm)
                rest = line[tm.end():].lstrip()
                if not rest:
                    continue
                fields = split_csv(rest)
                event = fields[0]
                args = fields[1:]

                # --- определение формата лога ---
                if self.advanced is None and event in GUID_EVENTS and args:
                    if ADVANCED_PREFIX.match(args[0]):
                        self.advanced = True
                    elif HEX_GUID.match(args[0]):
                        self.advanced = False
                if self.advanced is None:
                    continue

                res.lines_parsed += 1

                # --- смена локации ---
                if event == "ZONE_CHANGE" and len(args) >= 2:
                    zone = args[1].strip('"')
                    if zone != current_zone:
                        close_current()
                        current_zone = zone
                        current = _RawRun(zone=zone, start=ts)
                    continue

                if current is None:
                    current = _RawRun(zone=current_zone or "Без зоны", start=ts)

                # --- связка питомцев ---
                if event == "SPELL_SUMMON" and self.advanced and len(args) > 4:
                    owner = args[0]
                    pet = args[4]
                    if owner.startswith("Player-") and pet:
                        res.pets_of[pet] = owner
                    continue

                if event not in DAMAGE_EVENTS:
                    continue

                src_guid, src_name, amount, crit, school = self._extract_damage(args, event)
                if not src_guid or not src_name:
                    continue
                if src_guid.startswith("Player-"):
                    res.players.setdefault(src_guid, src_name)
                if amount <= 0:
                    continue

                src = current.sources.get(src_guid)
                if src is None:
                    src = _RawSource(src_name)
                    current.sources[src_guid] = src
                current.group_damage += amount
                src.damage += amount
                src.hits += 1
                src.active.add(int(ts.timestamp()))
                if current.end is None or ts > current.end:
                    current.end = ts

                ability_name = self._ability_name(args, event)
                st = src.abilities.get(ability_name)
                if st is None:
                    st = AbilityStat(ability_name)
                    src.abilities[ability_name] = st
                st.damage += amount
                st.hits += 1
                if crit:
                    st.crits += 1
                if amount > st.max_hit:
                    st.max_hit = amount

        close_current()

        # --- выбор персонажа ---
        player_guid = self._pick_player(raw_runs, res)
        res.player_guid = player_guid
        res.player_name = res.players.get(player_guid, "")
        res.advanced = bool(self.advanced)

        # --- сборка итоговых заходов ---
        targets = set()
        if player_guid:
            targets.add(player_guid)
            if self.include_pets:
                for pet, owner in res.pets_of.items():
                    if owner == player_guid:
                        targets.add(pet)

        for rr in raw_runs:
            run = Run(zone=rr.zone, start=rr.start, end=rr.end,
                      total_group_damage=rr.group_damage)
            for guid, src in rr.sources.items():
                if guid not in targets:
                    continue
                run.damage += src.damage
                run.hits += src.hits
                run.active_seconds |= src.active
                for name, st in src.abilities.items():
                    tgt = run.abilities.get(name)
                    if tgt is None:
                        run.abilities[name] = AbilityStat(name, st.damage, st.hits,
                                                          st.crits, st.max_hit)
                    else:
                        tgt.damage += st.damage
                        tgt.hits += st.hits
                        tgt.crits += st.crits
                        tgt.max_hit = max(tgt.max_hit, st.max_hit)
            res.runs.append(run)

        return res

    # ------------------------------------------------------------------
    def _pick_player(self, raw_runs: list, res: ParseResult) -> str:
        # 1) по фильтру имени
        if self.player_filter:
            for guid, name in res.players.items():
                if clean_name(name).lower() == self.player_filter:
                    return guid
        # 2) по владельцу питомцев
        if res.pets_of:
            owners = {o for o in res.pets_of.values() if o.startswith("Player-")}
            if len(owners) == 1:
                return next(iter(owners))
        # 3) игрок с наибольшим уроном
        totals: dict = defaultdict(int)
        names: dict = {}
        for rr in raw_runs:
            for guid, src in rr.sources.items():
                if guid.startswith("Player-"):
                    totals[guid] += src.damage
                    names[guid] = src.name
        if totals:
            best = max(totals, key=lambda g: totals[g])
            res.players.setdefault(best, names.get(best, ""))
            return best
        return ""

    # ------------------------------------------------------------------
    def _extract_damage(self, args: list, event: str):
        """Возвращает (sourceGUID, sourceName, amount, crit, school)."""
        if self.advanced:
            src_guid = args[0] if args else ""
            src_name = args[1] if len(args) > 1 else ""
            if event == "SWING_DAMAGE":
                amount = to_int(args[8]) if len(args) > 8 else 0
                crit = (len(args) > 14 and args[14] == "1")
                school = to_int(args[10]) if len(args) > 10 else 0
            else:
                amount = to_int(args[11]) if len(args) > 11 else 0
                crit = (len(args) > 17 and args[17] == "1")
                school = to_int(args[13]) if len(args) > 13 else 0
            return src_guid, clean_name(src_name), amount, crit, school

        src_name = args[0] if args else ""
        src_guid = args[1] if len(args) > 1 else ""
        if event == "SWING_DAMAGE":
            amount = to_int(args[6]) if len(args) > 6 else 0
            crit = (len(args) > 12 and args[12] == "1")
        else:
            amount = to_int(args[9]) if len(args) > 9 else 0
            crit = (len(args) > 15 and args[15] == "1")
        return src_guid, clean_name(src_name), amount, crit, 0

    # ------------------------------------------------------------------
    def _ability_name(self, args: list, event: str) -> str:
        if event in MELEE_EVENTS:
            return "Белая атака"
        idx = 9 if self.advanced else 7
        name = args[idx] if len(args) > idx else ""
        return name or "Белая атака"


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def cli_report(path: str, player: str | None = None, include_pets: bool = True) -> None:
    parser = CombatLogParser(player_filter=player, include_pets=include_pets)
    res = parser.parse(path)
    print(f"Файл: {path}")
    print(f"Формат: {'расширенный (advanced)' if res.advanced else 'обычный'}")
    print(f"Строк: {res.lines_total}, разобрано: {res.lines_parsed}")
    print(f"Игроков: {len(res.players)}, заходов: {len(res.runs)}")
    if res.player_name:
        print(f"Персонаж: {res.player_name} ({res.player_guid})")
    print()

    def n(v):
        return f"{v:,}".replace(",", " ")

    for run in res.runs:
        if run.damage <= 0:
            continue
        print(f"[{run.zone}]  {run.start}  длит. {run.duration}  "
              f"урон {n(run.damage)}  DPS {n(round(run.dps))}  "
              f"доля {run.share:.1f}%  актив. {run.active_time:.0f} c")
        for a in sorted(run.abilities.values(), key=lambda x: x.damage, reverse=True)[:5]:
            pct = 100.0 * a.damage / run.damage if run.damage else 0
            print(f"     {a.name:<34} {n(a.damage):>12}  {pct:5.1f}%  ударов {a.hits}")
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description="WoW Damage Parser")
    ap.add_argument("log", nargs="?", help="путь к WoWCombatLog.txt")
    ap.add_argument("--player", help="имя персонажа")
    ap.add_argument("--no-pets", action="store_true", help="не учитывать питомцев")
    ap.add_argument("--cli", action="store_true", help="консольный отчёт")
    args = ap.parse_args()

    if args.cli and args.log:
        cli_report(args.log, args.player, include_pets=not args.no_pets)
        return

    from gui import run_gui
    run_gui(default_log=args.log)


if __name__ == "__main__":
    main()
