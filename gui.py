# -*- coding: utf-8 -*-
"""GUI приложения WoW Damage Parser (customtkinter + ttk.Treeview)."""
from __future__ import annotations

import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from classes import CLASS_COLORS
from wow_damage_app import CombatLogParser, DEFAULT_LOG_PATHS, ParseResult, Run


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
# Приложение
# ----------------------------------------------------------------------------
class DamageApp(ctk.CTk):
    def __init__(self, default_log: str | None = None):
        super().__init__()
        self.title("WoW Damage Parser — статистика заходов")
        self.geometry("1400x860")
        self.minsize(1100, 700)

        self.result: ParseResult | None = None
        self.runs: list[Run] = []
        self.current_run: Run | None = None
        self.log_path = ctk.StringVar(value=self._guess_log_path(default_log))
        self.search = ctk.StringVar()
        self.only_instances = ctk.BooleanVar(value=True)
        self.only_keys = ctk.BooleanVar(value=False)

        self._build_ui()

    # ------------------------------------------------------------------
    def _guess_log_path(self, default_log: str | None) -> str:
        if default_log and Path(default_log).exists():
            return default_log
        for p in DEFAULT_LOG_PATHS:
            if Path(p).exists():
                return p
        return DEFAULT_LOG_PATHS[0] if DEFAULT_LOG_PATHS else ""

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self._setup_tree_style()

        pad = {"padx": 8, "pady": 6}

        # ---- путь к логу ----
        top = ctk.CTkFrame(self)
        top.pack(fill="x", **pad)
        ctk.CTkLabel(top, text="Combat log:").pack(side="left", padx=(10, 4))
        ctk.CTkEntry(top, textvariable=self.log_path).pack(
            side="left", fill="x", expand=True, padx=4)
        ctk.CTkButton(top, text="Обзор", width=90, command=self._browse).pack(
            side="left", padx=4)
        self.btn_load = ctk.CTkButton(top, text="Загрузить", width=110,
                                      command=self._start_parse)
        self.btn_load.pack(side="left", padx=(4, 10))

        # ---- фильтры ----
        filt = ctk.CTkFrame(self)
        filt.pack(fill="x", **pad)
        ctk.CTkCheckBox(filt, text="Только подземелья", variable=self.only_instances,
                        command=self._refresh_runs).pack(side="left", padx=(10, 12))
        ctk.CTkCheckBox(filt, text="Только ключи", variable=self.only_keys,
                        command=self._refresh_runs).pack(side="left", padx=(0, 12))
        ctk.CTkLabel(filt, text="Поиск:").pack(side="left", padx=(4, 4))
        entry = ctk.CTkEntry(filt, textvariable=self.search, width=240,
                             placeholder_text="название подземелья")
        entry.pack(side="left", padx=4)
        entry.bind("<KeyRelease>", lambda _e: self._refresh_runs())

        self.status = ctk.CTkLabel(filt, text="Готов к работе")
        self.status.pack(side="right", padx=12)
        self.progress = ctk.CTkProgressBar(filt, width=160)
        self.progress.set(0)
        self.progress.pack(side="right", padx=8)

        # ---- разделитель ----
        paned = ttk.PanedWindow(self, orient="vertical")
        paned.pack(fill="both", expand=True, **pad)

        top_frame = ttk.Frame(paned)
        bottom_frame = ttk.Frame(paned)
        paned.add(top_frame, weight=2)
        paned.add(bottom_frame, weight=3)

        # ================= заходы =================
        cols = ("date", "zone", "key", "bonus", "time", "limit", "group", "damage")
        heads = ("Дата", "Подземелье", "Ключ", "Результат", "Время",
                 "Таймер", "Группа", "Урон группы")
        widths = (110, 300, 70, 90, 90, 90, 80, 140)
        self.tree_runs = ttk.Treeview(top_frame, columns=cols, show="headings")
        for c, h, w in zip(cols, heads, widths):
            self.tree_runs.heading(c, text=h)
            anchor = "w" if c == "zone" else "center"
            self.tree_runs.column(c, width=w, anchor=anchor, stretch=(c == "zone"))
        self.tree_runs.pack(fill="both", expand=True, side="left")
        self.tree_runs.bind("<<TreeviewSelect>>", self._on_run_select)
        sb = ttk.Scrollbar(top_frame, orient="vertical", command=self.tree_runs.yview)
        self.tree_runs.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")

        # ================= детали =================
        self.tabs = ctk.CTkTabview(bottom_frame)
        self.tabs.pack(fill="both", expand=True)

        tab_group = self.tabs.add("Группа")
        tab_stats = self.tabs.add("Характеристики")
        tab_ab = self.tabs.add("Способности")
        tab_info = self.tabs.add("Инфо")

        # --- группа ---
        gcols = ("name", "class", "spec", "damage", "dps", "share",
                 "healing", "interrupts", "deaths")
        gheads = ("Игрок", "Класс", "Специализация", "Урон", "ДПС", "Доля",
                  "Исцеление", "Прерывания", "Смерти")
        gwidths = (170, 150, 170, 120, 90, 80, 120, 100, 80)
        self.tree_group = self._make_tree(tab_group, gcols, gheads, gwidths)

        # --- характеристики ---
        scols = ("name", "class", "ilvl", "str", "agi", "sta", "int",
                 "armor", "crit", "haste", "mastery", "vers")
        sheads = ("Игрок", "Класс", "iLvl", "Сила", "Ловкость", "Выносл.",
                  "Интеллект", "Броня", "Крит", "Скорость", "Искусность", "Универс.")
        swidths = (170, 150, 70, 90, 90, 90, 90, 90, 90, 90, 100, 90)
        self.tree_stats = self._make_tree(tab_stats, scols, sheads, swidths)
        self.stats_note = ctk.CTkLabel(
            tab_stats, text="", text_color="#f0a020", justify="left")
        self.stats_note.pack(side="bottom", fill="x", padx=10, pady=4)

        # --- способности ---
        ab_top = ctk.CTkFrame(tab_ab, fg_color="transparent")
        ab_top.pack(fill="x")
        ctk.CTkLabel(ab_top, text="Игрок:").pack(side="left", padx=(8, 4), pady=4)
        self.ability_player = ctk.CTkComboBox(ab_top, values=["—"], width=240,
                                              command=self._on_ability_player)
        self.ability_player.pack(side="left", padx=4, pady=4)

        acols = ("ability", "damage", "pct", "hits", "crits", "maxhit")
        aheads = ("Способность", "Урон", "Доля", "Ударов", "Критов", "Макс. удар")
        awidths = (340, 140, 90, 100, 100, 130)
        self.tree_ab = self._make_tree(tab_ab, acols, aheads, awidths)

        # --- инфо ---
        self.info_box = ctk.CTkTextbox(tab_info, wrap="word")
        self.info_box.pack(fill="both", expand=True)
        self.info_box.insert("1.0", self._info_text())
        self.info_box.configure(state="disabled")

        # ---- нижняя панель ----
        bottom = ctk.CTkFrame(self)
        bottom.pack(fill="x", **pad)
        ctk.CTkButton(bottom, text="Экспорт в Excel",
                      command=self._export_xlsx).pack(side="left", padx=8, pady=6)
        ctk.CTkButton(bottom, text="Экспорт заходов в CSV",
                      command=self._export_runs_csv).pack(side="left", padx=8, pady=6)
        ctk.CTkButton(bottom, text="Справка", command=self._show_help).pack(
            side="right", padx=8, pady=6)

        self.bind("<F5>", lambda _e: self._start_parse())
        if Path(self.log_path.get()).exists():
            self.after(400, self._start_parse)

    # ------------------------------------------------------------------
    def _setup_tree_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview",
                        background="#2b2b2b", fieldbackground="#2b2b2b",
                        foreground="#e6e6e6", rowheight=24, borderwidth=0)
        style.configure("Treeview.Heading",
                        background="#1f1f1f", foreground="#ffffff",
                        relief="flat", padding=4)
        style.map("Treeview", background=[("selected", "#1f6aa5")],
                  foreground=[("selected", "#ffffff")])

    def _make_tree(self, parent, cols, heads, widths) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=cols, show="headings")
        for c, h, w in zip(cols, heads, widths):
            tree.heading(c, text=h)
            anchor = "w" if c in ("name", "class", "spec", "ability") else "center"
            tree.column(c, width=w, anchor=anchor, stretch=(c in ("name", "ability")))
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
            "1. Включи запись боевого журнала — один раз в игре:\n"
            "      /combatlog\n\n"
            "2. Включи расширенное логирование (нужно для классов и характеристик):\n"
            "      /console advancedCombatLogging 1\n\n"
            "3. Пройди подземелье (ключ, рейд, обычный инст).\n\n"
            "4. В приложении нажми «Загрузить». Файл журнала:\n"
            "      <папка игры>\\Logs\\WoWCombatLog.txt\n"
            "   Читать можно, пока игра запущена.\n\n"
            "Что показывается\n"
            "================\n"
            "• Заходы: дата, подземелье, уровень ключа, результат (+1/+2/+3),\n"
            "  время прохождения и таймер подземелья.\n"
            "• Группа: кто был в группе, класс и специализация, урон, ДПС,\n"
            "  доля от общего урона, исцеление, прерывания, смерти.\n"
            "• Характеристики: уровень предметов, сила, ловкость, выносливость,\n"
            "  интеллект, броня, крит, скорость, искусность, универсальность.\n"
            "• Способности: разбивка урона выбранного игрока по способностям.\n\n"
            "Про характеристики\n"
            "===================\n"
            "Класс, специализация и статы берутся из события COMBATANT_INFO.\n"
            "Оно пишется только при включённом расширенном логировании\n"
            "(пункт 2 выше). Если статы пустые — включи его и пройди заход заново.\n\n"
            "Про бонус ключа\n"
            "================\n"
            "Бонус считается по таймеру подземелья: уложился в 100% времени — +1,\n"
            "в 80% — +2, в 60% — +3. Таймеры стандартных подземелий BfA уже вбиты;\n"
            "если подземелье неизвестно, бонус показывается как «—»."
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

    # ------------------------------------------------------------------
    def _start_parse(self) -> None:
        path = self.log_path.get().strip()
        if not path or not Path(path).exists():
            messagebox.showerror("Файл не найден",
                                 "Не найден файл журнала:\n" + (path or "(пусто)"))
            return
        self.btn_load.configure(state="disabled")
        self.progress.set(0)
        self.status.configure(text="Чтение лога...")
        threading.Thread(target=self._parse_worker, args=(path,), daemon=True).start()

    def _parse_worker(self, path: str) -> None:
        try:
            parser = CombatLogParser(
                progress_cb=lambda v: self.after(0, lambda: self.progress.set(v)))
            res = parser.parse(path)
            self.after(0, lambda: self._parse_done(res))
        except Exception:
            err = traceback.format_exc()
            self.after(0, lambda: self._parse_failed(err))

    def _parse_failed(self, err: str) -> None:
        self.btn_load.configure(state="normal")
        self.status.configure(text="Ошибка чтения")
        messagebox.showerror("Ошибка", err)

    def _parse_done(self, res: ParseResult) -> None:
        self.result = res
        self.btn_load.configure(state="normal")
        self.progress.set(1)

        keys = sum(1 for r in res.runs if r.key_level)
        fmt = "расширенный" if res.advanced else "обычный"
        cinfo = "есть" if res.has_combatant_info else "нет"
        self.status.configure(
            text=f"Заходов: {len(res.runs)} (ключи: {keys})  |  формат: {fmt}  |  "
                 f"COMBATANT_INFO: {cinfo}  |  строк: {fmt_int(res.lines_total)}")

        if res.has_combatant_info:
            self.stats_note.configure(text="")
        else:
            self.stats_note.configure(
                text="Характеристики недоступны: включи в игре "
                     "/console advancedCombatLogging 1 и пройди заход заново.")

        self._refresh_runs()

    # ------------------------------------------------------------------
    def _refresh_runs(self) -> None:
        for item in self.tree_runs.get_children():
            self.tree_runs.delete(item)
        if self.result is None:
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

        for run in runs:
            self.tree_runs.insert(
                "", "end", iid=str(id(run)),
                values=(
                    fmt_date(run.start),
                    run.zone,
                    f"+{run.key_level}" if run.key_level else "",
                    run.bonus,
                    fmt_duration(run.duration) if (run.key_time_ms or run.end) else "",
                    fmt_duration(run.timer_limit) if run.timer_limit else "",
                    run.group_size or len(run.players),
                    fmt_int(run.total_damage),
                ))

        if runs:
            first = str(id(runs[0]))
            self.tree_runs.selection_set(first)
            self.tree_runs.focus(first)
            self._on_run_select()
        else:
            self.current_run = None
            self._clear_details()

    # ------------------------------------------------------------------
    def _clear_details(self) -> None:
        for tree in (self.tree_group, self.tree_stats, self.tree_ab):
            for item in tree.get_children():
                tree.delete(item)
        self.ability_player.configure(values=["—"])
        self.ability_player.set("—")

    def _on_run_select(self, _event=None) -> None:
        sel = self.tree_runs.selection()
        if not sel:
            return
        run = next((r for r in self.runs if str(id(r)) == sel[0]), None)
        if run is None:
            return
        self.current_run = run

        # --- группа ---
        for item in self.tree_group.get_children():
            self.tree_group.delete(item)
        total = run.total_damage or 1
        players = sorted(run.players.values(), key=lambda p: p.damage, reverse=True)
        for p in players:
            share = 100.0 * p.damage / total if run.total_damage else 0.0
            self.tree_group.insert("", "end", values=(
                p.name or p.guid,
                p.cls or "—",
                p.spec or "",
                fmt_int(p.damage),
                fmt_int(p.dps),
                f"{share:.1f} %",
                fmt_int(p.healing),
                fmt_int(p.interrupts),
                fmt_int(p.deaths),
            ), tags=(self._class_tag(p.cls),))

        # --- характеристики ---
        for item in self.tree_stats.get_children():
            self.tree_stats.delete(item)
        for p in players:
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
            ), tags=(self._class_tag(p.cls),))

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

    def _class_tag(self, cls: str) -> str:
        tag = f"cls_{cls}" if cls else "cls_none"
        color = CLASS_COLORS.get(cls)
        if color:
            try:
                self.tree_group.tag_configure(tag, foreground=color)
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
        for a in sorted(player.abilities.values(), key=lambda x: x.damage, reverse=True):
            pct = 100.0 * a.damage / total if player.damage else 0.0
            self.tree_ab.insert("", "end", values=(
                a.name, fmt_int(a.damage), f"{pct:.1f} %",
                fmt_int(a.hits), fmt_int(a.crits), fmt_int(a.max_hit)))

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
        self.status.configure(text=f"Сохранено: {path}")

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
            from openpyxl.styles import Font
        except ImportError:
            messagebox.showerror("Нет openpyxl", "Установи openpyxl: pip install openpyxl")
            return

        bold = Font(bold=True)
        wb = Workbook()

        ws = wb.active
        ws.title = "Заходы"
        ws.append(["Дата", "Подземелье", "Ключ", "Результат", "Время (с)",
                   "Таймер (с)", "Группа", "Урон группы", "Исцеление группы"])
        for c in ws[1]:
            c.font = bold
        for r in self.runs:
            ws.append([
                r.start.strftime("%d.%m.%Y %H:%M:%S") if r.start else "",
                r.zone, r.key_level or "", r.bonus,
                int(r.duration.total_seconds()), r.timer_limit or "",
                r.group_size or len(r.players), r.total_damage, r.total_healing,
            ])
        for col, width in zip("ABCDEFGHI", (18, 32, 8, 12, 12, 12, 10, 16, 18)):
            ws.column_dimensions[col].width = width

        ws2 = wb.create_sheet("Группа")
        ws2.append(["Дата", "Подземелье", "Ключ", "Игрок", "Класс", "Специализация",
                    "Урон", "ДПС", "Доля (%)", "Исцеление", "Прерывания", "Смерти"])
        for c in ws2[1]:
            c.font = bold
        for r in self.runs:
            total = r.total_damage or 1
            for p in sorted(r.players.values(), key=lambda x: x.damage, reverse=True):
                ws2.append([
                    r.start.strftime("%d.%m.%Y %H:%M") if r.start else "", r.zone,
                    r.key_level or "", p.name or p.guid, p.cls, p.spec,
                    p.damage, round(p.dps), round(100.0 * p.damage / total, 1),
                    p.healing, p.interrupts, p.deaths,
                ])
        for col, width in zip("ABCDEFGHIJKL", (16, 30, 8, 18, 18, 20, 14, 10, 10, 14, 12, 10)):
            ws2.column_dimensions[col].width = width

        ws3 = wb.create_sheet("Характеристики")
        ws3.append(["Дата", "Подземелье", "Игрок", "Класс", "Специализация", "iLvl",
                    "Сила", "Ловкость", "Выносливость", "Интеллект", "Броня",
                    "Крит", "Скорость", "Искусность", "Универсальность"])
        for c in ws3[1]:
            c.font = bold
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
        for col, width in zip("ABCDEFGHIJKLMNO",
                              (16, 30, 18, 18, 20, 8, 10, 10, 13, 12, 10, 10, 11, 12, 15)):
            ws3.column_dimensions[col].width = width

        ws4 = wb.create_sheet("Способности")
        ws4.append(["Дата", "Подземелье", "Ключ", "Игрок", "Способность", "Урон",
                    "Доля (%)", "Ударов", "Критов", "Макс. удар"])
        for c in ws4[1]:
            c.font = bold
        for r in self.runs:
            for p in r.players.values():
                total = p.damage or 1
                for a in sorted(p.abilities.values(), key=lambda x: x.damage, reverse=True):
                    ws4.append([
                        r.start.strftime("%d.%m.%Y %H:%M") if r.start else "", r.zone,
                        r.key_level or "", p.name or p.guid, a.name, a.damage,
                        round(100.0 * a.damage / total, 1), a.hits, a.crits, a.max_hit,
                    ])
        for col, width in zip("ABCDEFGHIJ", (16, 30, 8, 18, 34, 14, 10, 10, 10, 13)):
            ws4.column_dimensions[col].width = width

        wb.save(path)
        self.status.configure(text=f"Сохранено: {path}")


def run_gui(default_log: str | None = None) -> None:
    app = DamageApp(default_log=default_log)
    app.mainloop()


if __name__ == "__main__":
    run_gui()
