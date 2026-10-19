# -*- coding: utf-8 -*-
"""Генератор тестового WoWCombatLog (расширенный формат BfA) для проверки парсера."""
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(7)

# ---------------------------------------------------------------------------
# Участники: (guid, имя, specID, класс, основная характеристика)
# ---------------------------------------------------------------------------
PLAYERS = [
    ("Player-17-0000ABCD", "Awdawdas-BFAReforged", 63, "Маг", "int"),
    ("Player-17-0000AAAA", "Beartank-BFAReforged", 104, "Друид", "agi"),
    ("Player-17-0000BBBB", "Healz-BFAReforged", 257, "Жрец", "int"),
    ("Player-17-0000CCCC", "Boomkin-BFAReforged", 102, "Друид", "int"),
    ("Player-17-0000DDDD", "Huntard-BFAReforged", 253, "Охотник", "agi"),
]
PET = ("Pet-0-1-2-3-456-0000AA11", "Wolf", "Player-17-0000DDDD")

ABILITIES = {
    "Маг": [("133", "Огненный шар", "0x4", 9), ("108853", "Огненный взрыв", "0x4", 7),
            ("11366", "Пиробласт", "0x4", 12)],
    "Друид": [("5176", "Гнев", "0x8", 8), ("8921", "Лунный огонь", "0x40", 9),
              ("33917", "Увечье", "0x1", 10)],
    "Жрец": [("585", "Кары", "0x2", 7), ("205448", "Пытка разума", "0x20", 10),
             ("47540", "Епитимья", "0x2", 9)],
    "Охотник": [("19434", "Прицельный выстрел", "0x1", 11), ("217200", "Шипастый выстрел", "0x1", 8),
                ("34026", "Команда «Взять!»", "0x1", 9)],
}

BOSS = ("Creature-0-1-2-3-1234-000000FF", "Кровавый страж")
TRASH = ("Creature-0-1-2-3-5555-000000EE", "Служитель")


def ts(dt: datetime) -> str:
    return (f"{dt.month}/{dt.day} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}."
            f"{dt.microsecond // 1000:03d}")


def adv_params(src_guid: str, level: int = 120) -> str:
    return (f"{src_guid},0000000000000000,100000,100000,5000,5000,2000,0,"
            f"0,100,100,0,0.00,0.00,1642,0.0000,{level}")


def spell_damage(dt, src, dst, spell_id, spell_name, school, amount, crit=False, owner=None):
    src_guid, src_name = src
    owner_guid = owner or "0000000000000000"
    return (f'{ts(dt)}  SPELL_DAMAGE,{src_guid},"{src_name}",0x511,0x0,'
            f'{dst[0]},"{dst[1]}",0xa48,0x0,{spell_id},"{spell_name}",{school},'
            f'{owner_guid},0000000000000000,100000,100000,5000,5000,2000,0,'
            f'0,100,100,0,0.00,0.00,1642,0.0000,120,'
            f'{amount},0,{school},0,0,0,{"1" if crit else "nil"},nil,nil,nil')


def swing(dt, src, dst, amount, crit=False):
    src_guid, src_name = src
    return (f'{ts(dt)}  SWING_DAMAGE,{src_guid},"{src_name}",0x511,0x0,'
            f'{dst[0]},"{dst[1]}",0xa48,0x0,'
            f'{src_guid},0000000000000000,100000,100000,5000,5000,2000,0,'
            f'0,100,100,0,0.00,0.00,1642,0.0000,120,'
            f'{amount},0,1,0,0,0,{"1" if crit else "nil"},nil,nil,nil')


def spell_heal(dt, src, dst, spell_id, spell_name, amount):
    src_guid, src_name = src
    return (f'{ts(dt)}  SPELL_HEAL,{src_guid},"{src_name}",0x511,0x0,'
            f'{dst[0]},"{dst[1]}",0x511,0x0,{spell_id},"{spell_name}",0x2,'
            f'{src_guid},0000000000000000,100000,100000,5000,9000,2000,0,'
            f'0,100,100,0,0.00,0.00,1642,0.0000,120,'
            f'{amount},0,0,nil')


def interrupt(dt, src, dst, spell_id=2139):
    src_guid, src_name = src
    return (f'{ts(dt)}  SPELL_INTERRUPT,{src_guid},"{src_name}",0x511,0x0,'
            f'{dst[0]},"{dst[1]}",0xa48,0x0,{spell_id},"Прерывание",0x40,'
            f'{src_guid},0000000000000000,100000,100000,5000,5000,2000,0,'
            f'0,100,100,0,0.00,0.00,1642,0.0000,120,'
            f'55555,"Каст босса",0x1')


def cast(dt, src, spell_id, spell_name):
    src_guid, src_name = src
    return (f'{ts(dt)}  SPELL_CAST_SUCCESS,{src_guid},"{src_name}",0x511,0x0,'
            f'0000000000000000,nil,0x80000000,0x80000000,{spell_id},"{spell_name}",0x1,'
            f'{src_guid},0000000000000000,100000,100000,5000,5000,2000,0,'
            f'0,100,100,0,0.00,0.00,1642,0.0000,120')


def death(dt, victim):
    guid, name = victim
    return (f'{ts(dt)}  UNIT_DIED,0000000000000000,nil,0x80000000,0x80000000,'
            f'{guid},"{name}",0x511,0x0,0')


def combatant_info(guid, spec_id, main_stat, dt, ilvl=430):
    stats = dict(str_=1000, agi=1000, sta=6000, int_=1000)
    if main_stat == "int":
        stats["int_"] = 12000
    elif main_stat == "agi":
        stats["agi"] = 12000
    else:
        stats["str_"] = 12000
    crit, haste, mastery, vers = 900, 800, 1100, 500
    gear = ",".join(
        f"({50000 + i},{ilvl + random.randint(-5, 5)},(),(),())" for i in range(16))
    return (
        f'{ts(dt)}  COMBATANT_INFO,{guid},1,'
        f'{stats["str_"]},{stats["agi"]},{stats["sta"]},{stats["int_"]},'
        f'0,0,0,{crit},{crit},{crit},10,0,{haste},{haste},{haste},0,'
        f'{mastery},{vers},{vers},{vers},{2500},'
        f'{spec_id},(193155,64129,238136),(0,235587),'
        f'[0,0,[],[],[]],[{gear}],[]'
    )


def build_run(lines, zone, instance_id, key_level, duration_s, start, deaths=1):
    lines.append(f'{ts(start)}  ZONE_CHANGE,{instance_id},"{zone}",1')
    if key_level:
        lines.append(f'{ts(start)}  CHALLENGE_MODE_START,"{zone}",{instance_id},'
                     f'{instance_id},{key_level},[9,122,4,121]')
    for guid, name, spec_id, cls, main in PLAYERS:
        lines.append(combatant_info(guid, spec_id, main, start))

    lines.append(f'{ts(start)}  ENCOUNTER_START,2137,"{BOSS[1]}",8,5,{instance_id}')

    t = start
    end = start + timedelta(seconds=duration_s)
    healed = 0
    death_done = 0
    while t < end:
        t += timedelta(milliseconds=random.randint(150, 700))
        dst = BOSS if random.random() < 0.65 else TRASH

        # урон игроков
        for guid, name, spec_id, cls, main in PLAYERS:
            if random.random() > 0.35:
                continue
            sid, sname, school, base = random.choice(ABILITIES[cls])
            crit = random.random() < 0.25
            amount = int(random.uniform(0.4, 1.0) * base * 150 * (2 if crit else 1))
            lines.append(spell_damage(t, (guid, name), dst, sid, sname, school,
                                      amount, crit))

        # урон питомца охотника (владелец указан в событии)
        if random.random() < 0.5:
            sid, sname, school, base = random.choice(ABILITIES["Охотник"])
            lines.append(spell_damage(t, PET[:2], dst, sid, sname, school,
                                      int(base * 120), owner=PET[2]))

        # исцеление жреца
        if random.random() < 0.45:
            amount = random.randint(3000, 12000)
            healed += amount
            lines.append(spell_heal(t, (PLAYERS[2][0], PLAYERS[2][1]),
                                    (PLAYERS[0][0], PLAYERS[0][1]),
                                    "2061", "Светлое исцеление", amount))

        # прерывания
        if random.random() < 0.03:
            src = random.choice(PLAYERS)
            lines.append(interrupt(t, (src[0], src[1]), BOSS))

        # касты (для определения класса без COMBATANT_INFO)
        if random.random() < 0.08:
            src = random.choice(PLAYERS)
            sid, sname, _, _ = random.choice(ABILITIES[src[3]])
            lines.append(cast(t, (src[0], src[1]), sid, sname))

        # смерть одного игрока
        if death_done < deaths and t > start + timedelta(seconds=duration_s * 0.4):
            victim = PLAYERS[-(death_done + 1)]
            lines.append(death(t, (victim[0], victim[1])))
            death_done += 1

    lines.append(f'{ts(end)}  ENCOUNTER_END,2137,"{BOSS[1]}",8,5,1,{duration_s * 1000}')

    if key_level:
        lines.append(f'{ts(end)}  CHALLENGE_MODE_END,{instance_id},1,{key_level},'
                     f'{int(duration_s * 1000)}')


def main():
    lines = [
        '10/18 18:59:59.900  COMBAT_LOG_VERSION,14,ADVANCED_LOG_ENABLED,1,'
        'BUILD_VERSION,8.3.7,PROJECT_ID,1',
        '10/18 18:59:59.950  ZONE_CHANGE,1642,"Зулдазар",0',
        '10/18 18:59:59.960  SPELL_SUMMON,Player-17-0000DDDD,"Huntard-BFAReforged",'
        '0x511,0x0,Pet-0-1-2-3-456-0000AA11,"Wolf",0x1113,0x0,30146,'
        '"Призыв питомца",0x1',
    ]
    base = datetime(2000, 10, 18, 19, 0, 0)
    build_run(lines, "Атал'Дазар", 203, 10, 1700, base)
    build_run(lines, "Вольная гавань", 207, 15, 1400, base + timedelta(hours=1))
    build_run(lines, "Усадьба Уэйкрестов", 209, 18, 1300, base + timedelta(hours=2))
    build_run(lines, "Храм Сетралисс", 208, 0, 900, base + timedelta(hours=3), deaths=2)
    lines.append(f'{ts(base + timedelta(hours=4))}  ZONE_CHANGE,1642,"Зулдазар",0')

    out = Path(__file__).parent / "sample_logs" / "WoWCombatLog_sample.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Создано: {out}  (строк: {len(lines):,})")


if __name__ == "__main__":
    main()
