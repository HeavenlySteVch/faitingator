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
from savedvars import parse_saved_variables, saved_variables_file

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
# COMBATLOG_OBJECT_TYPE_PET / _GUARDIAN: источник урона — питомец или страж
# (у охотников второе животное и «Ужас» приходят как Creature- с флагом 0x2000)
FLAG_PET = 0x1000 | 0x2000
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
    # знаменатель для ДПС/ХПС: общее время захода (одинаковое для всех),
    # иначе у игроков с редкими «всплесками» ДПС искусственно завышается
    time_base: float = 0.0

    @property
    def dps(self) -> float:
        t = self.time_base or len(self.active)
        return self.damage / t if t else 0.0

    @property
    def hps(self) -> float:
        t = self.time_base or len(self.active)
        return self.healing / t if t else 0.0


@dataclass
class Run:
    zone: str
    instance_id: int = 0
    cm_id: int = 0
    difficulty: int = 0
    start: datetime | None = None
    end: datetime | None = None
    key_level: int | None = None
    key_success: bool | None = None
    key_time_ms: int | None = None
    key_start: datetime | None = None
    timer_limit: int | None = None
    group_size: int = 0
    boss_kills: int = 0
    players: dict = field(default_factory=dict)
    loot: list = field(default_factory=list)

    @property
    def duration(self) -> timedelta:
        if self.key_time_ms:
            return timedelta(milliseconds=self.key_time_ms)
        if self.start and self.end:
            return timedelta(seconds=round((self.end - self.start).total_seconds()))
        return timedelta(0)

    @property
    def time_base(self) -> float:
        """Реально прошедшее время захода (секунды) — знаменатель для ДПС/ХПС.

        Берётся «настенное» время от старта ключа до финиша: штраф за смерти
        (5 секунд), который игра прибавляет к таймеру, на скорость не влияет.
        """
        if self.key_start and self.end and self.end > self.key_start:
            return (self.end - self.key_start).total_seconds()
        return self.duration.total_seconds()

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
    loot: list = field(default_factory=list)         # добыча из аддона (LootItem)


# Цвета и названия качества предметов (как в игре).
QUALITY_COLORS = {
    0: "#9d9d9d", 1: "#ffffff", 2: "#1eff00", 3: "#0070dd",
    4: "#a335ee", 5: "#ff8000", 6: "#e6cc80", 7: "#00ccff",
}
QUALITY_NAMES = {
    0: "серое", 1: "обычное", 2: "необычное", 3: "редкое",
    4: "эпическое", 5: "легендарное", 6: "артефакт", 7: "фамильное",
}


@dataclass
class LootItem:
    """Одна запись о добыче из SavedVariables аддона AutoCombatLog."""
    time: datetime | None = None
    player: str = ""
    item_id: int = 0
    name: str = ""
    quality: int = 1
    ilvl: int = 0
    zone: str = ""
    instance_id: int = 0

    @property
    def color(self) -> str:
        return QUALITY_COLORS.get(self.quality, QUALITY_COLORS[1])

    @property
    def quality_name(self) -> str:
        return QUALITY_NAMES.get(self.quality, "")


def load_loot(log_path: str | Path) -> list[LootItem]:
    """Добыча, которую аддон записал в SavedVariables (пусто, если файла нет)."""
    path = saved_variables_file(log_path)
    if path is None:
        return []
    try:
        data = parse_saved_variables(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    db = data.get("AutoCombatLogDB") or {}
    raw = db.get("loot") or {}
    if not isinstance(raw, dict):
        return []
    items: list[LootItem] = []
    for entry in raw.values():
        if not isinstance(entry, dict):
            continue
        stamp = entry.get("t")
        try:
            when = datetime.fromtimestamp(float(stamp)) if stamp else None
        except (TypeError, ValueError, OSError, OverflowError):
            when = None
        items.append(LootItem(
            time=when,
            player=str(entry.get("p") or ""),
            item_id=int(entry.get("id") or 0),
            name=str(entry.get("n") or ""),
            quality=int(entry.get("q") or 1),
            ilvl=int(entry.get("ilvl") or 0),
            zone=str(entry.get("z") or ""),
            instance_id=int(entry.get("i") or 0),
        ))
    items.sort(key=lambda x: x.time or datetime.min)
    return items


def _norm_zone(value: str) -> str:
    return (value or "").strip().lower().replace("ё", "е")


def attach_loot(runs: list, loot: list[LootItem],
                before: timedelta = timedelta(minutes=10),
                after: timedelta = timedelta(minutes=30)) -> int:
    """Раскладывает добычу по заходам: по времени и названию подземелья.

    Ключ заканчивается на CHALLENGE_MODE_END, а лут падает уже после него,
    поэтому окно после финиша шире (30 минут). Возвращает число совпадений.
    """
    attached = 0
    for item in loot:
        if item.time is None:
            continue
        best = None
        best_delta = None
        for run in runs:
            if not run.is_instance:
                continue
            start = run.key_start or run.start
            end = run.end or start
            if start is None:
                continue
            if item.time < start - before or item.time > end + after:
                continue
            if item.zone and run.zone and _norm_zone(item.zone) != _norm_zone(run.zone):
                continue
            delta = abs((item.time - end).total_seconds())
            if best is None or delta < best_delta:
                best, best_delta = run, delta
        if best is not None:
            best.loot.append(item)
            attached += 1
    for run in runs:
        run.loot.sort(key=lambda x: x.time or datetime.min)
    return attached


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
        last_ts: datetime | None = None

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

        def close_current(end_ts: datetime | None = None) -> None:
            nonlocal cur
            if cur is not None:
                if cur.end is None:
                    cur.end = end_ts or cur.start
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
                last_ts = ts
                rest = line[tm.end():].lstrip()
                if not rest:
                    continue

                fields = split_fields(rest)
                event = fields[0]
                a = fields[1:]
                if not a:
                    continue

                # ---------- владелец питомца из расширенного блока ----------
                # В расширенном логе после способности идут два GUID: сама цель
                # и её владелец. У питомца/стража владелец — игрок, поэтому связь
                # «питомец -> хозяин» берётся прямо из любой строки, где питомец
                # является целью (урон по нему, лечение, энергия).
                if len(a) > 12:
                    dst = a[4]
                    if (dst and dst not in NIL_GUIDS
                            and not dst.startswith("Player-")
                            and a[11] == dst
                            and a[12].startswith("Player-")):
                        pets_of.setdefault(dst, a[12])

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
                    # Смена подзоны внутри того же инстанса — это тот же заход.
                    # Например, в «Осаде Боралуса» клиент шлёт ZONE_CHANGE на
                    # «Осада боралусского моста» и обратно: раньше из-за этого
                    # один ключ распадался на несколько строк (каждая со своим
                    # куском урона). В открытом мире у разных локаций тоже
                    # бывает один mapID (Зулдазар и Дазар'алор — 1642), но там
                    # сложность 0, поэтому по-прежнему делим.
                    if (cur is not None and instance and cur.instance_id == instance
                            and (difficulty > 0 or cur.difficulty > 0)):
                        if (dungeon_timer(cur.zone) is None
                                and dungeon_timer(zone) is not None):
                            cur.zone = zone        # подзона -> настоящее имя
                        continue
                    close_current(ts)
                    cur_zone = zone
                    cur = Run(zone=zone, instance_id=instance, difficulty=difficulty, start=ts)
                    continue

                if event == "CHALLENGE_MODE_START":
                    zone = a[0].strip('"') if a else cur_zone
                    instance = to_int(a[1]) if len(a) > 1 else 0
                    cm_id = to_int(a[2]) if len(a) > 2 else 0
                    level = to_int(a[3]) if len(a) > 3 else 0
                    # тот же инстанс, но другая подзона — это продолжение захода
                    same_instance = bool(instance) and cur is not None \
                        and cur.instance_id == instance
                    if cur is None or (cur.zone != zone and not same_instance):
                        close_current(ts)
                        cur = Run(zone=zone, instance_id=instance, start=ts)
                    elif dungeon_timer(cur.zone) is None:
                        cur.zone = zone            # имя из старта ключа точнее
                    prev_level = cur.key_level
                    cur.key_level = level or prev_level
                    cur.instance_id = instance or cur.instance_id
                    cur.cm_id = cm_id or cur.cm_id
                    cur.timer_limit = dungeon_timer(cur.zone, cur.cm_id)
                    # Ключ начался: игра и Details считают урон/исцеление/смерти
                    # именно с этого момента. Всё, что набралось с момента входа
                    # в данж (мусор до вставки ключа), отбрасываем.
                    # Повторный START того же ключа (Firestorm иногда присылает
                    # его дважды с разницей в секунды) заново не обнуляет счёт.
                    repeat = (level and cur.key_start is not None
                              and (ts - cur.key_start).total_seconds() < 60
                              and level == (prev_level or 0))
                    if level and not repeat:
                        cur.key_start = ts
                        for p in cur.players.values():
                            p.damage = 0
                            p.healing = 0
                            p.interrupts = 0
                            p.deaths = 0
                            p.active.clear()
                            p.abilities.clear()
                            p.spell_ids.clear()
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
                    cur.timer_limit = cur.timer_limit or dungeon_timer(cur.zone, cur.cm_id)
                    close_current(ts)
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
                        credit(cur, owner, "", amount, event, a, adv, amount_idx, ts)
                        continue
                    if src_guid.startswith("Player-"):
                        credit(cur, src_guid, src_name, amount, event, a, adv, amount_idx, ts)
                        continue
                    # питомец или страж: владелец может выясниться позже
                    is_pet = src_guid.startswith("Pet-") or (
                        len(a) > 2 and to_flags(a[2]) & FLAG_PET)
                    if is_pet:
                        pending_pets.setdefault(src_guid, []).append(
                            (cur, src_name, amount, event, a, adv, amount_idx, ts))
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

        close_current(last_ts)

        # ---------- питомцы, владелец которых выяснился по аурам ----------
        # «Своя» аура питомца накладывается только на него (например, «Тёмное
        # превращение» рыцаря смерти). Баффы и хоты лекарей тоже висят на
        # питомцах, поэтому такие способности исключаем: их накладывают и на игроков.
        pet_candidates: dict[str, set[str]] = {}
        for (owner_guid, spell_id), targets in aura_dests.items():
            if (owner_guid, spell_id) in aura_on_players:
                continue
            for target in targets:
                if not target.startswith("Player-"):
                    pet_candidates.setdefault(target, set()).add(owner_guid)
        for pet_guid, events in pending_pets.items():
            owner = pets_of.get(pet_guid, "")
            if not owner:
                cands = pet_candidates.get(pet_guid) or set()
                owner = next(iter(cands)) if len(cands) == 1 else ""
            if not owner:
                continue
            for run_obj, name, amount, event, a, adv, amount_idx, ts in events:
                # урон питомца до старта ключа в зачёт не идёт
                if run_obj.key_start and ts < run_obj.key_start:
                    continue
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
                p.time_base = run.time_base
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
        # в журнале название завёрнуто в кавычки, внутренние кавычки экранированы:
        # "Команда \"Взять!\"" -> Команда "Взять!"
        if len(name) >= 2 and name[0] == '"' and name[-1] == '"':
            name = name[1:-1]
        return name.replace('\\"', '"')

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
