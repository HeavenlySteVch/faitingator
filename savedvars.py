# -*- coding: utf-8 -*-
"""Чтение SavedVariables аддона — это обычный Lua-файл с таблицами.

Lua-интерпретатора в системе нет, а формат, который пишет игра, простой:
присваивания вида ``Имя = { ... }`` со строками, числами, булевыми значениями
и вложенными таблицами. Поэтому разбираем его маленьким парсером.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

__all__ = ["parse_saved_variables", "saved_variables_file"]

_TOKEN_RE = re.compile(
    r"""
      (?P<skip>\s+|--\[\[.*?\]\]|--[^\n]*)
    | (?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
    | (?P<num>-?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?)
    | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    | (?P<punct>[{}\[\]=,;])
    """,
    re.VERBOSE | re.DOTALL,
)

_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b",
    "f": "\f", "v": "\v", "\\": "\\", '"': '"', "'": "'", "\n": "\n",
}


def _unescape(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch != "\\" or i + 1 >= len(text):
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1]
        if nxt.isdigit():                      # \065 — код символа
            digits = text[i + 1:i + 4]
            out.append(chr(int(digits)))
            i += 1 + len(digits)
            continue
        out.append(_ESCAPES.get(nxt, nxt))
        i += 2
    return "".join(out)


def _number(text: str) -> int | float:
    value = float(text)
    return int(value) if value.is_integer() and "." not in text and "e" not in text.lower() else value


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens = []
    for match in _TOKEN_RE.finditer(text):
        kind = match.lastgroup or ""
        if kind == "skip":
            continue
        tokens.append((kind, match.group()))
    return tokens


class _LuaParser:
    def __init__(self, tokens: list[tuple[str, str]]):
        self.tokens = tokens
        self.pos = 0

    def peek(self, offset: int = 0) -> tuple[str, str]:
        i = self.pos + offset
        return self.tokens[i] if i < len(self.tokens) else ("eof", "")

    def next(self) -> tuple[str, str]:
        token = self.peek()
        self.pos += 1
        return token

    def expect(self, value: str) -> None:
        kind, got = self.next()
        if got != value:
            raise ValueError(f"ожидалось {value!r}, получено {got!r}")

    def value(self) -> Any:
        kind, text = self.peek()
        if kind == "punct" and text == "{":
            return self.table()
        if kind == "str":
            self.pos += 1
            return _unescape(text[1:-1])
        if kind == "num":
            self.pos += 1
            return _number(text)
        if kind == "name":
            self.pos += 1
            if text == "true":
                return True
            if text == "false":
                return False
            if text == "nil":
                return None
            return text
        raise ValueError(f"неожиданный токен {text!r}")

    def table(self) -> dict[Any, Any]:
        self.expect("{")
        result: dict[Any, Any] = {}
        index = 1
        while True:
            kind, text = self.peek()
            if kind == "eof":
                raise ValueError("таблица не закрыта")
            if kind == "punct" and text == "}":
                self.pos += 1
                return result
            if kind == "punct" and text in ",;":
                self.pos += 1
                continue
            if kind == "punct" and text == "[":
                self.pos += 1
                key = self.value()
                self.expect("]")
                self.expect("=")
            elif kind == "name" and self.peek(1) == ("punct", "="):
                key = text
                self.pos += 2
            else:
                key = index
                index += 1
            result[key] = self.value()


def parse_saved_variables(text: str) -> dict[str, Any]:
    """Все присваивания верхнего уровня: {'AutoCombatLogDB': {...}}."""
    parser = _LuaParser(_tokenize(text))
    data: dict[str, Any] = {}
    while parser.peek()[0] != "eof":
        kind, name = parser.peek()
        if kind == "name" and parser.peek(1) == ("punct", "="):
            parser.pos += 2
            data[name] = parser.value()
        else:
            parser.next()
    return data


def saved_variables_file(log_path: str | Path, addon: str = "AutoCombatLog") -> Path | None:
    """<игра>\\WTF\\Account\\<аккаунт>\\SavedVariables\\<аддон>.lua (самый свежий)."""
    log = Path(log_path)
    account = log.parent.parent / "WTF" / "Account"
    if not account.is_dir():
        return None
    found = [p for p in account.glob(f"*/SavedVariables/{addon}.lua") if p.is_file()]
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime)
