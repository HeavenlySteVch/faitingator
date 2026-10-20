# -*- coding: utf-8 -*-
"""GUI приложения WoW Damage Parser (customtkinter + таблицы на Canvas/ttk).

Оформление: тёмная тема «графит», карточки-панели, шрифты Segoe UI (текст)
и Consolas (числа), градиентные полосы урона, строки заходов красятся по
уровню ключа.
"""
from __future__ import annotations

import shutil
import sys
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

import customtkinter as ctk

from classes import CLASS_COLORS
from wow_damage_app import (CombatLogParser, DEFAULT_LOG_PATHS, ParseResult, Run,
                            attach_loot, load_loot)


# ----------------------------------------------------------------------------
# Палитра и шрифты
# ----------------------------------------------------------------------------
APP_BG = "#15171b"
CARD = "#1c1f24"
CARD_2 = "#212731"
TABLE_BG = "#1a1d21"
HEAD_BG = "#272c33"
ROW_ALT = "#1f2328"
ROW_HOVER = "#252b33"
SEL_BG = "#26313f"
BORDER = "#2e353d"
TEXT = "#e7eaee"
TEXT_DIM = "#98a1ac"
TEXT_MUTED = "#6f7883"
ACCENT = "#4f8cf7"
ACCENT_HOVER = "#3d78de"
CARD_2_HOVER = "#323a46"
WARN = "#e0a33a"
OK = "#7ec27e"
LEADER = "#ffd479"

FONT = "Segoe UI"
FONT_NUM = "Consolas"

# Аддон автозаписи боевого журнала (лежит в репозитории, копируется в игру)
ADDON_SRC = Path(__file__).resolve().parent / "addon" / "AutoCombatLog"


def _rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v))) for v in rgb)


def mix(color_a: str, color_b: str, t: float) -> str:
    """Смешивает два цвета: t=0 — color_a, t=1 — color_b."""
    try:
        a, b = _rgb(color_a), _rgb(color_b)
    except Exception:
        return color_a
    t = max(0.0, min(1.0, t))
    return _hex(tuple(a[i] + (b[i] - a[i]) * t for i in range(3)))


def shade(color: str, factor: float) -> str:
    """Осветляет (factor > 1) или затемняет (factor < 1) цвет."""
    try:
        rgb = _rgb(color)
    except Exception:
        return color
    if factor >= 1:
        return mix(color, "#ffffff", min(1.0, factor - 1.0))
    return mix(color, "#000000", 1.0 - factor)


def hsv_hex(hue: float, sat: float, val: float) -> str:
    """Цвет из HSV (hue в градусах) — для плавного перехода между оттенками."""
    import colorsys
    r, g, b = colorsys.hsv_to_rgb((hue % 360) / 360.0, sat, val)
    return _hex((r * 255, g * 255, b * 255))


def contrast_text(color: str) -> str:
    """Тёмный или светлый текст — чтобы читался на фоне указанного цвета."""
    try:
        r, g, b = _rgb(color)
    except Exception:
        return "#ffffff"
    lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#101010" if lum > 0.6 else "#ffffff"


# Цвет строки захода по уровню ключа: M0 — синий, +2 — зелёный,
# дальше через жёлтый к красному (+20), ключи выше — чёрные.
KEY_COLOR_M0 = hsv_hex(215, 0.62, 0.42)
KEY_COLOR_2 = hsv_hex(120, 0.58, 0.38)
KEY_COLOR_BLACK = "#000000"


def key_level_color(level) -> str:
    """Цвет строки подземелья по уровню ключа (None — обычный инст, M0)."""
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = 0
    if level <= 0:
        return KEY_COLOR_M0
    if level > 20:
        return KEY_COLOR_BLACK
    if level <= 2:
        return KEY_COLOR_2
    return hsv_hex(120.0 * (1.0 - (level - 2) / 18.0), 0.62, 0.42)


def make_fonts() -> dict:
    """Шрифты таблиц на Canvas (Segoe UI для текста, Consolas для чисел)."""
    base = tkfont.nametofont("TkDefaultFont")

    def mk(size: int, weight: str = "normal", family: str = FONT):
        f = base.copy()
        f.configure(family=family, size=size, weight=weight)
        return f

    return {
        "body": mk(10),
        "body_bold": mk(10, "bold"),
        "head": mk(10, "bold"),
        "num": mk(10, family=FONT_NUM),
        "num_bold": mk(10, "bold", family=FONT_NUM),
    }


# ----------------------------------------------------------------------------
# Форматирование
# ----------------------------------------------------------------------------
def fmt_int(v) -> str:
    try:
        return f"{int(round(float(v))):,}".replace(",", " ")
    except Exception:
        return str(v)


def fmt_duration(td) -> str:
    if td is None:
        return ""
    if isinstance(td, (int, float)):
        total = int(td)
    else:
        total = int(td.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fmt_date(dt) -> str:
    return dt.strftime("%d.%m.%y %H:%M") if dt else ""


# ----------------------------------------------------------------------------
# Таблица на Canvas: цветные полосы, цветные фрагменты, выбор строки
# ----------------------------------------------------------------------------
class BarTable:
    """Таблица, где ячейка может быть полосой (доля от максимума), цветным
    текстом или несколькими цветными фрагментами (имена игроков).

    ttk.Treeview не умеет ни частичной закраски ячейки, ни разных цветов
    внутри одной ячейки, поэтому такие таблицы рисуются на Canvas.
    """

    ROW_H = 26
    HEAD_H = 30
    PAD = 10

    def __init__(self, parent, columns, on_select=None, fonts: dict | None = None,
                 row_bg=None, zebra: bool = True):
        self.columns = columns
        self.on_select = on_select
        self.fonts = fonts or make_fonts()
        self.selected = -1
        self.hover = -1
        self._row_bg = row_bg
        self._zebra = zebra

        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        self.head_canvas = tk.Canvas(frame, bg=HEAD_BG, highlightthickness=0,
                                     bd=0, height=self.HEAD_H)
        self.canvas = tk.Canvas(frame, bg=TABLE_BG, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(frame, orient="vertical", command=self.canvas.yview)
        self.hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vsb.set, xscrollcommand=self._on_xscroll)
        self.head_canvas.grid(row=0, column=0, sticky="ew")
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.vsb.grid(row=1, column=1, sticky="ns")
        self.hsb.grid(row=2, column=0, sticky="ew")

        self.rows: list[dict] = []

        self.canvas.bind("<Configure>", lambda _e: self.redraw())
        self.head_canvas.bind("<Configure>", lambda _e: self._draw_header())
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        for widget in (self.canvas, self.head_canvas):
            widget.bind("<MouseWheel>", self._on_wheel)
            widget.bind("<Shift-MouseWheel>", self._on_wheel_x)

    # ------------------------------------------------------------------
    def set_rows(self, rows: list[dict]) -> None:
        self.rows = rows
        self.selected = -1
        self.hover = -1
        self.canvas.yview_moveto(0.0)
        self.canvas.xview_moveto(0.0)
        self.redraw()

    def clear(self) -> None:
        self.set_rows([])

    def row_values(self, index: int) -> list[str]:
        """Тексты ячеек строки — для проверок и отладки."""
        row = self.rows[index]
        out = []
        for col in self.columns:
            cell = row.get(col["key"], "")
            if isinstance(cell, dict) and "parts" in cell:
                out.append("".join(str(p.get("text", "")) for p in cell["parts"]))
            elif isinstance(cell, dict):
                out.append(str(cell.get("text", "")))
            else:
                out.append(str(cell))
        return out

    def select(self, index: int, notify: bool = False) -> None:
        self.selected = index
        self.redraw()
        if notify and 0 <= index < len(self.rows) and self.on_select:
            self.on_select(index)

    # ------------------------------------------------------------------
    def _on_click(self, event):
        index = int(self.canvas.canvasy(event.y) // self.ROW_H)
        if 0 <= index < len(self.rows):
            self.select(index)
            if self.on_select:
                self.on_select(index)

    def _on_motion(self, event):
        index = int(self.canvas.canvasy(event.y) // self.ROW_H)
        if not 0 <= index < len(self.rows):
            index = -1
        if index != self.hover:
            self.hover = index
            self.canvas.configure(cursor="hand2" if (index >= 0 and self.on_select) else "")
            self.redraw()

    def _on_leave(self, _event=None):
        if self.hover != -1:
            self.hover = -1
            self.canvas.configure(cursor="")
            self.redraw()

    def _on_wheel(self, event):
        self.canvas.yview_scroll(-self._wheel_steps(event), "units")
        return "break"

    def _on_wheel_x(self, event):
        self.canvas.xview_scroll(-self._wheel_steps(event), "units")
        return "break"

    @staticmethod
    def _wheel_steps(event) -> int:
        """Прокрутка на одну строку, даже если колесо шлёт малые дельты."""
        return int(event.delta / 120) or (1 if event.delta > 0 else -1)

    def _on_xscroll(self, first, last):
        self.hsb.set(first, last)
        self._draw_header()  # заголовок едет вместе со столбцами

    def _layout(self) -> list[tuple[int, int]]:
        """Координаты и ширины колонок с учётом растягиваемых."""
        stretch = [c for c in self.columns if c.get("stretch")]
        fixed = sum(int(c["width"]) for c in self.columns)
        extra = 0
        if stretch:
            extra = max(0, self.canvas.winfo_width() - fixed) // len(stretch)
        xs, x = [], 0
        for col in self.columns:
            width = int(col["width"]) + (extra if col.get("stretch") else 0)
            xs.append((x, width))
            x += width
        return xs

    def _text_x(self, x: int, width: int, anchor: str) -> tuple[float, str]:
        if anchor == "e":
            return x + width - self.PAD, "e"
        if anchor == "center":
            return x + width / 2, "center"
        return x + self.PAD, "w"

    # ------------------------------------------------------------------
    def _draw_header(self) -> None:
        self.head_canvas.delete("all")
        xs = self._layout()
        visible_w = max(sum(w for _x, w in xs), self.head_canvas.winfo_width())
        offset = self.canvas.canvasx(0)
        self.head_canvas.create_rectangle(0, 0, visible_w, self.HEAD_H,
                                          fill=HEAD_BG, width=0)
        for (x, w), col in zip(xs, self.columns):
            tx, anchor = self._text_x(x, w, col.get("anchor", "w"))
            self.head_canvas.create_text(tx - offset, self.HEAD_H / 2 + 1,
                                         text=col["title"], anchor=anchor,
                                         fill=TEXT_DIM, font=self.fonts["head"])
        self.head_canvas.create_line(0, self.HEAD_H - 1, visible_w,
                                     self.HEAD_H - 1, fill=BORDER)

    def _row_bg_for(self, i: int, row: dict) -> str | None:
        bg = row.get("bg")
        if i == self.selected:
            return mix(bg or SEL_BG, ACCENT, 0.30)
        if i == self.hover:
            return mix(bg, "#ffffff", 0.10) if bg else ROW_HOVER
        if bg:
            return bg
        if not self._zebra:
            return None
        return ROW_ALT if i % 2 else None

    def redraw(self) -> None:
        self.canvas.delete("all")
        xs = self._layout()
        total_w = sum(w for _x, w in xs)
        visible_w = max(total_w, self.canvas.winfo_width())
        height = max(len(self.rows) * self.ROW_H, self.canvas.winfo_height())
        self.canvas.configure(scrollregion=(0, 0, total_w, height))
        self._draw_header()

        for i, row in enumerate(self.rows):
            y = i * self.ROW_H
            if self._row_bg:
                bg = self._row_bg(row)
            else:
                bg = self._row_bg_for(i, row)
            if bg:
                self.canvas.create_rectangle(0, y, visible_w, y + self.ROW_H,
                                             fill=bg, width=0, tags="rowbg")
            base = contrast_text(bg) if bg else TEXT
            for (x, w), col in zip(xs, self.columns):
                self._draw_cell(row.get(col["key"], ""), x, w, y,
                                col.get("anchor", "w"), base, col)
            self.canvas.create_line(0, y + self.ROW_H - 1, visible_w,
                                    y + self.ROW_H - 1, fill=_row_line(bg))
            if i == self.selected:
                self.canvas.create_rectangle(0, y + 1, visible_w, y + self.ROW_H - 1,
                                             outline=ACCENT, width=1, tags="sel")
                self.canvas.create_rectangle(0, y + 1, 3, y + self.ROW_H - 1,
                                             fill=ACCENT, width=0, tags="sel")

    def _draw_cell(self, cell, x: int, w: int, y: int, anchor: str,
                   base_color: str, col: dict) -> None:
        if not isinstance(cell, dict):
            cell = {"text": cell}
        text = str(cell.get("text", ""))
        value = cell.get("bar")
        color = cell.get("color") or base_color
        font = self.fonts.get(cell.get("font") or col.get("font") or "body",
                              self.fonts["body"])

        if "parts" in cell:
            self._draw_parts(cell["parts"], x, w, y, cell.get("font"))
            return

        if value is None:
            tx, ta = self._text_x(x, w, anchor)
            self.canvas.create_text(tx, y + self.ROW_H / 2, text=text,
                                    anchor=ta, fill=color, font=font)
            return

        # полоса: ширина = доля от максимума, заливка с градиентом
        top = float(cell.get("max") or 0)
        frac = float(value) / top if top > 0 else 0.0
        frac = max(0.0, min(1.0, frac))
        x0, x1 = x + self.PAD, x + w - self.PAD
        y0, y1 = y + 5, y + self.ROW_H - 5
        self.canvas.create_rectangle(x0, y0, x1, y1,
                                     fill=mix(TABLE_BG, "#ffffff", 0.05),
                                     width=0, tags="track")
        cursor = x0 + (x1 - x0) * frac
        if frac > 0:
            steps = max(1, min(16, int((cursor - x0) / 6) or 1))
            dark, light = shade(color, 0.72), shade(color, 1.15)
            for k in range(steps):
                t0 = k / steps
                seg_x0 = x0 + (cursor - x0) * t0
                seg_x1 = x0 + (cursor - x0) * ((k + 1) / steps)
                self.canvas.create_rectangle(seg_x0, y0, min(seg_x1 + 0.5, cursor), y1,
                                             fill=mix(dark, light, t0),
                                             width=0, tags="bar")
        if text:
            tx = x + w - self.PAD
            ty = y + self.ROW_H / 2
            # тень-обводка: число читается и на тёмном фоне, и на светлой полосе
            self.canvas.create_text(tx + 1, ty + 1, text=text, anchor="e",
                                    fill="#000000", font=self.fonts["num_bold"])
            self.canvas.create_text(tx, ty, text=text, anchor="e",
                                    fill=contrast_text(color),
                                    font=self.fonts["num_bold"])

    def _draw_parts(self, parts: list, x: int, w: int, y: int, font_name=None) -> None:
        """Несколько цветных фрагментов в одной ячейке (например, имена игроков)."""
        font = self.fonts.get(font_name or "body", self.fonts["body"])
        cx = x + self.PAD
        limit = x + w - self.PAD
        ellipsis = "…"
        for part in parts:
            text = str(part.get("text", ""))
            if not text:
                continue
            color = part.get("color") or TEXT
            if cx + font.measure(text) > limit:
                while text and cx + font.measure(text + ellipsis) > limit:
                    text = text[:-1]
                if text:
                    self.canvas.create_text(cx, y + self.ROW_H / 2, text=text + ellipsis,
                                            anchor="w", fill=color, font=font)
                return
            self.canvas.create_text(cx, y + self.ROW_H / 2, text=text,
                                    anchor="w", fill=color, font=font)
            cx += font.measure(text)


def _row_line(bg: str | None) -> str:
    """Линия-разделитель под строкой: чуть темнее самого фона."""
    if not bg:
        return "#23272d"
    return shade(bg, 0.75)


# ----------------------------------------------------------------------------
# Приложение
# ----------------------------------------------------------------------------
class DamageApp(ctk.CTk):
    def __init__(self, default_log: str | None = None):
        super().__init__()
        self.title("WoW Damage Parser — статистика заходов")
        self.geometry("1460x920")
        self.minsize(1180, 760)
        self.configure(fg_color=APP_BG)
        self._set_window_icon()

        self.fonts = make_fonts()
        self.result: ParseResult | None = None
        self.runs: list[Run] = []
        self.current_run: Run | None = None
        self.log_path = ctk.StringVar(value=self._guess_log_path(default_log))
        self.search = ctk.StringVar()
        self.only_instances = ctk.BooleanVar(value=True)
        self.only_keys = ctk.BooleanVar(value=False)

        self._build_ui()

    # ------------------------------------------------------------------
    def _set_window_icon(self) -> None:
        """Иконка окна и панели задач: золотой ключ-камень на тёмной плитке."""
        base = Path(__file__).resolve().parent
        if sys.platform == "win32":
            # без собственного AppUserModelID Windows показывает в панели задач
            # иконку python.exe, а не нашу
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "faitingator.wowdamage")
            except Exception:
                pass
        ico = base / "icon.ico"
        png = base / "icon.png"
        try:
            if ico.exists():
                self.iconbitmap(default=str(ico))
        except Exception:
            pass
        try:
            if png.exists():
                self._icon_photo = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _guess_log_path(self, default_log: str | None) -> str:
        if default_log and Path(default_log).exists():
            return default_log
        for p in DEFAULT_LOG_PATHS:
            if Path(p).exists():
                return p
        return DEFAULT_LOG_PATHS[0] if DEFAULT_LOG_PATHS else ""

    # ------------------------------------------------------------------
    def _card(self, parent, **kw) -> ctk.CTkFrame:
        """Панель-карточка с тонкой рамкой."""
        return ctk.CTkFrame(parent, fg_color=CARD, corner_radius=10,
                            border_width=1, border_color=BORDER, **kw)

    def _build_ui(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self._setup_tree_style()

        pad = {"padx": 12, "pady": 6}

        # ---- заголовок ----
        header = self._card(self)
        header.pack(fill="x", padx=12, pady=(12, 6))
        left = ctk.CTkFrame(header, fg_color="transparent")
        left.pack(side="left", padx=16, pady=10)
        ctk.CTkLabel(left, text="WoW  Damage  Parser", font=("Segoe UI", 19, "bold"),
                     text_color=TEXT).pack(anchor="w")
        ctk.CTkLabel(left, text="статистика заходов в подземелья по боевому журналу",
                     font=("Segoe UI", 10), text_color=TEXT_MUTED).pack(anchor="w")
        # индикаторы формата лога
        self.badges = ctk.CTkFrame(header, fg_color="transparent")
        self.badges.pack(side="right", padx=16)
        self.badge_fmt = self._badge(self.badges, "формат: —")
        self.badge_ci = self._badge(self.badges, "COMBATANT_INFO: —")
        self.badge_runs = self._badge(self.badges, "заходов: 0")

        # ---- путь к журналу ----
        top = self._card(self)
        top.pack(fill="x", **pad)
        ctk.CTkLabel(top, text="Combat log", font=("Segoe UI", 11, "bold"),
                     text_color=TEXT_DIM).pack(side="left", padx=(14, 8), pady=10)
        self.entry_path = ctk.CTkEntry(
            top, textvariable=self.log_path, height=32, corner_radius=8,
            fg_color=CARD_2, border_color=BORDER, text_color=TEXT,
            font=("Consolas", 10))
        self.entry_path.pack(side="left", fill="x", expand=True, padx=4)
        ctk.CTkButton(top, text="Обзор", width=88, height=32, corner_radius=8,
                      command=self._browse, fg_color=CARD_2, hover_color=CARD_2_HOVER,
                      border_width=1, border_color=BORDER, text_color=TEXT,
                      font=("Segoe UI", 11)).pack(side="left", padx=4)
        self.btn_load = ctk.CTkButton(
            top, text="Загрузить", width=118, height=32, corner_radius=8,
            command=self._start_parse, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="#ffffff", font=("Segoe UI", 11, "bold"))
        self.btn_load.pack(side="left", padx=(4, 12))

        # ---- фильтры ----
        filt = self._card(self)
        filt.pack(fill="x", **pad)
        check_kw = dict(font=("Segoe UI", 11), text_color=TEXT, fg_color=ACCENT,
                        hover_color=ACCENT_HOVER, border_color=BORDER,
                        checkbox_width=20, checkbox_height=20, corner_radius=5)
        ctk.CTkCheckBox(filt, text="Только подземелья", variable=self.only_instances,
                        command=self._refresh_runs, **check_kw).pack(
            side="left", padx=(14, 16), pady=10)
        ctk.CTkCheckBox(filt, text="Только ключи", variable=self.only_keys,
                        command=self._refresh_runs, **check_kw).pack(
            side="left", padx=(0, 20), pady=10)
        ctk.CTkLabel(filt, text="Поиск", font=("Segoe UI", 11, "bold"),
                     text_color=TEXT_DIM).pack(side="left", padx=(0, 6))
        entry = ctk.CTkEntry(filt, textvariable=self.search, width=260, height=30,
                             corner_radius=8, fg_color=CARD_2, border_color=BORDER,
                             text_color=TEXT, placeholder_text="название подземелья",
                             placeholder_text_color=TEXT_MUTED, font=("Segoe UI", 11))
        entry.pack(side="left", pady=10)
        entry.bind("<KeyRelease>", lambda _e: self._refresh_runs())

        self.status = ctk.CTkLabel(filt, text="Готов к работе", font=("Segoe UI", 11),
                                   text_color=TEXT_DIM)
        self.status.pack(side="right", padx=(12, 16))
        self.progress = ctk.CTkProgressBar(filt, width=150, height=6,
                                           corner_radius=3, fg_color=CARD_2,
                                           progress_color=ACCENT)
        self.progress.set(0)
        self.progress.pack(side="right", pady=10)

        # ---- разделитель ----
        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, **pad)
        top_frame = ttk.Frame(paned)
        bottom_frame = ttk.Frame(paned)
        paned.add(top_frame, weight=3)
        paned.add(bottom_frame, weight=4)

        # ================= заходы =================
        runs_card = self._card(top_frame)
        runs_card.pack(fill="both", expand=True)
        runs_head = ctk.CTkFrame(runs_card, fg_color="transparent")
        runs_head.pack(fill="x", padx=14, pady=(10, 0))
        ctk.CTkLabel(runs_head, text="ЗАХОДЫ", font=("Segoe UI", 12, "bold"),
                     text_color=TEXT).pack(side="left")
        ctk.CTkLabel(runs_head, text="клик по строке — детали группы; строка "
                                     "окрашена по уровню ключа",
                     font=("Segoe UI", 9), text_color=TEXT_MUTED).pack(
            side="left", padx=10)
        self.runs_count = ctk.CTkLabel(runs_head, text="", font=("Segoe UI", 11),
                                       text_color=TEXT_DIM)
        self.runs_count.pack(side="right")

        runs_cols = [
            {"key": "date", "title": "Дата", "width": 126, "anchor": "center",
             "font": "num"},
            {"key": "zone", "title": "Подземелье", "width": 168, "anchor": "w",
             "font": "body_bold"},
            {"key": "players", "title": "Игроки в группе", "width": 400,
             "anchor": "w", "stretch": True},
            {"key": "key", "title": "Ключ", "width": 60, "anchor": "center",
             "font": "num_bold"},
            {"key": "bonus", "title": "Результат", "width": 84, "anchor": "center"},
            {"key": "time", "title": "Время", "width": 76, "anchor": "center",
             "font": "num"},
            {"key": "limit", "title": "Таймер", "width": 76, "anchor": "center",
             "font": "num"},
            {"key": "damage", "title": "Урон группы", "width": 116, "anchor": "center",
             "font": "num_bold"},
        ]
        self.runs_table = BarTable(runs_card, runs_cols, on_select=self._on_run_row,
                                   fonts=self.fonts, zebra=False)

        # ================= детали =================
        self.tabs = ctk.CTkTabview(
            bottom_frame, fg_color=CARD, border_width=1, border_color=BORDER,
            corner_radius=10, segmented_button_fg_color=CARD_2,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOVER,
            segmented_button_unselected_color=CARD_2,
            segmented_button_unselected_hover_color=CARD_2_HOVER,
            segmented_button_font=("Segoe UI", 11, "bold"),
            text_color=TEXT)
        self.tabs.pack(fill="both", expand=True)

        tab_group = self.tabs.add("Группа")
        tab_stats = self.tabs.add("Характеристики")
        tab_ab = self.tabs.add("Способности")
        tab_loot = self.tabs.add("Добыча")
        tab_info = self.tabs.add("Инфо")

        # --- группа (таблица с полосой урона) ---
        group_head = ctk.CTkFrame(tab_group, fg_color="transparent")
        group_head.pack(fill="x", padx=10, pady=(8, 0))
        ctk.CTkLabel(group_head, text="ГРУППА", font=("Segoe UI", 12, "bold"),
                     text_color=TEXT).pack(side="left")
        self.group_summary = ctk.CTkLabel(group_head, text="", font=("Segoe UI", 10),
                                          text_color=TEXT_DIM)
        self.group_summary.pack(side="left", padx=12)

        gcols = [
            {"key": "name", "title": "Игрок", "width": 170, "anchor": "w",
             "stretch": True, "font": "body_bold"},
            {"key": "ilvl", "title": "iLvl", "width": 60, "anchor": "center",
             "font": "num"},
            {"key": "spec", "title": "Специализация", "width": 170, "anchor": "w"},
            {"key": "damage", "title": "Урон", "width": 210, "anchor": "e"},
            {"key": "dps", "title": "ДПС", "width": 90, "anchor": "center",
             "font": "num"},
            {"key": "share", "title": "Доля", "width": 74, "anchor": "center",
             "font": "num"},
            {"key": "healing", "title": "Исцеление", "width": 120, "anchor": "center",
             "font": "num"},
            {"key": "interrupts", "title": "Прерывания", "width": 112,
             "anchor": "center", "font": "num"},
            {"key": "deaths", "title": "Смерти", "width": 80, "anchor": "center",
             "font": "num"},
        ]
        self.group_table = BarTable(tab_group, gcols, fonts=self.fonts)

        # --- характеристики ---
        self.stats_note = ctk.CTkLabel(tab_stats, text="", text_color=WARN,
                                       font=("Segoe UI", 10), justify="left")
        self.stats_note.pack(side="bottom", fill="x", padx=12, pady=6)
        scols = ("name", "class", "ilvl", "str", "agi", "sta", "int",
                 "armor", "crit", "haste", "mastery", "vers")
        sheads = ("Игрок", "Класс", "iLvl", "Сила", "Ловкость", "Выносливость",
                  "Интеллект", "Броня", "Крит", "Скорость", "Искусность", "Универсальность")
        swidths = (180, 150, 70, 90, 100, 110, 100, 90, 90, 100, 110, 120)
        self.tree_stats = self._make_tree(tab_stats, scols, sheads, swidths, num_from=2)

        # --- способности ---
        ab_top = ctk.CTkFrame(tab_ab, fg_color="transparent")
        ab_top.pack(fill="x", padx=10, pady=(8, 4))
        ctk.CTkLabel(ab_top, text="Игрок", font=("Segoe UI", 11, "bold"),
                     text_color=TEXT_DIM).pack(side="left", padx=(0, 8))
        self.ability_player = ctk.CTkComboBox(
            ab_top, values=["—"], width=260, height=30, corner_radius=8,
            command=self._on_ability_player, fg_color=CARD_2, border_color=BORDER,
            button_color=CARD_2, button_hover_color=CARD_2_HOVER, text_color=TEXT,
            dropdown_fg_color=CARD_2, dropdown_text_color=TEXT,
            dropdown_hover_color=SEL_BG, font=("Segoe UI", 11))
        self.ability_player.pack(side="left")

        acols = ("ability", "damage", "pct", "hits", "crits", "maxhit")
        aheads = ("Способность", "Урон", "Доля", "Ударов", "Критов", "Макс. удар")
        awidths = (360, 150, 90, 100, 100, 140)
        self.tree_ab = self._make_tree(tab_ab, acols, aheads, awidths, num_from=1)

        # --- добыча (из SavedVariables аддона) ---
        loot_head = ctk.CTkFrame(tab_loot, fg_color="transparent")
        loot_head.pack(fill="x", padx=10, pady=(8, 0))
        ctk.CTkLabel(loot_head, text="ДОБЫЧА", font=("Segoe UI", 12, "bold"),
                     text_color=TEXT).pack(side="left")
        self.loot_summary = ctk.CTkLabel(loot_head, text="", font=("Segoe UI", 10),
                                         text_color=TEXT_DIM)
        self.loot_summary.pack(side="left", padx=12)
        self.loot_note = ctk.CTkLabel(tab_loot, text="", text_color=WARN,
                                      font=("Segoe UI", 10), justify="left",
                                      anchor="w")
        self.loot_note.pack(side="bottom", fill="x", padx=12, pady=6)
        lcols = [
            {"key": "player", "title": "Игрок", "width": 190, "anchor": "w",
             "font": "body_bold"},
            {"key": "item", "title": "Предмет", "width": 420, "anchor": "w",
             "stretch": True},
            {"key": "ilvl", "title": "iLvl", "width": 70, "anchor": "center",
             "font": "num"},
            {"key": "quality", "title": "Качество", "width": 110, "anchor": "center"},
            {"key": "time", "title": "Время", "width": 80, "anchor": "center",
             "font": "num"},
        ]
        self.loot_table = BarTable(tab_loot, lcols, fonts=self.fonts)

        # --- инфо ---
        addon_row = ctk.CTkFrame(tab_info, fg_color="transparent")
        addon_row.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        self.addon_status = ctk.CTkLabel(addon_row, text="", font=("Segoe UI", 10),
                                         text_color=TEXT_DIM)
        self.addon_status.pack(side="left", padx=(4, 12))
        ctk.CTkButton(addon_row, text="Установить автозапись журнала", width=250,
                      height=30, corner_radius=8, command=self._install_addon,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#ffffff",
                      font=("Segoe UI", 11, "bold")).pack(side="right")

        self.info_box = ctk.CTkTextbox(tab_info, wrap="word", corner_radius=8,
                                       fg_color=TABLE_BG, border_width=0,
                                       text_color=TEXT_DIM, font=("Segoe UI", 11),
                                       scrollbar_button_color=CARD_2,
                                       scrollbar_button_hover_color=CARD_2_HOVER)
        self.info_box.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.info_box.insert("1.0", self._info_text())
        self.info_box.configure(state="disabled")
        self._update_addon_status()

        # ---- нижняя панель ----
        bottom = self._card(self)
        bottom.pack(fill="x", padx=12, pady=(6, 12))
        ctk.CTkButton(bottom, text="Экспорт в Excel", width=170, height=32,
                      corner_radius=8, command=self._export_xlsx, fg_color=CARD_2,
                      hover_color=CARD_2_HOVER, border_width=1, border_color=BORDER,
                      text_color=TEXT, font=("Segoe UI", 11)).pack(
            side="left", padx=(12, 8), pady=10)
        ctk.CTkButton(bottom, text="Экспорт заходов в CSV", width=210, height=32,
                      corner_radius=8, command=self._export_runs_csv, fg_color=CARD_2,
                      hover_color=CARD_2_HOVER, border_width=1, border_color=BORDER,
                      text_color=TEXT, font=("Segoe UI", 11)).pack(
            side="left", padx=4, pady=10)
        ctk.CTkButton(bottom, text="Справка", width=110, height=32, corner_radius=8,
                      command=self._show_help, fg_color=CARD_2, hover_color=CARD_2_HOVER,
                      border_width=1, border_color=BORDER, text_color=TEXT,
                      font=("Segoe UI", 11)).pack(side="right", padx=(8, 12), pady=10)
        ctk.CTkLabel(bottom, text="F5 — перечитать журнал", font=("Segoe UI", 9),
                     text_color=TEXT_MUTED).pack(side="right", padx=8)

        self.bind("<F5>", lambda _e: self._start_parse())
        if Path(self.log_path.get()).exists():
            self.after(400, self._start_parse)

    # ------------------------------------------------------------------
    def _badge(self, parent, text: str) -> ctk.CTkLabel:
        """Небольшой индикатор-«пилюля» в шапке."""
        label = ctk.CTkLabel(parent, text=text, font=("Segoe UI", 10),
                             text_color=TEXT_DIM, fg_color=CARD_2,
                             corner_radius=9, height=26, padx=12)
        label.pack(side="left", padx=4)
        return label

    def _setup_tree_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", background=TABLE_BG, fieldbackground=TABLE_BG,
                        foreground=TEXT, rowheight=27, borderwidth=0,
                        font=("Segoe UI", 10))
        style.configure("Treeview.Heading", background=HEAD_BG, foreground=TEXT_DIM,
                        relief="flat", padding=(8, 5), font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", SEL_BG)],
                  foreground=[("selected", TEXT)])
        style.map("Treeview.Heading", background=[("active", "#2c323a")])
        for orient in ("Vertical", "Horizontal"):
            style.configure(f"{orient}.TScrollbar", background="#39414c",
                            troughcolor=TABLE_BG, darkcolor=TABLE_BG,
                            lightcolor=TABLE_BG, bordercolor=TABLE_BG,
                            arrowcolor=TEXT_DIM, borderwidth=0, arrowsize=12)
        style.configure("TPanedwindow", background=APP_BG)
        style.configure("Sash", sashthickness=8, gripcount=0, background=APP_BG)

    def _make_tree(self, parent, cols, heads, widths, num_from: int = 0) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, padx=8, pady=(6, 8))
        tree = ttk.Treeview(frame, columns=cols, show="headings")
        for i, (c, h, w) in enumerate(zip(cols, heads, widths)):
            tree.heading(c, text=h)
            anchor = "w" if c in ("name", "class", "spec", "ability") else "center"
            tree.column(c, width=w, anchor=anchor, stretch=(c in ("name", "ability")))
        tree.tag_configure("odd", background=ROW_ALT)
        tree.tag_configure("even", background=TABLE_BG)
        tree.pack(fill="both", expand=True, side="left")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        return tree

    # ------------------------------------------------------------------
    def _info_text(self) -> str:
        return (
            "Как получить данные\n"
            "====================\n\n"
            "1. Включи расширенное логирование (нужно для классов и характеристик):\n"
            "      /console advancedCombatLogging 1\n\n"
            "2. Пройди подземелье (ключ, рейд, обычный инст).\n\n"
            "3. В приложении нажми «Загрузить» (или F5). Файл журнала:\n"
            "      <папка игры>\\Logs\\WoWCombatLog.txt\n"
            "   Читать можно, пока игра запущена.\n\n"
            "Запись журнала (/combatlog)\n"
            "===========================\n"
            "Команда /combatlog действует только до выхода из игры — клиент не\n"
            "сохраняет эту настройку, поэтому её приходится вводить каждый раз.\n\n"
            "Чтобы не вводить команду: кнопка «Установить автозапись журнала»\n"
            "ниже копирует в игру маленький аддон (папка addon\\AutoCombatLog\n"
            "в репозитории). После перезапуска игры аддон сам включает запись\n"
            "при входе. Проверка в игре — команда /acl.\n\n"
            "Что показывается\n"
            "================\n"
            "• Заходы: дата, подземелье, участники (имена цветом класса), уровень\n"
            "  ключа, результат (+1/+2/+3), время и таймер. Строка красится по\n"
            "  уровню ключа: M0 — синий, +2 — зелёный, дальше плавно к красному\n"
            "  (+20), ключи выше 20 — чёрные.\n"
            "• Группа: кто был в группе, уровень предметов и специализация\n"
            "  (класс виден по цвету имени), урон (полоса — доля от лучшего\n"
            "  игрока), ДПС, доля от общего урона, исцеление, прерывания, смерти.\n"
            "• Характеристики: уровень предметов, сила, ловкость, выносливость,\n"
            "  интеллект, броня, крит, скорость, искусность, универсальность.\n"
            "• Способности: разбивка урона выбранного игрока по способностям.\n"
            "• Добыча: кому что выпало с финального сундука (предмет, его\n"
            "  уровень и качество).\n\n"
            "Как считаются цифры\n"
            "===================\n"
            "• Урон считается только по противникам — «Пошатывание» хмелевара и\n"
            "  прочий урон по себе не завышает результат. Поэтому цифры сходятся\n"
            "  с аддонами вроде Details!.\n"
            "• Урон питомцев и стражей приписывается владельцу (по призыву и по\n"
            "  «своим» аурам, например «Тёмное превращение» рыцаря смерти).\n"
            "• iLvl считается как в игре — без рубашки и табарда.\n"
            "• Специализация уточняется по способностям: в логе Firestorm в\n"
            "  COMBATANT_INFO записан лут-спек, а не боевой.\n\n"
            "Про характеристики\n"
            "===================\n"
            "Класс, специализация и статы берутся из события COMBATANT_INFO.\n"
            "Оно пишется только при включённом расширенном логировании\n"
            "(пункт 2 выше). Если статы пустые — включи его и пройди заход заново.\n\n"
            "Про бонус ключа\n"
            "================\n"
            "Бонус считается по таймеру подземелья: уложился в 100% времени — +1,\n"
            "в 80% — +2, в 60% — +3. Таймеры стандартных подземелий BfA уже вбиты;\n"
            "если подземелье неизвестно, бонус показывается как «—».\n\n"
            "Про добычу\n"
            "==========\n"
            "Добыча не попадает в боевой журнал, поэтому её записывает аддон\n"
            "AutoCombatLog: он слушает сообщения чата о полученных предметах и\n"
            "складывает их в SavedVariables. Приложению нужен файл\n"
            "      <папка игры>\\WTF\\Account\\<аккаунт>\\SavedVariables\\AutoCombatLog.lua\n"
            "Игра обновляет его при /reload или выходе из игры — после этого\n"
            "нажми «Загрузить» (F5). Записи привязываются к заходу по времени\n"
            "и названию подземелья."
        )

    def _show_help(self) -> None:
        messagebox.showinfo("Справка", self._info_text())

    # ------------------------------------------------------------------
    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Выбери WoWCombatLog.txt",
            filetypes=[("Combat log", "*.txt"), ("Все файлы", "*.*")])
        if path:
            self.log_path.set(path)
            self._update_addon_status()

    # ------------------------------------------------------------------
    # Аддон автозаписи журнала
    # ------------------------------------------------------------------
    def _game_addons_dir(self) -> Path | None:
        """Папка Interface\\AddOns игры, выведенная из пути к журналу."""
        raw = self.log_path.get().strip()
        if not raw:
            return None
        log = Path(raw)
        if log.name.lower().endswith(".txt"):   # ...\<игра>\Logs\WoWCombatLog.txt
            root = log.parent.parent
            if any((root / marker).exists() for marker in ("Wow.exe", "Interface", "Data", "WTF")):
                return root / "Interface" / "AddOns"
        return None

    def _addon_outdated(self, target: Path) -> bool:
        """В игре лежит старая копия аддона (файлы отличаются от исходных)."""
        dest = target / ADDON_SRC.name
        try:
            for src in sorted(ADDON_SRC.iterdir()):
                if not src.is_file():
                    continue
                other = dest / src.name
                if not other.exists() or other.read_bytes() != src.read_bytes():
                    return True
        except OSError:
            return False
        return False

    def _update_addon_status(self, note: str = "") -> None:
        target = self._game_addons_dir()
        if target is None:
            text = "автозапись журнала: путь к игре не распознан"
            color = TEXT_MUTED
        elif (target / ADDON_SRC.name / "AutoCombatLog.toc").exists():
            if self._addon_outdated(target):
                text = "автозапись журнала: аддон устарел — переустанови кнопкой ниже"
                color = WARN
            else:
                text = "автозапись журнала: аддон установлен"
                color = OK
        else:
            text = "автозапись журнала: аддон не установлен"
            color = WARN
        if note:
            text += " · " + note
        self.addon_status.configure(text=text, text_color=color)

    def _install_addon(self) -> None:
        target = self._game_addons_dir()
        if target is None:
            messagebox.showerror(
                "Не нашёл папку игры",
                "Не удалось определить папку игры по пути к журналу.\n"
                "Проверь путь к WoWCombatLog.txt (кнопка «Обзор») — он должен "
                "лежать в <папка игры>\\Logs\\.")
            return
        if not ADDON_SRC.is_dir():
            messagebox.showerror("Нет файлов аддона",
                                 f"Не найден каталог аддона:\n{ADDON_SRC}")
            return
        dest = target / ADDON_SRC.name
        if dest.exists() and not messagebox.askyesno(
                "Переустановить аддон?",
                f"Аддон уже лежит здесь:\n{dest}\n\nЗаменить файлы?"):
            return
        try:
            dest.mkdir(parents=True, exist_ok=True)
            for src in sorted(ADDON_SRC.iterdir()):
                if src.is_file():
                    shutil.copy2(src, dest / src.name)
        except OSError as exc:
            messagebox.showerror("Не удалось установить аддон",
                                 f"{dest}\n\n{exc}")
            return
        self._update_addon_status(note="перезапусти игру")
        messagebox.showinfo(
            "Аддон установлен",
            "Аддон автозаписи скопирован в игру:\n"
            f"{dest}\n\n"
            "Полностью перезапусти игру (новые аддоны подхватываются при "
            "запуске) — запись боевого журнала будет включаться сама, "
            "/combatlog вводить больше не нужно.\n\n"
            "Проверка в игре: команда /acl покажет состояние записи.")

    # ------------------------------------------------------------------
    def _start_parse(self) -> None:
        path = self.log_path.get().strip()
        if not path or not Path(path).exists():
            messagebox.showerror("Файл не найден",
                                 "Не найден файл журнала:\n" + (path or "(пусто)"))
            return
        self.btn_load.configure(state="disabled", text="Чтение...")
        self.progress.set(0)
        self.status.configure(text="Чтение лога...", text_color=WARN)
        self._update_addon_status()
        threading.Thread(target=self._parse_worker, args=(path,), daemon=True).start()

    def _parse_worker(self, path: str) -> None:
        try:
            parser = CombatLogParser(
                progress_cb=lambda v: self.after(0, lambda: self.progress.set(v)))
            res = parser.parse(path)
            # добыча лежит не в боевом журнале, а в SavedVariables аддона
            try:
                res.loot = load_loot(path)
                attach_loot(res.runs, res.loot)
            except Exception:
                res.loot = []
            self.after(0, lambda: self._parse_done(res))
        except Exception:
            err = traceback.format_exc()
            self.after(0, lambda: self._parse_failed(err))

    def _parse_failed(self, err: str) -> None:
        self.btn_load.configure(state="normal", text="Загрузить")
        self.status.configure(text="Ошибка чтения", text_color="#e05c5c")
        messagebox.showerror("Ошибка", err)

    def _parse_done(self, res: ParseResult) -> None:
        self.result = res
        self.btn_load.configure(state="normal", text="Загрузить")
        self.progress.set(1)

        keys = sum(1 for r in res.runs if r.key_level)
        fmt = "расширенный" if res.advanced else "обычный"
        cinfo = "есть" if res.has_combatant_info else "нет"
        self.status.configure(
            text=f"строк: {fmt_int(res.lines_total)}", text_color=TEXT_DIM)
        self.badge_fmt.configure(text=f"формат: {fmt}")
        self.badge_ci.configure(text=f"COMBATANT_INFO: {cinfo}",
                                text_color=TEXT_DIM if res.has_combatant_info else WARN)
        self.badge_runs.configure(text=f"заходов: {len(res.runs)} (ключи: {keys})")

        if res.has_combatant_info:
            self.stats_note.configure(text="")
        else:
            self.stats_note.configure(
                text="Характеристики недоступны: включи в игре "
                     "/console advancedCombatLogging 1 и пройди заход заново.")

        self._refresh_runs()

    # ------------------------------------------------------------------
    def _refresh_runs(self) -> None:
        if self.result is None:
            self.runs_table.clear()
            return

        text = self.search.get().strip().lower()
        runs = list(self.result.runs)
        if self.only_instances.get():
            runs = [r for r in runs if r.is_instance]
        if self.only_keys.get():
            runs = [r for r in runs if r.key_level]
        if text:
            runs = [r for r in runs if text in r.zone.lower()]
        runs.sort(key=lambda r: r.start.timestamp() if r.start else 0, reverse=True)
        self.runs = runs
        self.runs_count.configure(
            text=f"показано {len(runs)} из {len(self.result.runs)}")

        rows = []
        for run in runs:
            rows.append({
                "bg": key_level_color(run.key_level),
                "date": fmt_date(run.start),
                "zone": run.zone,
                "players": self._players_cell(run),
                "key": f"+{run.key_level}" if run.key_level else "",
                "bonus": run.bonus,
                "time": fmt_duration(run.duration) if (run.key_time_ms or run.end) else "",
                "limit": fmt_duration(run.timer_limit) if run.timer_limit else "",
                "damage": fmt_int(run.total_damage),
            })
        self.runs_table.set_rows(rows)

        if runs:
            self.runs_table.select(0)
            self._on_run_row(0)
        else:
            self.current_run = None
            self._clear_details()

    def _players_cell(self, run: Run) -> dict:
        """Имена участников захода, каждое — цветом класса."""
        players = [p for p in run.players.values() if p.damage or p.healing or p.interrupts]
        players.sort(key=lambda p: p.damage, reverse=True)
        parts = []
        for p in players:
            if parts:
                parts.append({"text": ", ", "color": TEXT_MUTED})
            parts.append({"text": p.name or p.guid,
                          "color": CLASS_COLORS.get(p.cls) or "#d0d0d0"})
        if not parts:
            return {"text": "—"}
        return {"parts": parts}

    def _on_run_row(self, index: int) -> None:
        if not (0 <= index < len(self.runs)):
            return
        self.current_run = self.runs[index]
        self._show_run(self.current_run)

    # ------------------------------------------------------------------
    def _clear_details(self) -> None:
        for tree in (self.tree_stats, self.tree_ab):
            for item in tree.get_children():
                tree.delete(item)
        self.group_table.clear()
        self.loot_table.clear()
        self.group_summary.configure(text="")
        self.loot_summary.configure(text="")
        self.loot_note.configure(text="")
        self.ability_player.configure(values=["—"])
        self.ability_player.set("—")

    def _show_run(self, run: Run) -> None:
        # --- группа ---
        total = run.total_damage or 1
        players = sorted(run.players.values(), key=lambda p: p.damage, reverse=True)
        top_damage = max((p.damage for p in players), default=0)
        healing = sum(p.healing for p in players)
        head = f"{len(players)} игрок(ов)"
        if run.duration:
            head += f" · время {fmt_duration(run.duration)}"
        head += f" · урон {fmt_int(run.total_damage)} · исцеление {fmt_int(healing)}"
        self.group_summary.configure(text=head)

        rows = []
        for i, p in enumerate(players):
            share = 100.0 * p.damage / total if run.total_damage else 0.0
            color = CLASS_COLORS.get(p.cls) or ACCENT
            is_leader = i == 0 and p.damage > 0
            name = p.name or p.guid
            rows.append({
                "name": ({"parts": [{"text": "★ ", "color": LEADER},
                                    {"text": name, "color": color}],
                          "font": "body_bold"}
                         if is_leader else
                         {"text": name, "color": color, "font": "body_bold"}),
                "ilvl": {"text": f"{p.info.ilvl:.0f}" if (p.info and p.info.ilvl) else "—"},
                "spec": {"text": p.spec or "—", "color": TEXT_DIM if not p.spec else TEXT},
                "damage": {"text": fmt_int(p.damage), "bar": p.damage,
                           "max": top_damage, "color": color},
                "dps": {"text": fmt_int(p.dps)},
                "share": {"text": f"{share:.1f}%"},
                "healing": {"text": fmt_int(p.healing)},
                "interrupts": {"text": fmt_int(p.interrupts)},
                "deaths": {"text": fmt_int(p.deaths),
                           "color": "#e05c5c" if p.deaths else TEXT_DIM},
            })
        self.group_table.set_rows(rows)

        # --- характеристики ---
        for item in self.tree_stats.get_children():
            self.tree_stats.delete(item)
        for i, p in enumerate(players):
            inf = p.info
            has = inf is not None and inf.has_stats
            self.tree_stats.insert("", "end", values=(
                p.name or p.guid,
                p.cls or "—",
                f"{inf.ilvl:.0f}" if inf and inf.ilvl else "—",
                fmt_int(inf.strength) if has else "—",
                fmt_int(inf.agility) if has else "—",
                fmt_int(inf.stamina) if has else "—",
                fmt_int(inf.intellect) if has else "—",
                fmt_int(inf.armor) if has else "—",
                fmt_int(inf.crit) if has else "—",
                fmt_int(inf.haste) if has else "—",
                fmt_int(inf.mastery) if has else "—",
                fmt_int(inf.versatility) if has else "—",
            ), tags=(self._class_tag(p.cls), "odd" if i % 2 else "even"))

        # --- способности ---
        names = [p.name or p.guid for p in players]
        self.ability_player.configure(values=names or ["—"])
        if names:
            self.ability_player.set(names[0])
            self._fill_abilities(players[0])
        else:
            self.ability_player.set("—")
            for item in self.tree_ab.get_children():
                self.tree_ab.delete(item)

        # --- добыча ---
        self._fill_loot(run, players)

    # ------------------------------------------------------------------
    def _fill_loot(self, run: Run, players) -> None:
        """Кому что выпало с финального сундука (данные аддона AutoCombatLog)."""
        items = list(run.loot)
        classes = {(p.name or p.guid): p.cls for p in players}
        rows = []
        for it in items:
            cls = classes.get(it.player)
            rows.append({
                "player": {"text": it.player or "—",
                           "color": CLASS_COLORS.get(cls) or TEXT_DIM,
                           "font": "body_bold"},
                "item": {"text": it.name or f"предмет #{it.item_id}",
                         "color": it.color, "font": "body_bold"},
                "ilvl": {"text": str(it.ilvl) if it.ilvl else "—"},
                "quality": {"text": it.quality_name, "color": it.color},
                "time": {"text": it.time.strftime("%H:%M") if it.time else "—"},
            })
        self.loot_table.set_rows(rows)
        self.loot_summary.configure(
            text=f"{len(items)} предмет(ов)" if items else "")
        known = bool(getattr(self.result, "loot", None))
        if items or known:
            self.loot_note.configure(text="")
        else:
            self.loot_note.configure(
                text="Добыча не попадает в боевой журнал — её записывает аддон "
                     "AutoCombatLog. Нажмите «Установить автозапись журнала» на "
                     "вкладке «Инфо», перезапустите игру: записи появятся после "
                     "/reload или выхода из игры.")

    def _class_tag(self, cls: str) -> str:
        tag = f"cls_{cls}" if cls else "cls_none"
        color = CLASS_COLORS.get(cls)
        if color:
            try:
                self.tree_stats.tag_configure(tag, foreground=color)
            except Exception:
                pass
        return tag

    def _on_ability_player(self, value: str) -> None:
        if self.current_run is None:
            return
        p = next((x for x in self.current_run.players.values()
                  if (x.name or x.guid) == value), None)
        if p is not None:
            self._fill_abilities(p)

    def _fill_abilities(self, player) -> None:
        for item in self.tree_ab.get_children():
            self.tree_ab.delete(item)
        total = player.damage or 1
        for i, a in enumerate(sorted(player.abilities.values(),
                                     key=lambda x: x.damage, reverse=True)):
            pct = 100.0 * a.damage / total if player.damage else 0.0
            self.tree_ab.insert("", "end", values=(
                a.name, fmt_int(a.damage), f"{pct:.1f}%",
                fmt_int(a.hits), fmt_int(a.crits), fmt_int(a.max_hit)),
                tags=("odd" if i % 2 else "even",))

    # ------------------------------------------------------------------
    def _export_runs_csv(self) -> None:
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                           filetypes=[("CSV", "*.csv")],
                                           initialfile="wow_runs.csv")
        if not path:
            return
        import csv
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["Дата", "Подземелье", "Ключ", "Результат", "Время (с)",
                        "Таймер (с)", "Группа", "Урон группы", "Исцеление группы"])
            for r in self.runs:
                w.writerow([
                    r.start.strftime("%d.%m.%Y %H:%M:%S") if r.start else "",
                    r.zone,
                    r.key_level or "",
                    r.bonus,
                    int(r.duration.total_seconds()),
                    r.timer_limit or "",
                    r.group_size or len(r.players),
                    r.total_damage,
                    r.total_healing,
                ])
        self.status.configure(text=f"Сохранено: {path}", text_color=TEXT_DIM)

    def _export_xlsx(self) -> None:
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx",
                                           filetypes=[("Excel", "*.xlsx")],
                                           initialfile="wow_stats.xlsx")
        if not path:
            return
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Alignment, Font, PatternFill
        except ImportError:
            messagebox.showerror("Нет openpyxl", "Установи openpyxl: pip install openpyxl")
            return

        bold = Font(bold=True, color="FFFFFF")
        head_fill = PatternFill("solid", fgColor="2F343B")
        center = Alignment(horizontal="center")

        def style_header(ws, widths, numeric_from: int | None = None):
            for c in ws[1]:
                c.font = bold
                c.fill = head_fill
                c.alignment = center
            for col, width in zip("ABCDEFGHIJKLMNO", widths):
                ws.column_dimensions[col].width = width
            ws.freeze_panes = "A2"

        wb = Workbook()

        ws = wb.active
        ws.title = "Заходы"
        ws.append(["Дата", "Подземелье", "Ключ", "Результат", "Время (с)",
                   "Таймер (с)", "Группа", "Урон группы", "Исцеление группы"])
        for r in self.runs:
            ws.append([
                r.start.strftime("%d.%m.%Y %H:%M:%S") if r.start else "",
                r.zone, r.key_level or "", r.bonus,
                int(r.duration.total_seconds()), r.timer_limit or "",
                r.group_size or len(r.players), r.total_damage, r.total_healing,
            ])
        style_header(ws, (18, 32, 8, 12, 12, 12, 10, 16, 18))

        ws2 = wb.create_sheet("Группа")
        ws2.append(["Дата", "Подземелье", "Ключ", "Игрок", "iLvl", "Класс", "Специализация",
                    "Урон", "ДПС", "Доля (%)", "Исцеление", "Прерывания", "Смерти"])
        for r in self.runs:
            total = r.total_damage or 1
            for p in sorted(r.players.values(), key=lambda x: x.damage, reverse=True):
                ilvl = round(p.info.ilvl) if (p.info and p.info.ilvl) else ""
                ws2.append([
                    r.start.strftime("%d.%m.%Y %H:%M") if r.start else "", r.zone,
                    r.key_level or "", p.name or p.guid, ilvl, p.cls, p.spec,
                    p.damage, round(p.dps), round(100.0 * p.damage / total, 1),
                    p.healing, p.interrupts, p.deaths,
                ])
        style_header(ws2, (16, 30, 8, 18, 8, 18, 20, 14, 10, 10, 14, 12, 10))

        ws3 = wb.create_sheet("Характеристики")
        ws3.append(["Дата", "Подземелье", "Игрок", "Класс", "Специализация", "iLvl",
                    "Сила", "Ловкость", "Выносливость", "Интеллект", "Броня",
                    "Крит", "Скорость", "Искусность", "Универсальность"])
        for r in self.runs:
            for p in sorted(r.players.values(), key=lambda x: x.damage, reverse=True):
                inf = p.info
                ws3.append([
                    r.start.strftime("%d.%m.%Y %H:%M") if r.start else "", r.zone,
                    p.name or p.guid, p.cls, p.spec,
                    inf.ilvl if inf else "", inf.strength if inf else "",
                    inf.agility if inf else "", inf.stamina if inf else "",
                    inf.intellect if inf else "", inf.armor if inf else "",
                    inf.crit if inf else "", inf.haste if inf else "",
                    inf.mastery if inf else "", inf.versatility if inf else "",
                ])
        style_header(ws3, (16, 30, 18, 18, 20, 8, 10, 10, 13, 12, 10, 10, 11, 12, 15))

        ws4 = wb.create_sheet("Способности")
        ws4.append(["Дата", "Подземелье", "Ключ", "Игрок", "Способность", "Урон",
                    "Доля (%)", "Ударов", "Критов", "Макс. удар"])
        for r in self.runs:
            for p in r.players.values():
                total = p.damage or 1
                for a in sorted(p.abilities.values(), key=lambda x: x.damage, reverse=True):
                    ws4.append([
                        r.start.strftime("%d.%m.%Y %H:%M") if r.start else "", r.zone,
                        r.key_level or "", p.name or p.guid, a.name, a.damage,
                        round(100.0 * a.damage / total, 1), a.hits, a.crits, a.max_hit,
                    ])
        style_header(ws4, (16, 30, 8, 18, 34, 14, 10, 10, 10, 13))

        ws5 = wb.create_sheet("Добыча")
        ws5.append(["Дата", "Подземелье", "Ключ", "Игрок", "Предмет", "iLvl",
                    "Качество", "Время"])
        for r in self.runs:
            for it in r.loot:
                ws5.append([
                    r.start.strftime("%d.%m.%Y %H:%M") if r.start else "", r.zone,
                    r.key_level or "", it.player, it.name, it.ilvl or "",
                    it.quality_name,
                    it.time.strftime("%H:%M") if it.time else "",
                ])
                ws5.cell(row=ws5.max_row, column=5).font = Font(
                    color=it.color.lstrip("#").upper())
        style_header(ws5, (16, 30, 8, 18, 38, 8, 14, 8))

        wb.save(path)
        self.status.configure(text=f"Сохранено: {path}", text_color=TEXT_DIM)


def run_gui(default_log: str | None = None) -> None:
    app = DamageApp(default_log=default_log)
    app.mainloop()


if __name__ == "__main__":
    run_gui()
