# -*- coding: utf-8 -*-
"""Проверка аддона AutoCombatLog без запуска игры.

Что проверяется:
  * файлы аддона на месте, .toc ссылается на существующий .lua;
  * .toc содержит ## Interface: с версией интерфейса (80300 = BfA 8.3);
  * кодировка UTF-8 без BOM (WoW читает аддоны как UTF-8);
  * в .lua сбалансированы блоки (function/if/do/repeat ... end/until);
  * используются нужные API (LoggingCombat) и есть команда /acl.

Запуск:
    python tools\\check_addon.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ADDON = Path(__file__).resolve().parent.parent / "addon" / "AutoCombatLog"


def strip_lua(src: str) -> str:
    """Убирает комментарии и строки, чтобы считать ключевые слова по коду."""
    out = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        # длинный комментарий --[[ ... ]] / --[=[ ... ]=]
        if src.startswith("--[", i):
            m = re.match(r"--\[(=*)\[", src[i:])
            if m:
                closing = "]" + m.group(1) + "]"
                end = src.find(closing, i + m.end())
                i = n if end < 0 else end + len(closing)
                continue
        if src.startswith("--", i):
            end = src.find("\n", i)
            i = n if end < 0 else end
            continue
        # длинная строка
        m = re.match(r"\[(=*)\[", src[i:])
        if m:
            closing = "]" + m.group(1) + "]"
            end = src.find(closing, i + m.end())
            if end < 0:
                raise ValueError("незакрытая длинная строка")
            out.append(" ")
            i = end + len(closing)
            continue
        if ch in "\"'":
            j = i + 1
            while j < n:
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == ch:
                    break
                j += 1
            else:
                raise ValueError("незакрытая строка %s" % ch)
            out.append(" ")
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def words(code: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*", code)


def check_blocks(code: str) -> tuple[bool, str]:
    """Считает открывающие/закрывающие блоки (elseif — не отдельный if)."""
    counts: dict[str, int] = {}
    for w in words(code):
        counts[w] = counts.get(w, 0) + 1
    opens = counts.get("function", 0) + counts.get("do", 0) + counts.get("repeat", 0)
    opens += counts.get("if", 0)
    closes = counts.get("end", 0) + counts.get("until", 0)
    detail = (f"открыто: function={counts.get('function', 0)} "
              f"if={counts.get('if', 0)} elseif={counts.get('elseif', 0)} "
              f"do={counts.get('do', 0)} repeat={counts.get('repeat', 0)} "
              f"-> {opens}; закрыто: end={counts.get('end', 0)} "
              f"until={counts.get('until', 0)} -> {closes}")
    return opens == closes, detail


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    problems: list[str] = []

    toc = ADDON / "AutoCombatLog.toc"
    lua = ADDON / "AutoCombatLog.lua"
    for f in (toc, lua):
        if not f.is_file():
            problems.append(f"нет файла: {f}")
    if problems:
        print("\n".join(problems))
        return 1

    for f in (toc, lua):
        raw = f.read_bytes()
        if raw[:3] == b"\xef\xbb\xbf":
            problems.append(f"{f.name}: есть BOM (нужен UTF-8 без BOM)")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append(f"{f.name}: не UTF-8 ({exc})")

    toc_text = toc.read_text(encoding="utf-8")
    lua_text = lua.read_text(encoding="utf-8")

    iface = re.search(r"^##\s*Interface:\s*(.+)$", toc_text, re.M)
    if not iface:
        problems.append("в .toc нет строки '## Interface:'")
    elif "80300" not in iface.group(1):
        problems.append(f"в ## Interface: нет 80300 (есть {iface.group(1)!r})")

    listed = [ln.strip() for ln in toc_text.splitlines()
              if ln.strip() and not ln.strip().startswith("##")
              and ln.strip().lower().endswith(".lua")]
    if "AutoCombatLog.lua" not in listed:
        problems.append("в .toc не подключён AutoCombatLog.lua")

    for api in ("LoggingCombat", "advancedCombatLogging"):
        if api not in lua_text:
            problems.append(f"в .lua не используется {api}")

    if "SLASH_AUTOCOMBATLOG1" not in lua_text:
        problems.append("нет слэш-команды (/acl)")

    code = strip_lua(lua_text)
    ok, detail = check_blocks(code)
    print("Блоки:", detail)
    if not ok:
        problems.append("не сбалансированы блоки function/if/do/end")

    # Незакрытые скобки в коде (грубая проверка)
    for open_ch, close_ch in (("(", ")"), ("{", "}")):
        diff = code.count(open_ch) - code.count(close_ch)
        if diff:
            problems.append(f"скобки {open_ch}{close_ch}: расхождение {diff}")

    print("Файлы:", toc.name, "+", lua.name,
          f"({len(toc_text.splitlines())} и {len(lua_text.splitlines())} строк)")
    print("Интерфейс:", iface.group(1) if iface else "(нет)")
    print("Подключено:", ", ".join(listed) or "(пусто)")

    if problems:
        print("\nПРОБЛЕМЫ:")
        for p in problems:
            print(" -", p)
        return 1
    print("ADDON OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
