# -*- coding: utf-8 -*-
r"""
WoW Damage Parser — статистика заходов из combat log.

Читает Logs\WoWCombatLog.txt (World of Warcraft), находит заходы в подземелья и рейды,
считает урон, исцеление, прерывания и смерти по каждому игроку группы,
определяет уровень ключа, время прохождения и бонус (+1/+2/+3).

При включённом расширенном логировании (`/console advancedCombatLogging 1`)
дополнительно читает COMBATANT_INFO: класс, специализацию, уровень предметов
и характеристики каждого персонажа.

Запуск GUI:        python wow_damage_app.py
Запуск из консоли: python wow_damage_app.py --cli "путь\к\WoWCombatLog.txt" [--keys] [--all]
                   (по умолчанию только подземелья; --all — вместе с открытым миром)
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from classes import SPECS, class_by_spec, dungeon_timer, guess_class, refine_spec

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
HEAL_EVENTS = {"SPELL_HEAL", "SPELL_PERIODIC_HEAL"}

DEFAULT_LOG_PATHS = [
    r"C:\Firestorm\Battle for Azeroth Reforged\Logs\WoWCombatLog.txt",
]

GUID_RE = re.compile(r"^([A-Z][a-z]+-\d+-[0-9A-F]+|[0-9A-F]{16})$")
HEX_GUID_RE = re.compile(r"^[0-9A-F]{16}$")
TIME_RE = re.compile(r"^(\d+)/(\d+)\s+(\d+):(\d+):(\d+)\.(\d+)")
GEAR_ITEM_RE = re.compile(r"\((\d+),(\d+),")
NIL_GUIDS = {"", "0000000000000000", "0", "nil"}
SPEC_IDS = set(SPECS.keys())
# COMBATLOG_OBJECT_TYPE_PLAYER: цель урона — персонаж (самоурон «Пошатывания» и т.п.)
FLAG_PLAYER = 0x400
# Ауры, по которым восстанавливается связь «питомец -> владелец».
AURA_EVENTS = ("SPELL_AURA_APPLIED", "SPELL_AURA_REFRESH", "SPELL_AURA_APPLIED_DOSE")


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
class CombatantInfo:
    guid: str
    spec_id: int = 0
    cls: str = ""
    spec: str = ""
    strength: int = 0
    agility: int = 0
    stamina: int = 0
    intellect: int = 0
    armor: int = 0
    crit: int = 0
    haste: int = 0
    mastery: int = 0
    versatility: int = 0
    ilvl: float = 0.0

    @property
    def has_stats(self) -> bool:
        return bool(self.strength or self.agility or self.stamina or self.intellect)


@dataclass
class PlayerRun:
    guid: str
    name: str
    damage: int = 0
    healing: int = 0
    interrupts: int = 0
    deaths: int = 0
    active: set = field(default_factory=set)
    abilities: dict = field(default_factory=dict)
    spell_ids: dict = field(default_factory=dict)
    cls: str = ""
    spec: str = ""
    spec_id: int = 0
    info: CombatantInfo | None = None

    @property
    def dps(self) -> float:
        return self.damage / len(self.active) if self.active else 0.0

    @property
    def hps(self) -> float:
        return self.healing / len(self.active) if self.active else 0.0


@dataclass
class Run:
    zone: str
    instance_id: int = 0
    difficulty: int = 0
    start: datetime | None = None
    end: datetime | None = None
    key_level: int | None = None
    key_success: bool | None = None
    key_time_ms: int | None = None
    timer_limit: int | None = None
    group_size: int = 0
    boss_kills: int = 0
    players: dict = field(default_factory=dict)

    @property
    def duration(self) -> timedelta:
        if self.key_time_ms:
            return timedelta(milliseconds=self.key_time_ms)
        if self.start and self.end:
            return self.end - self.start
        return timedelta(0)

    @property
    def total_damage(self) -> int:
        return sum(p.damage for p in self.players.values())

    @property
    def total_healing(self) -> int:
        return sum(p.healing for p in self.players.values())

    @property
    def bonus(self) -> str:
        """+1 / +2 / +3 / провал / —"""
        if self.key_level is None:
            return ""
        if self.key_success is False:
            return "провал"
        if not self.timer_limit or not self.key_time_ms:
            return "—"
        limit_ms = self.timer_limit * 1000
        if self.key_time_ms <= limit_ms * 0.6:
            return "+3"
        if self.key_time_ms <= limit_ms * 0.8:
            return "+2"
        if self.key_time_ms <= limit_ms:
            return "+1"
        return "провал"

    @property
    def is_instance(self) -> bool:
        """Подземелье/рейд, а не открытый мир.

        Ориентиры: сложность из ZONE_CHANGE (в открытом мире 0, в инстансе > 0),
        пройденный ключ или убитые боссы.
        """
        return bool(self.key_level) or self.difficulty > 0 or self.boss_kills > 0

    # совместимость со старым названием
    @property
    def is_dungeon(self) -> bool:
        return self.is_instance

    @property
    def is_empty(self) -> bool:
        """Заход без единого события — остаётся от служебных смен зоны."""
        if self.key_time_ms or self.boss_kills:
            return False
        return not any(p.damage or p.healing or p.interrupts or p.deaths
                       for p in self.players.values())


@dataclass
class ParseResult:
    runs: list = field(default_factory=list)
    players: dict = field(default_factory=dict)      # guid -> name
    combatants: dict = field(default_factory=dict)   # guid -> CombatantInfo
    advanced: bool = False
    has_combatant_info: bool = False
    lines_total: int = 0
    player_guid: str = ""
    player_name: str = ""


# ----------------------------------------------------------------------------
# Утилиты
# ----------------------------------------------------------------------------
def split_fields(s: str) -> list[str]:
    """Разбивает строку по запятым верхнего уровня (учитывая кавычки, () и [])."""
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    quoted = False
    for ch in s:
        if ch == '"':
            quoted = not quoted
            buf.append(ch)
            continue
        if quoted:
            buf.append(ch)
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


def parse_time(m: re.Match, year: int = 2000) -> datetime:
    month, day, hh, mm, ss, ms = (int(x) for x in m.groups())
    return datetime(year, month, day, hh, mm, ss) + timedelta(milliseconds=ms)


def is_guid(value: str) -> bool:
    return bool(value) and bool(GUID_RE.match(value)) and value not in NIL_GUIDS


def clean_name(name: str) -> str:
    if not name:
        return name
    name = name.strip('"')
    if "-" in name and not name.startswith("0x"):
        head, _, tail = name.partition("-")
        if head and head[0].isupper() and tail and all(c.isalnum() or c == "-" for c in tail):
            return head
    return name


def to_int(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def to_flags(value: str) -> int:
    """Флаги события вида 0x511 -> число."""
    try:
        return int(value, 16)
    except (TypeError, ValueError):
        return 0


def gear_ilvls(field_value: str) -> list[int]:
    """Уровни предметов из поля экипировки COMBATANT_INFO.

    Поле выглядит как [(itemID,ilvl,enchant,(азеритовые силы),(бонусы)), ...];
    важно брать только верхний уровень, иначе в средний уровень попадут
    идентификаторы азеритовых сил и чар (из-за чего он получается в разы выше).
    """
    inner = field_value.strip()
    if inner.startswith("["):
        inner = inner[1:]
    if inner.endswith("]"):
        inner = inner[:-1]

    levels: list[int] = []
    for item in split_fields(inner):
        item = item.strip()
        if not (item.startswith("(") and item.endswith(")")):
            continue
        parts = split_fields(item[1:-1])
        if len(parts) < 2:
            continue
        item_id, ilvl = to_int(parts[0]), to_int(parts[1])
        if item_id > 0 and 0 < ilvl < 1000:
            levels.append(ilvl)
    return levels


# ----------------------------------------------------------------------------
# Парсер
# ----------------------------------------------------------------------------
class CombatLogParser:
    def __init__(self, progress_cb=None):
        self.progress_cb = progress_cb

    def parse(self, path: str | Path) -> ParseResult:
        path = Path(path)
        size = max(1, path.stat().st_size)
        res = ParseResult()

        runs: list[Run] = []
        names: dict[str, str] = {}
        pets_of: dict[str, str] = {}
        # урон питомцев, владелец которых определится позже (по аурам)
        pending_pets: dict[str, list] = {}
        # (источник, способность) -> цели: нужна для поиска «своих» питомцев
        aura_dests: dict[tuple[str, int], set[str]] = {}
        aura_on_players: set[tuple[str, int]] = set()
        cur: Run | None = None
        cur_zone = ""

        # Год в combat log не пишется — восстанавливаем по дате файла
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        year_state = {"year": mtime.year, "prev_month": 0}

        def timestamp(m: re.Match) -> datetime:
            month = int(m.group(1))
            if year_state["prev_month"] and month < year_state["prev_month"] - 6:
                year_state["year"] += 1  # переход через Новый год
            dt = parse_time(m, year_state["year"])
            if not year_state["prev_month"] and dt > mtime + timedelta(days=2):
                year_state["year"] -= 1  # лог за прошлый год
                dt = parse_time(m, year_state["year"])
            year_state["prev_month"] = month
            return dt

        def get_player(run: Run, guid: str, name: str) -> PlayerRun:
            p = run.players.get(guid)
            if p is None:
                p = PlayerRun(guid=guid, name=name)
                run.players[guid] = p
            elif name and not p.name:
                p.name = name
            if name and guid not in names:
                names[guid] = name
            return p

        def close_current() -> None:
            nonlocal cur
            if cur is not None:
                if cur.end is None:
                    cur.end = cur.start
                runs.append(cur)
                cur = None

        def credit(run: Run, guid: str, name: str, amount: int, event: str,
                   a: list[str], adv: bool, amount_idx: int, ts: datetime) -> None:
            """Начисляет урон/исцеление игроку."""
            p = get_player(run, guid, name)
            if event in DAMAGE_EVENTS:
                p.damage += amount
                ability = self._ability_name(a, event)
                st = p.abilities.get(ability)
                if st is None:
                    st = AbilityStat(ability)
                    p.abilities[ability] = st
                st.damage += amount
                st.hits += 1
                if self._is_crit(a, event, adv, amount_idx):
                    st.crits += 1
                if amount > st.max_hit:
                    st.max_hit = amount
            else:
                p.healing += amount
            p.active.add(int(ts.timestamp()))

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
                ts = timestamp(tm)
                rest = line[tm.end():].lstrip()
                if not rest:
                    continue

                fields = split_fields(rest)
                event = fields[0]
                a = fields[1:]
                if not a:
                    continue

                # ---------- формат лога ----------
                if event == "COMBAT_LOG_VERSION":
                    for i, value in enumerate(a):
                        if value == "ADVANCED_LOG_ENABLED" and i + 1 < len(a):
                            res.advanced = a[i + 1].strip() == "1"
                    continue

                # ---------- зоны и ключи ----------
                if event == "ZONE_CHANGE":
                    zone = a[1].strip('"') if len(a) > 1 else ""
                    instance = to_int(a[0]) if a else 0
                    difficulty = to_int(a[2]) if len(a) > 2 else 0
                    if (cur is not None and cur.zone == zone
                            and cur.instance_id == instance and cur.difficulty == difficulty):
                        continue
                    close_current()
                    cur_zone = zone
                    cur = Run(zone=zone, instance_id=instance, difficulty=difficulty, start=ts)
                    continue

                if event == "CHALLENGE_MODE_START":
                    zone = a[0].strip('"') if a else cur_zone
                    instance = to_int(a[1]) if len(a) > 1 else 0
                    level = to_int(a[3]) if len(a) > 3 else 0
                    if cur is None or cur.zone != zone:
                        close_current()
                        cur = Run(zone=zone, instance_id=instance, start=ts)
                    cur.key_level = level or None
                    cur.instance_id = instance or cur.instance_id
                    cur.timer_limit = dungeon_timer(cur.zone)
                    continue

                if event == "CHALLENGE_MODE_END":
                    vals = [to_int(x) for x in a[1:4]] if len(a) > 1 else [0, 0, 0]
                    if not any(vals):
                        # сброс ключа без данных (например, пересоздание группы):
                        # закрывать заход нельзя, иначе остаётся пустая строка
                        if cur is not None:
                            cur.key_success = None
                            cur.key_time_ms = None
                        continue
                    if cur is None:
                        cur = Run(zone=cur_zone or "Неизвестно", start=ts)
                    cur.key_success = vals[0] == 1
                    if vals[1]:
                        cur.key_level = vals[1]
                    cur.key_time_ms = vals[2]
                    if len(a) > 4 and to_int(a[4]) > 60_000:
                        cur.timer_limit = to_int(a[4]) // 1000
                    cur.timer_limit = cur.timer_limit or dungeon_timer(cur.zone)
                    close_current()
                    continue

                if event == "ENCOUNTER_START":
                    if cur is not None and len(a) > 3:
                        cur.group_size = max(cur.group_size, to_int(a[3]))
                    continue

                if event == "ENCOUNTER_END":
                    if cur is not None and len(a) > 4 and to_int(a[4]) == 1:
                        cur.boss_kills += 1
                    continue

                # ---------- характеристики ----------
                if event == "COMBATANT_INFO":
                    info = self._parse_combatant_info(a)
                    if info is not None:
                        res.combatants[info.guid] = info
                        res.has_combatant_info = True
                        if cur is not None:
                            get_player(cur, info.guid, "").info = info
                    continue

                # ---------- питомцы ----------
                if event == "SPELL_SUMMON":
                    if len(a) > 4:
                        owner, pet = a[0], a[4]
                        if owner.startswith("Player-") and pet:
                            pets_of[pet] = owner
                    continue

                # ---------- смерть ----------
                if event in ("UNIT_DIED", "UNIT_DESTROYED"):
                    if len(a) > 4 and a[4].startswith("Player-") and cur is not None:
                        p = get_player(cur, a[4], clean_name(a[5] if len(a) > 5 else ""))
                        p.deaths += 1
                    continue

                # ---------- прерывания ----------
                if event == "SPELL_INTERRUPT":
                    if a and a[0].startswith("Player-") and cur is not None:
                        p = get_player(cur, a[0], clean_name(a[1] if len(a) > 1 else ""))
                        p.interrupts += 1
                        p.active.add(int(ts.timestamp()))
                    continue

                # ---------- урон / исцеление ----------
                if event in DAMAGE_EVENTS or event in HEAL_EVENTS:
                    if cur is None:
                        continue
                    src_guid = a[0] if a else ""
                    if not src_guid or src_guid in NIL_GUIDS:
                        continue
                    src_name = clean_name(a[1] if len(a) > 1 else "")
                    adv, amount_idx = self._layout(a, event)
                    amount = to_int(a[amount_idx]) if len(a) > amount_idx else 0
                    if amount <= 0:
                        continue
                    # урон по игроку — это полученный урон (например, «Пошатывание»
                    # хмелевара), а не нанесённый: в зачёт он не идёт
                    if event in DAMAGE_EVENTS and len(a) > 6 and to_flags(a[6]) & FLAG_PLAYER:
                        continue

                    owner = pets_of.get(src_guid, "")
                    if owner:
                        src_guid = owner
                        src_name = ""
                    if not src_guid.startswith("Player-"):
                        if src_guid.startswith("Pet-"):
                            # владелец питомца может выясниться позже (по аурам)
                            pending_pets.setdefault(src_guid, []).append(
                                (cur, src_name, amount, event, a, adv, amount_idx, ts))
                        continue

                    credit(cur, src_guid, src_name, amount, event, a, adv, amount_idx, ts)
                    continue

                # ---------- ауры: связь «питомец -> владелец» ----------
                if event in AURA_EVENTS:
                    if len(a) > 8 and a[0].startswith("Player-") and a[4]:
                        key = (a[0], to_int(a[8]))
                        aura_dests.setdefault(key, set()).add(a[4])
                        if a[4].startswith("Player-"):
                            aura_on_players.add(key)
                    continue

                # ---------- касты (для определения класса без COMBATANT_INFO) ----------
                if event == "SPELL_CAST_SUCCESS":
                    if cur is not None and a and a[0].startswith("Player-"):
                        sid = to_int(a[8]) if len(a) > 8 else 0
                        if sid:
                            p = get_player(cur, a[0], clean_name(a[1] if len(a) > 1 else ""))
                            p.spell_ids[sid] = p.spell_ids.get(sid, 0) + 1

        close_current()

        # ---------- питомцы, владелец которых выяснился по аурам ----------
        # «Своя» аура питомца накладывается только на него (например, «Тёмное
        # превращение» рыцаря смерти). Баффы и хоты лекарей тоже висят на
        # питомцах, поэтому такие способности исключаем: их накладывают и на игроков.
        pet_candidates: dict[str, set[str]] = {}
        for (owner_guid, spell_id), targets in aura_dests.items():
            if (owner_guid, spell_id) in aura_on_players:
                continue
            for target in targets:
                if target.startswith("Pet-"):
                    pet_candidates.setdefault(target, set()).add(owner_guid)
        for pet_guid, events in pending_pets.items():
            owner = pets_of.get(pet_guid, "")
            if not owner:
                cands = pet_candidates.get(pet_guid) or set()
                owner = next(iter(cands)) if len(cands) == 1 else ""
            if not owner:
                continue
            for run_obj, name, amount, event, a, adv, amount_idx, ts in events:
                credit(run_obj, owner, "", amount, event, a, adv, amount_idx, ts)

        # ---------- финализация ----------
        res.players.update(names)
        for guid in res.combatants:
            res.players.setdefault(guid, "")

        for run in runs:
            for guid, info in res.combatants.items():
                if guid in run.players and run.players[guid].info is None:
                    run.players[guid].info = info
            for p in run.players.values():
                if p.info is not None:
                    p.cls = p.info.cls or p.cls
                    p.spec = p.info.spec or p.spec
                    p.spec_id = p.info.spec_id or p.spec_id
                if not p.cls:
                    p.cls = guess_class(p.spell_ids)
                # в логе Firestorm лежит лут-спек — уточняем по способностям
                p.spec_id = refine_spec(p.cls, p.spec_id, p.spell_ids)
                if p.spec_id:
                    p.spec = class_by_spec(p.spec_id)[1]
                if not p.name:
                    p.name = res.players.get(p.guid, "")

            # «прохожие»: в заход попали (например, что-то кастовали рядом),
            # но ни урона, ни лечения, ни прерываний, ни смертей, ни данных
            # COMBATANT_INFO о них нет — в отчёте они только мешают
            for stray in [g for g, pl in run.players.items()
                          if not (pl.damage or pl.healing or pl.interrupts
                                  or pl.deaths or pl.info is not None)]:
                del run.players[stray]

        res.runs = [r for r in runs if r.players and not r.is_empty]

        totals: dict[str, int] = defaultdict(int)
        for run in res.runs:
            for guid, p in run.players.items():
                totals[guid] += p.damage
        if totals:
            best = max(totals, key=lambda g: totals[g])
            res.player_guid = best
            res.player_name = res.players.get(best, "")

        return res

    # ------------------------------------------------------------------
    @staticmethod
    def _layout(a: list[str], event: str) -> tuple[bool, int]:
        """Определяет раскладку строки и индекс поля с количеством.

        Расширенная раскладка добавляет 17 полей, поэтому строка заметно
        длиннее: у обычной (без расширенной) строки урона меньше 20 полей,
        у расширенной — 30 и больше. Ориентируемся только на длину, потому что
        infoGUID в расширенной части может быть не только Player-, но и
        Creature-/Pet-/Vehicle-GUID (например, когда целью является питомец).
        """
        if event.startswith("SWING"):
            adv = len(a) >= 25
            return adv, (25 if adv else 8)
        adv = len(a) >= 30
        return adv, (28 if adv else 11)

    @staticmethod
    def _is_crit(a: list[str], event: str, adv: bool, amount_idx: int = -1) -> bool:
        if not adv:
            idx = 12 if event.startswith("SWING") else 17
            return len(a) > idx and a[idx] == "1"
        # На клиентах Firestorm признак крита идёт на 7 полей позже урона
        # (между ними: базовый урон до крита, «-1», школа и т.д.).
        idx = amount_idx + 7 if amount_idx >= 0 else (
            32 if event.startswith("SWING") else 35)
        if len(a) > idx and a[idx] == "1":
            return True
        return False

    @staticmethod
    def _ability_name(a: list[str], event: str) -> str:
        if event.startswith("SWING") or event in ("DAMAGE_SHIELD", "DAMAGE_SPLIT"):
            return "Белая атака"
        name = a[9] if len(a) > 9 else ""
        if not name or name in NIL_GUIDS:
            return "Белая атака"
        return name.strip('"')

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_combatant_info(a: list[str]) -> CombatantInfo | None:
        if len(a) < 25:
            return None
        guid = a[0]
        if not guid.startswith("Player-"):
            return None

        info = CombatantInfo(guid=guid)
        info.strength = to_int(a[2])
        info.agility = to_int(a[3])
        info.stamina = to_int(a[4])
        info.intellect = to_int(a[5])
        info.crit = to_int(a[9])
        info.haste = to_int(a[14])
        info.mastery = to_int(a[18])
        info.versatility = to_int(a[19])
        info.armor = to_int(a[22])

        spec_id = 0
        # Основной вариант: armor, specID идут непосредственно перед списком
        # талантов «(...)». Позиция фиксированная, поэтому значения
        # характеристик (например, рейтинг универсальности = 577) не путаются
        # со specID (577 — это «Охотник на демонов / Истребление»).
        if len(a) > 23 and to_int(a[23]) in SPEC_IDS:
            spec_id = to_int(a[23])
        else:
            # Запасной вариант: ищем specID перед полем талантов.
            for i in range(19, min(len(a) - 1, 32)):
                if to_int(a[i]) in SPEC_IDS and a[i + 1].startswith("("):
                    spec_id = to_int(a[i])
                    break
        info.spec_id = spec_id
        info.cls, info.spec = class_by_spec(spec_id)

        best_field = ""
        best_count = 0
        for value in a[24:]:
            if not value.startswith("["):
                continue
            count = len(GEAR_ITEM_RE.findall(value))
            if count > best_count:  # экипировка — это список предметов (id, ilvl, ...)
                best_field, best_count = value, count
        if best_field:
            levels = gear_ilvls(best_field)
            if levels:
                # Игра не учитывает рубашку и табард (у табардов BfA iLvl 120),
                # поэтому отбрасываем предметы, чей уровень резко ниже остальных:
                # иначе средний iLvl (359.7 у мага в 385-м гире) расходится с игрой.
                top = max(levels)
                kept = [lv for lv in levels if lv >= top * 0.5] or levels
                info.ilvl = round(sum(kept) / len(kept), 1)

        return info


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _n(v) -> str:
    return f"{int(round(v)):,}".replace(",", " ")


def cli_report(path: str, only_keys: bool = False, everything: bool = False) -> None:
    try:  # консоль Windows может не знать часть символов
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    res = CombatLogParser().parse(path)

    print(f"Файл: {path}")
    print(f"Формат: {'расширенный' if res.advanced else 'обычный'}, "
          f"COMBATANT_INFO: {'есть' if res.has_combatant_info else 'НЕТ'}")
    print(f"Строк: {res.lines_total}, заходов: {len(res.runs)}, игроков: {len(res.players)}")
    print()

    runs = list(res.runs) if everything else [r for r in res.runs if r.is_instance]
    if only_keys:
        runs = [r for r in runs if r.key_level]
    runs.sort(key=lambda r: r.start or datetime.min, reverse=True)

    for run in runs:
        head = f"[{run.zone}] {run.start:%d.%m %H:%M}"
        if run.key_level:
            head += f"  ключ +{run.key_level}"
        if run.key_time_ms:
            head += f"  время {run.duration}  {run.bonus}"
        head += f"  урон {_n(run.total_damage)}  исц. {_n(run.total_healing)}"
        print(head)
        print(f"  {'Игрок':<18}{'Класс':<20}{'Урон':>12}{'ДПС':>8}"
              f"{'Исц.':>12}{'Прер.':>7}{'См.':>5}")
        for p in sorted(run.players.values(), key=lambda x: x.damage, reverse=True):
            cls = f"{p.cls} / {p.spec}" if p.spec else (p.cls or "—")
            print(f"  {p.name[:17]:<18}{cls[:19]:<20}{_n(p.damage):>12}"
                  f"{_n(p.dps):>8}{_n(p.healing):>12}{p.interrupts:>7}{p.deaths:>5}")
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description="WoW Damage Parser")
    ap.add_argument("log", nargs="?", help="путь к WoWCombatLog.txt")
    ap.add_argument("--cli", action="store_true", help="консольный отчёт")
    ap.add_argument("--keys", action="store_true", help="только ключи")
    ap.add_argument("--all", action="store_true",
                    help="показывать всё, включая открытый мир (по умолчанию только подземелья)")
    args = ap.parse_args()

    if args.cli and args.log:
        cli_report(args.log, only_keys=args.keys, everything=args.all)
        return

    from gui import run_gui
    run_gui(default_log=args.log)


if __name__ == "__main__":
    main()
