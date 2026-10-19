# -*- coding: utf-8 -*-
"""GUI приложения WoW Damage Parser (customtkinter + ttk.Treeview)."""
from __future__ import annotations

import csv
import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from wow_damage_app import CombatLogParser, DEFAULT_LOG_PATHS, ParseResult, Run


def fmt_int(v) -> str:
    try:
        return f"{int(v):,}".replace(",", " ")
    except Exception:
        return str(v)


def fmt_duration(td) -> str:
    total = int(td.total_seconds()) if td else 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class DamageApp(ctk.CTk):
    def __init__(self, default_log: str | None = None):
        super().__init__()
        self.title("WoW Damage Parser — урон из combat log")
        self.geometry("1180x720")
        self.minsize(980, 600)

        self.result: ParseResult | None = None
        self.current_run: Run | None = None
        self.log_path = ctk.StringVar(value=self._guess_log_path(default_log))

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

        pad = {"padx": 8, "pady": 6}

        # ---- Верхняя панель: путь к логу ----
        top = ctk.CTkFrame(self)
        top.pack(fill="x", **pad)

        ctk.CTkLabel(top, text="Combat log:").pack(side="left", padx=(10, 4))
        ctk.CTkEntry(top, textvariable=self.log_path).pack(
            side="left", fill="x", expand=True, padx=4)
        ctk.CTkButton(top, text="Обзор", width=90,
                      command=self._browse).pack(side="left", padx=4)
        self.btn_load = ctk.CTkButton(top, text="Загрузить", width=110,
                                      command=self._start_parse)
        self.btn_load.pack(side="left", padx=(4, 10))

        # ---- Панель фильтров ----
        filt = ctk.CTkFrame(self)
        filt.pack(fill="x", **pad)

        ctk.CTkLabel(filt, text="Персонаж:").pack(side="left", padx=(10, 4))
        self.player_box = ctk.CTkComboBox(filt, values=["—"], width=220,
                                          command=self._on_player_change)
        self.player_box.pack(side="left", padx=4)

        self.pets_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(filt, text="Учитывать питомцев и тотемы",
                        variable=self.pets_var).pack(side="left", padx=12)

        self.status = ctk.CTkLabel(filt, text="Готов к работе")
        self.status.pack(side="right", padx=12)

        self.progress = ctk.CTkProgressBar(filt, width=180)
        self.progress.set(0)
        self.progress.pack(side="right", padx=8)

        # ---- Вкладки ----
        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, **pad)

        tab_runs = self.tabs.add("Заходы")
        tab_ab = self.tabs.add("Способности")
        tab_info = self.tabs.add("Инфо")

        # --- таблица заходов ---
        cols = ("zone", "start", "duration", "damage", "dps", "share", "active")
        heads = ("Зона", "Начало", "Длительность", "Урон", "DPS", "Доля группы", "Активное время")
        widths = (260, 140, 110, 130, 100, 110, 130)
        self.tree_runs = ttk.Treeview(tab_runs, columns=cols, show="headings")
        for c, h, w in zip(cols, heads, widths):
            self.tree_runs.heading(c, text=h)
            self.tree_runs.column(c, width=w, anchor="center" if c != "zone" else "w")
        self.tree_runs.pack(fill="both", expand=True, side="left")
        self.tree_runs.bind("<<TreeviewSelect>>", self._on_run_select)
        sb1 = ttk.Scrollbar(tab_runs, orient="vertical", command=self.tree_runs.yview)
        self.tree_runs.configure(yscrollcommand=sb1.set)
        sb1.pack(side="right", fill="y")

        # --- таблица способностей ---
        cols2 = ("ability", "damage", "pct", "hits", "crits", "maxhit")
        heads2 = ("Способность", "Урон", "Доля", "Ударов", "Критов", "Макс. удар")
        widths2 = (320, 140, 90, 100, 100, 130)
        self.tree_ab = ttk.Treeview(tab_ab, columns=cols2, show="headings")
        for c, h, w in zip(cols2, heads2, widths2):
            self.tree_ab.heading(c, text=h)
            self.tree_ab.column(c, width=w, anchor="center" if c != "ability" else "w")
        self.tree_ab.pack(fill="both", expand=True, side="left")
        sb2 = ttk.Scrollbar(tab_ab, orient="vertical", command=self.tree_ab.yview)
        self.tree_ab.configure(yscrollcommand=sb2.set)
        sb2.pack(side="right", fill="y")

        # --- инфо ---
        self.info_box = ctk.CTkTextbox(tab_info, wrap="word")
        self.info_box.pack(fill="both", expand=True)
        self.info_box.insert("1.0", self._info_text())
        self.info_box.configure(state="disabled")

        # ---- Нижняя панель ----
        bottom = ctk.CTkFrame(self)
        bottom.pack(fill="x", **pad)
        ctk.CTkButton(bottom, text="Экспорт заходов в CSV", command=self._export_runs_csv).pack(
            side="left", padx=8, pady=6)
        ctk.CTkButton(bottom, text="Экспорт заходов в Excel", command=self._export_runs_xlsx).pack(
            side="left", padx=8, pady=6)
        ctk.CTkButton(bottom, text="Экспорт способностей в CSV",
                      command=self._export_abilities_csv).pack(side="left", padx=8, pady=6)
        ctk.CTkButton(bottom, text="Справка", command=self._show_help).pack(side="right", padx=8, pady=6)

    # ------------------------------------------------------------------
    def _info_text(self) -> str:
        return (
            "Как получить данные\n"
            "====================\n\n"
            "1. Зайди в игру и один раз введи в чат команду:\n"
            "      /combatlog\n"
            "   В ответ появится сообщение, что запись боевого журнала включена.\n\n"
            "2. Пройди подземелье (рейд, ключ) как обычно.\n\n"
            "3. Игра начнёт писать файл:\n"
            "      <папка игры>\\Logs\\WoWCombatLog.txt\n\n"
            "4. Выйди из игры (или просто нажми «Загрузить» в этом окне —\n"
            "   файл можно читать, пока игра запущена).\n\n"
            "5. Нажми «Загрузить» — приложение найдёт заходы и посчитает урон.\n\n"
            "Полезно знать\n"
            "=============\n"
            "• «Обзор» — выбрать файл лога вручную.\n"
            "• В выпадающем списке выбери своего персонажа, если он определился неверно.\n"
            "• «Активное время» — число секунд, в которые ты реально наносил урон.\n"
            "  DPS считается как урон / активное время.\n"
            "• «Доля группы» — твой урон относительно всей группы за заход.\n"
            "• Кнопки экспорта сохранят результаты в CSV или Excel.\n\n"
            "Файл лога растёт со временем. Если он станет очень большим,\n"
            "старые записи можно удалить — приложение читает файл целиком,\n"
            "но работает построчно и не требует много памяти."
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
            parser = CombatLogParser(include_pets=self.pets_var.get(),
                                     progress_cb=lambda v: self.after(
                                         0, lambda: self.progress.set(v)))
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

        names = sorted(set(res.players.values()))
        combo_values = ["— Авто —"] + names
        self.player_box.configure(values=combo_values)
        if res.player_name:
            self.player_box.set(res.player_name)
        else:
            self.player_box.set("— Авто —")

        self.status.configure(
            text=f"Заходов: {sum(1 for r in res.runs if r.damage > 0)}  |  "
                 f"строк: {res.lines_total}  |  "
                 f"{'advanced' if res.advanced else 'обычный'} формат")
        self._refresh_runs()

    # ------------------------------------------------------------------
    def _on_player_change(self, _value=None) -> None:
        if self.result is None:
            return
        name = self.player_box.get()
        if not name or name.startswith("—"):
            return
        # перечитать лог с фильтром по имени
        path = self.log_path.get().strip()
        self.status.configure(text=f"Пересчёт для {name}...")
        self.btn_load.configure(state="disabled")
        threading.Thread(target=self._reparse_worker, args=(path, name), daemon=True).start()

    def _reparse_worker(self, path: str, name: str) -> None:
        try:
            parser = CombatLogParser(player_filter=name,
                                     include_pets=self.pets_var.get())
            res = parser.parse(path)
            self.after(0, lambda: self._reparse_done(res))
        except Exception:
            err = traceback.format_exc()
            self.after(0, lambda: self._parse_failed(err))

    def _reparse_done(self, res: ParseResult) -> None:
        self.btn_load.configure(state="normal")
        self.result = res
        self.status.configure(text=f"Пересчитано: {res.player_name}")
        self._refresh_runs()

    # ------------------------------------------------------------------
    def _refresh_runs(self) -> None:
        for item in self.tree_runs.get_children():
            self.tree_runs.delete(item)
        if self.result is None:
            return
        self._runs_with_damage = [r for r in self.result.runs if r.damage > 0]
        for run in self._runs_with_damage:
            self.tree_runs.insert(
                "", "end", iid=str(id(run)),
                values=(run.zone, run.start.strftime("%d.%m %H:%M:%S") if run.start else "",
                        fmt_duration(run.duration), fmt_int(run.damage),
                        fmt_int(run.dps), f"{run.share:.1f} %", f"{run.active_time:.0f} c"))

    def _on_run_select(self, _event=None) -> None:
        sel = self.tree_runs.selection()
        if not sel or self.result is None:
            return
        run = next((r for r in self._runs_with_damage if str(id(r)) == sel[0]), None)
        if run is None:
            return
        self.current_run = run
        for item in self.tree_ab.get_children():
            self.tree_ab.delete(item)
        for a in sorted(run.abilities.values(), key=lambda x: x.damage, reverse=True):
            pct = 100.0 * a.damage / run.damage if run.damage else 0
            self.tree_ab.insert("", "end", values=(
                a.name, fmt_int(a.damage), f"{pct:.1f} %",
                fmt_int(a.hits), fmt_int(a.crits), fmt_int(a.max_hit)))
        self.tabs.set("Способности")

    # ------------------------------------------------------------------
    def _export_runs_csv(self) -> None:
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")],
                                            initialfile="wow_runs.csv")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["Зона", "Начало", "Длительность (с)", "Урон", "DPS",
                        "Активное время (с)", "Доля группы (%)"])
            for r in self._runs_with_damage:
                w.writerow([r.zone,
                            r.start.strftime("%d.%m.%Y %H:%M:%S") if r.start else "",
                            int(r.duration.total_seconds()), r.damage,
                            round(r.dps), round(r.active_time), round(r.share, 1)])
        self.status.configure(text=f"Сохранено: {path}")

    def _export_abilities_csv(self) -> None:
        if self.current_run is None:
            messagebox.showinfo("Нет данных", "Сначала выбери заход во вкладке «Заходы».")
            return
        path = filedialog.asksaveasfilename(defaultextension=".csv",
                                            filetypes=[("CSV", "*.csv")],
                                            initialfile="wow_abilities.csv")
        if not path:
            return
        run = self.current_run
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["Способность", "Урон", "Доля (%)", "Ударов", "Критов", "Макс. удар"])
            for a in sorted(run.abilities.values(), key=lambda x: x.damage, reverse=True):
                pct = 100.0 * a.damage / run.damage if run.damage else 0
                w.writerow([a.name, a.damage, round(pct, 1), a.hits, a.crits, a.max_hit])
        self.status.configure(text=f"Сохранено: {path}")

    def _export_runs_xlsx(self) -> None:
        if self.result is None:
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx",
                                            filetypes=[("Excel", "*.xlsx")],
                                            initialfile="wow_runs.xlsx")
        if not path:
            return
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font
        except ImportError:
            messagebox.showerror("Нет openpyxl", "Установи openpyxl: pip install openpyxl")
            return
        wb = Workbook()
        ws = wb.active
        ws.title = "Заходы"
        headers = ["Зона", "Начало", "Длительность (с)", "Урон", "DPS",
                   "Активное время (с)", "Доля группы (%)"]
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in self._runs_with_damage:
            ws.append([r.zone,
                       r.start.strftime("%d.%m.%Y %H:%M:%S") if r.start else "",
                       int(r.duration.total_seconds()), r.damage,
                       round(r.dps), round(r.active_time), round(r.share, 1)])
        for col, width in zip("ABCDEFG", (28, 20, 18, 14, 10, 18, 16)):
            ws.column_dimensions[col].width = width

        ws2 = wb.create_sheet("Способности")
        ws2.append(["Зона", "Способность", "Урон", "Доля (%)", "Ударов", "Критов", "Макс. удар"])
        for c in ws2[1]:
            c.font = Font(bold=True)
        for r in self._runs_with_damage:
            for a in sorted(r.abilities.values(), key=lambda x: x.damage, reverse=True):
                pct = 100.0 * a.damage / r.damage if r.damage else 0
                ws2.append([r.zone, a.name, a.damage, round(pct, 1), a.hits, a.crits, a.max_hit])
        for col, width in zip("ABCDEFG", (26, 34, 14, 10, 10, 10, 14)):
            ws2.column_dimensions[col].width = width

        wb.save(path)
        self.status.configure(text=f"Сохранено: {path}")


def run_gui(default_log: str | None = None) -> None:
    app = DamageApp(default_log=default_log)
    app.mainloop()


if __name__ == "__main__":
    run_gui()
