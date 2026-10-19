# -*- coding: utf-8 -*-
"""Генератор тестового WoWCombatLog (advanced формат) — только для проверки парсера."""
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)

PLAYER_GUID = "Player-1-0000ABCD"
PLAYER_NAME = "Awdawdas-Firestorm"
PET_GUID = "Pet-0-1-2-3-456-0000AA11"
PET_NAME = "Felguard"
GROUP = [
    ("Player-1-0000AAAA", "Tanky-Firestorm"),
    ("Player-1-0000BBBB", "Healz-Firestorm"),
    ("Player-1-0000CCCC", "Boomkin-Firestorm"),
]
BOSS = ("Creature-0-1-2-3-1234-000000FF", "Кровавый страж")
TRASH = ("Creature-0-1-2-3-5555-000000EE", "Служитель")

ABILITIES = [
    ("110149", "Слово Тьмы: Смерть", "0x20", 9),
    ("188196", "Стрела хаоса", "0x24", 11),
    ("196447", "Разрушение души", "0x24", 12),
    ("234153", "Похищение души", "0x20", 8),
]


def ts(dt: datetime) -> str:
    return f"{dt.month}/{dt.day} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}.{dt.microsecond // 1000:03d}"


def spell_damage(dt: datetime, src_guid: str, src_name: str, spell_id: str, spell_name: str,
                 school: str, amount: int, crit: bool, dst) -> str:
    return (f'{ts(dt)}  SPELL_DAMAGE,{src_guid},"{src_name}",0x511,0x0,'
            f'{dst[0]},"{dst[1]}",0xa48,0x0,{spell_id},"{spell_name}",{school},'
            f'{amount},0,{school},0,0,0,{"1" if crit else "nil"},nil,nil,nil')


def swing(dt: datetime, src_guid: str, src_name: str, amount: int, crit: bool, dst) -> str:
    return (f'{ts(dt)}  SWING_DAMAGE,{src_guid},"{src_name}",0x511,0x0,'
            f'{dst[0]},"{dst[1]}",0xa48,0x0,'
            f'{amount},0,1,0,0,0,{"1" if crit else "nil"},nil,nil,nil')


def build_run(lines: list, zone: str, start: datetime, duration_sec: int, player_share: float):
    lines.append(f'{ts(start)}  ZONE_CHANGE,9999,"{zone}"')
    lines.append(f'{ts(start)}  ENCOUNTER_START,2431,"{BOSS[1]}",8,5')
    t = start
    end = start + timedelta(seconds=duration_sec)
    while t < end:
        t += timedelta(milliseconds=random.randint(120, 900))
        dst = BOSS if random.random() < 0.6 else TRASH
        # наш персонаж и его питомец
        r = random.random()
        if r < player_share:
            if random.random() < 0.35:
                lines.append(swing(t, PLAYER_GUID, PLAYER_NAME,
                                   random.randint(800, 3000), random.random() < 0.2, dst))
            else:
                sid, sname, school, base = random.choice(ABILITIES)
                crit = random.random() < 0.25
                amount = int(random.uniform(0.4, 1.0) * base * 220 * (2 if crit else 1))
                lines.append(spell_damage(t, PLAYER_GUID, PLAYER_NAME,
                                          sid, sname, school, amount, crit, dst))
            if random.random() < 0.4:
                sid, sname, school, base = random.choice(ABILITIES)
                amount = int(random.uniform(0.2, 0.5) * base * 150)
                lines.append(spell_damage(t, PET_GUID, PET_NAME, sid, sname, school, amount, False, dst))
        else:
            g = random.choice(GROUP)
            sid, sname, school, base = random.choice(ABILITIES)
            amount = int(random.uniform(0.4, 1.0) * base * 200)
            lines.append(spell_damage(t, g[0], g[1], sid, sname, school, amount, False, dst))
    lines.append(f'{ts(end)}  ENCOUNTER_END,2431,"{BOSS[1]}",8,5,1')


def main():
    lines = [
        '10/18 18:59:59.900  COMBAT_LOG_VERSION,11,ADVANCED_LOG_ENABLED,1,BUILD_VERSION,8.3.7,PROJECT_ID,1',
        '10/18 18:59:59.950  SPELL_SUMMON,Player-1-0000ABCD,"Awdawdas-Firestorm",0x511,0x0,'
        'Pet-0-1-2-3-456-0000AA11,"Felguard",0x1113,0x0,30146,"Призыв стража Скверны",0x1',
    ]
    base = datetime(2000, 10, 18, 19, 0, 0)
    build_run(lines, "Яма Сарона", base, 900, 0.52)
    build_run(lines, "Гробница Саргераса", base + timedelta(hours=1), 1500, 0.41)
    build_run(lines, "Подземелье: Крепость Черной Ладьи", base + timedelta(hours=2), 1200, 0.47)

    out = Path(__file__).parent / "sample_logs" / "WoWCombatLog_sample.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Создано: {out}  (строк: {len(lines)})")


if __name__ == "__main__":
    main()
