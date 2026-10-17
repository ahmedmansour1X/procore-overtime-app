#!/usr/bin/env python3
"""Procore Overtime - desktop overtime tracker (Python + Tkinter, no extra installs).

Your log is saved automatically to:  <your home folder>/ProcoreOvertime/overtime_data.json
(with a backup copy next to it) after every change.
"""
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

QUEUES = ["Phones", "Chats", "Email Cases"]
APP_DIR = Path.home() / "ProcoreOvertime"
DATA_FILE = APP_DIR / "overtime_data.json"
BACKUP_FILE = APP_DIR / "overtime_data.backup.json"
DEFAULTS = {"name": "", "email": "", "rate": "", "mult": "1",
            "entries": [], "trash": [], "timer": None}
TEAL, CREAM, GOLD, INK, FIELD = "#0f5d54", "#f4f0e0", "#d8a73e", "#22302d", "#fbf9ef"


# ---------- pure helpers (no UI) ----------
def parse_time(s):
    """'22:46', '10.46 pm', '10:46PM' -> 'HH:MM' (24h) or None."""
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*([ap]m)?\s*$", s or "", re.I)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if mi > 59:
        return None
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap == "pm" else 0)
    elif h > 23:
        return None
    return f"{h:02d}:{mi:02d}"


def parse_date(s):
    """'2026-10-17' or '10/17/2026' -> ISO date string, or None."""
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def entry_seconds(start, end):
    a = int(start[:2]) * 60 + int(start[3:])
    b = int(end[:2]) * 60 + int(end[3:])
    diff = (b - a) * 60
    return diff if diff > 0 else diff + 86400  # overnight shift


def fmt_num(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


def fmt_dur(sec):
    t = round(sec / 60)
    return f"{t // 60}h {t % 60:02d}m"


def fmt12(hm):
    h, m = int(hm[:2]), int(hm[3:])
    return f"{h % 12 or 12}.{m:02d} {'Pm' if h >= 12 else 'Am'}"


def mdy(iso):
    y, m, d = iso.split("-")
    return f"{m}/{d}/{y}"


def pay_for(sec, rate, mult):
    return sec / 3600 * rate * mult


def money(v):
    return f"{v:,.2f} EGP"


def log_line(e, rate, mult):
    """10/17/2026 - 2 hours - 10.46 Pm to 12.46 Am - Phones = 600"""
    h = fmt_num(e["sec"] / 3600)
    unit = "hour" if h == "1" else "hours"
    return (f"{mdy(e['date'])} - {h} {unit} - {fmt12(e['start'])} to "
            f"{fmt12(e['end'])} - {e['queue']} = {fmt_num(pay_for(e['sec'], rate, mult))}")


def sort_key(e):
    return (e["date"], e["start"])


# ---------- storage ----------
def load_data():
    for path in (DATA_FILE, BACKUP_FILE):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                out = {**DEFAULTS, **d}
                out["entries"] = list(out["entries"] or [])
                out["trash"] = list(out["trash"] or [])
                return out
        except (OSError, ValueError):
            continue
    return json.loads(json.dumps(DEFAULTS))


def save_data(data):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if DATA_FILE.exists():
        try:
            shutil.copy2(DATA_FILE, BACKUP_FILE)
        except OSError:
            pass
    tmp = DATA_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)  # atomic: never leaves a half-written file


# ---------- app ----------
class App:
    def __init__(self, root):
        self.root = root
        self.data = load_data()
        root.title("Procore Overtime")
        root.geometry("1120x720")
        root.minsize(920, 600)
        root.configure(bg=TEAL)
        self.style()
        self.status = tk.StringVar(value=f"Saved automatically to {DATA_FILE}")
        self.build()
        self.refresh()
        self.tick()
        root.protocol("WM_DELETE_WINDOW", self.close)

    # -- styling
    def style(self):
        s = ttk.Style()
        s.theme_use("clam")
        s.configure(".", font=("Segoe UI", 10))
        s.configure("Card.TFrame", background=CREAM)
        s.configure("Card.TLabel", background=CREAM, foreground=INK)
        s.configure("Big.TLabel", background=CREAM, foreground=INK, font=("Consolas", 26, "bold"))
        s.configure("Pay.TLabel", background=CREAM, foreground=TEAL, font=("Segoe UI", 14, "bold"))
        s.configure("Card.TLabelframe", background=CREAM, bordercolor=GOLD, relief="solid", borderwidth=1)
        s.configure("Card.TLabelframe.Label", background=CREAM, foreground=INK, font=("Segoe UI", 11, "bold"))
        s.configure("Gold.TButton", background=GOLD, foreground="#4a3406", font=("Segoe UI", 10, "bold"))
        s.map("Gold.TButton", background=[("active", "#e6b84f")])
        s.configure("Stop.TButton", background="#b5473a", foreground="white", font=("Segoe UI", 10, "bold"))
        s.map("Stop.TButton", background=[("active", "#d9534f")])
        s.configure("Treeview", rowheight=28, fieldbackground=FIELD, background=FIELD, foreground=INK)
        s.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        s.map("Treeview", background=[("selected", TEAL)], foreground=[("selected", "white")])
        s.configure("TEntry", fieldbackground=FIELD)
        s.configure("TCombobox", fieldbackground=FIELD)
        s.configure("Panedwindow", background=TEAL)

    def card(self, parent, title):
        lf = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=12)
        lf.pack(fill="x", expand=True, pady=(0, 10))
        lf.columnconfigure(1, weight=1)
        return lf

    def field(self, parent, label, row, var=None):
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=3)
        e = ttk.Entry(parent, textvariable=var)
        e.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=3)
        return e

    # -- layout
    def build(self):
        # Interactive Resizable Splitter
        paned = ttk.PanedWindow(self.root, orient="horizontal")
        paned.pack(fill="both", expand=True, padx=10, pady=10)

        # Left Panel (Cards) with Scroll Support
        left_container = tk.Frame(paned, bg=TEAL)
        paned.add(left_container, weight=1)

        canvas = tk.Canvas(left_container, bg=TEAL, highlightthickness=0)
        v_scroll = ttk.Scrollbar(left_container, orient="vertical", command=canvas.yview)
        left = tk.Frame(canvas, bg=TEAL)

        left.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=left, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.configure(yscrollcommand=v_scroll.set)

        # Mousewheel scroll binding for left panel
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", lambda e: _on_mousewheel(e) if str(e.widget).startswith(str(left_container)) else None)

        canvas.pack(side="left", fill="both", expand=True)
        v_scroll.pack(side="right", fill="y")

        # Right Panel (Log Table & Controls)
        right = tk.Frame(paned, bg=TEAL)
        paned.add(right, weight=3)

        # 1. Pay Settings Card
        c = self.card(left, "Pay settings")
        self.v_name = tk.StringVar(value=self.data["name"])
        self.v_email = tk.StringVar(value=self.data["email"])
        self.v_rate = tk.StringVar(value=str(self.data["rate"]))
        self.v_mult = tk.StringVar(value=str(self.data["mult"]))
        self.field(c, "Name", 0, self.v_name)
        self.field(c, "Email", 1, self.v_email)
        self.field(c, "Rate (EGP/h)", 2, self.v_rate)
        self.field(c, "Multiplier", 3, self.v_mult)
        for v in (self.v_name, self.v_email, self.v_rate, self.v_mult):
            v.trace_add("write", lambda *a: self.on_settings())

        # 2. Live Timer Card
        c = self.card(left, "Live timer")
        self.clock = ttk.Label(c, text="00:00:00", style="Big.TLabel", anchor="center")
        self.clock.pack(fill="x", pady=(2, 6))
        row = ttk.Frame(c, style="Card.TFrame")
        row.pack(fill="x", pady=(2, 0))
        self.t_queue = ttk.Combobox(row, values=QUEUES, state="readonly", width=14)
        self.t_queue.set(QUEUES[0])
        self.t_queue.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.t_btn = ttk.Button(row, text="▶ Start", command=self.toggle_timer, style="Gold.TButton")
        self.t_btn.pack(side="right")

        # 3. Add Overtime Manually Card
        c = self.card(left, "Add overtime manually")
        self.m_date = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        self.m_start, self.m_end, self.m_note = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.field(c, "Date (YYYY-MM-DD)", 0, self.m_date)
        ttk.Label(c, text="Queue", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=3)
        self.m_queue = ttk.Combobox(c, values=QUEUES, state="readonly")
        self.m_queue.set(QUEUES[0])
        self.m_queue.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=3)
        self.field(c, "Start (e.g. 22:46)", 2, self.m_start)
        self.field(c, "End (e.g. 00:46)", 3, self.m_end)
        self.field(c, "Note", 4, self.m_note)
        ttk.Button(c, text="Add entry", command=self.add_manual, style="Gold.TButton").grid(
            row=5, column=0, columnspan=2, sticky="e", pady=(8, 0))

        # 4. Summary Card
        c = self.card(left, "Summary")
        self.s_time = ttk.Label(c, text="", style="Card.TLabel")
        self.s_time.pack(anchor="w")
        self.s_pay = ttk.Label(c, text="", style="Pay.TLabel")
        self.s_pay.pack(anchor="w", pady=(2, 4))
        self.s_q = ttk.Label(c, text="", style="Card.TLabel", justify="left")
        self.s_q.pack(anchor="w")

        # Right Side - Overtime Log
        box = ttk.LabelFrame(right, text="Overtime log", style="Card.TLabelframe", padding=10)
        box.pack(fill="both", expand=True)
        cols = ("date", "queue", "time", "dur", "pay", "note")
        self.tree = ttk.Treeview(box, columns=cols, show="headings", selectmode="browse")
        for k, t, w in (("date", "Date", 90), ("queue", "Queue", 100), ("time", "Time", 170),
                        ("dur", "Duration", 85), ("pay", "Pay", 115), ("note", "Note", 200)):
            self.tree.heading(k, text=t)
            self.tree.column(k, width=w, stretch=(k == "note"))
        sb = ttk.Scrollbar(box, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.edit_selected())
        self.tree.bind("<Delete>", lambda e: self.delete_selected())

        # Action Buttons Toolbar
        bar = tk.Frame(right, bg=TEAL)
        bar.pack(fill="x", pady=(10, 0))
        for text, cmd, st in (("📋 Copy log", self.copy_log, "Gold.TButton"),
                              ("✏️ Edit", self.edit_selected, "TButton"),
                              ("🗑️ Delete", self.delete_selected, "TButton"),
                              ("♻️ Recently deleted", self.show_trash, "TButton"),
                              ("💾 Export CSV", self.export_csv, "TButton"),
                              ("✉️ Email my log", self.email_log, "TButton"),
                              ("📁 Data folder", self.open_folder, "TButton")):
            ttk.Button(bar, text=text, command=cmd, style=st).pack(side="left", padx=(0, 6))

        tk.Label(right, textvariable=self.status, bg=TEAL, fg="#bfe3dd", anchor="w").pack(fill="x", pady=(8, 0))

    # -- data / state
    def rate(self):
        try:
            return float(self.v_rate.get())
        except ValueError:
            return 0.0

    def mult(self):
        try:
            return float(self.v_mult.get())
        except ValueError:
            return 1.0

    def say(self, msg):
        self.status.set(msg)

    def persist(self):
        try:
            save_data(self.data)
        except OSError as e:
            messagebox.showerror("Could not save", f"Your log could not be saved:\n{e}")

    def on_settings(self):
        self.data.update(name=self.v_name.get(), email=self.v_email.get().strip(),
                         rate=self.v_rate.get(), mult=self.v_mult.get())
        self.persist()
        self.refresh()

    def entries_sorted(self, newest_first=True):
        return sorted(self.data["entries"], key=sort_key, reverse=newest_first)

    def refresh(self):
        sel = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        r, m = self.rate(), self.mult()
        tot, by = 0, {q: 0 for q in QUEUES}
        for e in self.entries_sorted():
            self.tree.insert("", "end", iid=e["id"], values=(
                mdy(e["date"]), e["queue"], f"{fmt12(e['start'])} → {fmt12(e['end'])}",
                fmt_dur(e["sec"]), money(pay_for(e["sec"], r, m)), e.get("note", "")))
            tot += e["sec"]
            by[e["queue"]] = by.get(e["queue"], 0) + e["sec"]
        if sel and self.tree.exists(sel[0]):
            self.tree.selection_set(sel[0])
        self.s_time.config(text=f"Total overtime: {fmt_dur(tot)}  ({tot / 3600:.2f} h)")
        self.s_pay.config(text=f"To be paid: {money(pay_for(tot, r, m))}")
        self.s_q.config(text="\n".join(
            f"{q}: {fmt_dur(s)} · {money(pay_for(s, r, m))}" for q, s in by.items()))

    def selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Select an entry", "Click an entry in the log first.")
            return None
        return next((e for e in self.data["entries"] if e["id"] == sel[0]), None)

    # -- timer
    def tick(self):
        t = self.data.get("timer")
        if t:
            s = int(time.time() - t["start"])
            self.clock.config(text=f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")
        else:
            self.clock.config(text="00:00:00")
        self.root.after(500, self.tick)

    def sync_timer_ui(self):
        running = bool(self.data.get("timer"))
        self.t_btn.config(text="■ Stop & save" if running else "▶ Start",
                          style="Stop.TButton" if running else "Gold.TButton")
        self.t_queue.config(state="disabled" if running else "readonly")
        if running:
            self.t_queue.set(self.data["timer"]["queue"])

    def toggle_timer(self):
        t = self.data.get("timer")
        if not t:
            self.data["timer"] = {"start": time.time(), "queue": self.t_queue.get()}
            self.say("Timer started.")
        else:
            start = datetime.fromtimestamp(t["start"])
            sec = max(1, round(time.time() - t["start"]))
            self.data["entries"].append({
                "id": uuid.uuid4().hex[:10], "date": start.strftime("%Y-%m-%d"),
                "queue": t["queue"], "start": start.strftime("%H:%M"),
                "end": datetime.now().strftime("%H:%M"), "sec": sec, "note": ""})
            self.data["timer"] = None
            self.say(f"Saved {fmt_dur(sec)} on {t['queue']}.")
        self.persist()
        self.sync_timer_ui()
        self.refresh()

    # -- add / edit / delete / restore
    def validate(self, d, s, e):
        d2, s2, e2 = parse_date(d), parse_time(s), parse_time(e)
        if not (d2 and s2 and e2):
            messagebox.showwarning("Check the entry",
                                   "Use a date like 2026-10-17 and times like 22:46 or 10:46 PM.")
            return None
        return d2, s2, e2

    def add_manual(self):
        v = self.validate(self.m_date.get(), self.m_start.get(), self.m_end.get())
        if not v:
            return
        d, s, e = v
        self.data["entries"].append({"id": uuid.uuid4().hex[:10], "date": d, "queue": self.m_queue.get(),
                                     "start": s, "end": e, "sec": entry_seconds(s, e),
                                     "note": self.m_note.get().strip()})
        self.m_start.set("")
        self.m_end.set("")
        self.m_note.set("")
        self.persist()
        self.refresh()
        self.say("Entry added.")

    def edit_selected(self):
        e = self.selected()
        if not e:
            return
        win = tk.Toplevel(self.root)
        win.title("Edit entry")
        win.configure(bg=CREAM)
        win.transient(self.root)
        win.grab_set()
        f = ttk.Frame(win, style="Card.TFrame", padding=14)
        f.pack()
        vd, vs, ve, vn = (tk.StringVar(value=e["date"]), tk.StringVar(value=e["start"]),
                          tk.StringVar(value=e["end"]), tk.StringVar(value=e.get("note", "")))
        self.field(f, "Date (YYYY-MM-DD)", 0, vd)
        ttk.Label(f, text="Queue", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=3)
        q = ttk.Combobox(f, values=QUEUES, state="readonly", width=16)
        q.set(e["queue"])
        q.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=3)
        self.field(f, "Start", 2, vs)
        self.field(f, "End", 3, ve)
        self.field(f, "Note", 4, vn)

        def save():
            v = self.validate(vd.get(), vs.get(), ve.get())
            if not v:
                return
            e.update(date=v[0], start=v[1], end=v[2], queue=q.get(),
                     sec=entry_seconds(v[1], v[2]), note=vn.get().strip())
            self.persist()
            self.refresh()
            self.say("Entry updated.")
            win.destroy()

        row = ttk.Frame(f, style="Card.TFrame")
        row.grid(row=5, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(row, text="Cancel", command=win.destroy).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Save changes", command=save, style="Gold.TButton").pack(side="left")

    def delete_selected(self):
        e = self.selected()
        if not e:
            return
        self.data["entries"].remove(e)
        e["deletedAt"] = time.time()
        self.data["trash"].insert(0, e)
        self.persist()
        self.refresh()
        self.say("Moved to Recently deleted. You can restore it from there.")

    def show_trash(self):
        win = tk.Toplevel(self.root)
        win.title("Recently deleted")
        win.configure(bg=CREAM)
        win.geometry("620x360")
        win.transient(self.root)
        tree = ttk.Treeview(win, columns=("d", "q", "t", "p"), show="headings", selectmode="browse")
        for k, t, w in (("d", "Date", 90), ("q", "Queue", 90), ("t", "Time", 190), ("p", "Pay", 110)):
            tree.heading(k, text=t)
            tree.column(k, width=w)
        tree.pack(fill="both", expand=True, padx=10, pady=10)

        def fill():
            tree.delete(*tree.get_children())
            for e in self.data["trash"]:
                tree.insert("", "end", iid=e["id"], values=(
                    mdy(e["date"]), e["queue"], f"{fmt12(e['start'])} → {fmt12(e['end'])}",
                    money(pay_for(e["sec"], self.rate(), self.mult()))))

        def pick():
            sel = tree.selection()
            return next((e for e in self.data["trash"] if sel and e["id"] == sel[0]), None)

        def restore(entry=None, all_=False):
            items = list(self.data["trash"]) if all_ else ([entry] if entry else [])
            for e in items:
                e.pop("deletedAt", None)
                self.data["trash"].remove(e)
                self.data["entries"].append(e)
            if items:
                self.persist()
                self.refresh()
                fill()
                self.say(f"Restored {len(items)} entr{'y' if len(items) == 1 else 'ies'}.")

        def forever():
            e = pick()
            if e and messagebox.askyesno("Delete forever", "Permanently delete this entry?", parent=win):
                self.data["trash"].remove(e)
                self.persist()
                fill()

        bar = tk.Frame(win, bg=CREAM)
        bar.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bar, text="Restore", style="Gold.TButton", command=lambda: restore(pick())).pack(side="left")
        ttk.Button(bar, text="Restore all", command=lambda: restore(all_=True)).pack(side="left", padx=6)
        ttk.Button(bar, text="Delete forever", command=forever).pack(side="left")
        ttk.Button(bar, text="Close", command=win.destroy).pack(side="right")
        fill()

    # -- output
    def log_text(self):
        r, m = self.rate(), self.mult()
        return "\n".join(log_line(e, r, m) for e in self.entries_sorted(newest_first=False))

    def copy_log(self):
        if not self.data["entries"]:
            self.say("Nothing to copy yet.")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.log_text())
        self.root.update()
        self.say(f"Log copied ({len(self.data['entries'])} entries).")

    def export_csv(self):
        if not self.data["entries"]:
            self.say("Nothing to export yet.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV file", "*.csv")],
            initialfile=f"procore-overtime-{datetime.now():%Y-%m-%d}.csv")
        if not path:
            return
        r, m, name = self.rate(), self.mult(), self.v_name.get()
        tot = 0
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["Name", "Date", "Queue", "Start", "End", "Duration (h:mm)",
                            "Decimal hours", "Rate (EGP/h)", "Multiplier", "Pay (EGP)", "Note"])
                for e in self.entries_sorted(newest_first=False):
                    t = round(e["sec"] / 60)
                    tot += e["sec"]
                    w.writerow([name, e["date"], e["queue"], e["start"], e["end"], f"{t // 60}:{t % 60:02d}",
                                f"{e['sec'] / 3600:.2f}", r, m, f"{pay_for(e['sec'], r, m):.2f}", e.get("note", "")])
                t = round(tot / 60)
                w.writerow([name, "TOTAL", "", "", "", f"{t // 60}:{t % 60:02d}", f"{tot / 3600:.2f}",
                            r, m, f"{pay_for(tot, r, m):.2f}", ""])
        except OSError as e:
            messagebox.showerror("Could not export", str(e))
            return
        self.say(f"Exported to {path}")

    def email_log(self):
        if not self.data["entries"]:
            self.say("Nothing to email yet.")
            return
        to = self.v_email.get().strip()
        if not re.match(r"^\S+@\S+\.\S+$", to):
            messagebox.showwarning("Email address", "Enter a valid email address first.")
            return
        r, m = self.rate(), self.mult()
        tot = sum(e["sec"] for e in self.data["entries"])
        head = (f"Procore Overtime log{' - ' + self.v_name.get() if self.v_name.get() else ''}\n"
                f"Total: {fmt_dur(tot)} ({tot / 3600:.2f} h) = {money(pay_for(tot, r, m))}\n\n")
        body = head + "\n".join(log_line(e, r, m) for e in self.entries_sorted())
        if len(body) > 1500:
            body = body[:1500].rsplit("\n", 1)[0] + "\n... (shortened - use Export CSV for the full list)"
        self.copy_log()
        subject = quote(f"Procore Overtime log {datetime.now():%Y-%m-%d}")
        webbrowser.open(f"mailto:{to}?subject={subject}&body={quote(body)}")
        self.say("Opening your email app (the full log text is also on your clipboard).")

    def open_folder(self):
        APP_DIR.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(APP_DIR)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(APP_DIR)])
        else:
            subprocess.Popen(["xdg-open", str(APP_DIR)])

    def close(self):
        self.persist()
        self.root.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on Windows
    except Exception:
        pass
    root = tk.Tk()
    app = App(root)
    app.sync_timer_ui()
    root.mainloop()


if __name__ == "__main__":
    main()
