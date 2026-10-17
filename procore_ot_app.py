#!/usr/bin/env python3
"""Procore OT App - desktop overtime tracker (Python + Tkinter, no extra installs).

What's inside
  * Live timer, plus manual entries with a pop-up calendar and time picker
  * Overtime log with your name on every entry, search, and multi-select
    (Ctrl+click, Shift+click or Ctrl+A) so you can delete several entries at once
  * "Clear all" (restorable from Recently deleted), CSV export and copy log
  * A separate Overtime summary page (totals, by queue, by day and by month)
  * Resizable panels, zoom, collapsible cards, and Default, Dark or Custom themes
  * You choose the folder where your log is saved (View > Change save folder...)

Build a Windows app (no Python needed to run it):
    pyinstaller --onefile --noconsole --name "Procore OT App" procore_ot_app.py
"""
import calendar
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk
from tkinter import font as tkfont

APP_NAME = "Procore OT App"
QUEUES = ["Phones", "Chats", "Email Cases"]
DATA_NAME = "overtime_data.json"
BACKUP_NAME = "overtime_data.backup.json"
SETTINGS_NAME = "ProcoreOTApp.settings.json"
LEGACY_DIR = Path.home() / "ProcoreOvertime"  # where older versions saved the log


def app_base_dir():
    """Folder that contains the app (.exe when built, .py when run as a script)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APPDATA_DIR = Path(os.environ.get("APPDATA") or (Path.home() / ".config")) / "ProcoreOTApp"
# The tiny settings file only remembers which folder you picked. It's kept next
# to the app when possible, otherwise in AppData.
SETTINGS_CANDIDATES = [app_base_dir() / SETTINGS_NAME, APPDATA_DIR / SETTINGS_NAME]

BASE_KEYS = ("bg", "card_bg", "accent", "text", "field_bg")
PRESETS = {
    "default": {"bg": "#0f5d54", "card_bg": "#f4f0e0", "accent": "#d8a73e", "text": "#22302d",
                "field_bg": "#fffdf6", "header_bg": "#0a453e", "accent_fg": "#3d2a04",
                "select_bg": "#0f5d54"},
    "dark": {"bg": "#0b1220", "card_bg": "#151f32", "accent": "#38bdf8", "text": "#e2e8f0",
             "field_bg": "#0e1626", "header_bg": "#070c17", "select_bg": "#0369a1"},
}
CUSTOM_DEFAULT = {"bg": "#1e1e2e", "card_bg": "#2a2b3d", "accent": "#cba6f7", "text": "#cdd6f4",
                  "field_bg": "#181825"}
STARTER_PALETTES = [
    ("Teal & cream", PRESETS["default"]),
    ("Dark", PRESETS["dark"]),
    ("Purple night", CUSTOM_DEFAULT),
    ("Ocean", {"bg": "#0b3954", "card_bg": "#eef5f9", "accent": "#ff7a45", "text": "#10263a",
               "field_bg": "#ffffff"}),
    ("Forest", {"bg": "#14281d", "card_bg": "#1f3a2b", "accent": "#9ad36a", "text": "#e8f3e8",
                "field_bg": "#162b20"}),
    ("Rose", {"bg": "#3a1c2e", "card_bg": "#fff4f7", "accent": "#e0567f", "text": "#3a1c2e",
              "field_bg": "#ffffff"}),
    ("Graphite", {"bg": "#1c1c1e", "card_bg": "#2c2c2e", "accent": "#0a84ff", "text": "#f2f2f7",
                  "field_bg": "#1c1c1e"}),
]

DEFAULTS = {
    "name": "", "rate": "", "mult": "1",
    "entries": [], "trash": [], "timer": None,
    "ui_scale": 10, "theme": "default", "custom_theme": dict(CUSTOM_DEFAULT),
    "collapsed": {}, "sash": None, "geometry": "", "maximized": False, "page": "log",
}


# ---------- pure helpers (no UI) ----------
def parse_time(s):
    """'22:46', '10.46 pm', '10:46PM' -> 'HH:MM' (24h) or None."""
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*([ap]\.?m\.?)?\s*$", s or "", re.I)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower().replace(".", "")
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


def month_label(key):
    try:
        y, m = key.split("-")
        return f"{calendar.month_name[int(m)]} {y}"
    except (ValueError, IndexError):
        return key


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


def to_float(v, default):
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def clamp_int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


# ---------- colors ----------
def _rgb(h):
    h = str(h or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (128, 128, 128)


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def mix(a, b, w):
    """Blend colour a toward colour b by weight w (0..1)."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(x * (1 - w) + y * w for x, y in zip(ra, rb))


def lum(h):
    r, g, b = _rgb(h)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def readable_on(h):
    return "#111111" if lum(h) > 0.6 else "#ffffff"


def valid_hex(v):
    return isinstance(v, str) and re.match(r"^#[0-9a-fA-F]{6}$", v) is not None


def build_palette(base):
    """Turn 5 base colours (plus optional overrides) into a full UI palette."""
    b = dict(CUSTOM_DEFAULT)
    b.update({k: v for k, v in (base or {}).items() if valid_hex(v)})
    bg, card, acc, text, field = (b[k] for k in BASE_KEYS)
    p = {
        "bg": bg, "card_bg": card, "accent": acc, "text": text, "field_bg": field,
        "field_fg": b.get("field_fg", text),
        "muted": b.get("muted", mix(text, card, 0.42)),
        "border": b.get("border", mix(text, card, 0.80)),
        "accent_fg": b.get("accent_fg", readable_on(acc)),
        "accent_hover": mix(acc, "#ffffff" if lum(acc) < 0.5 else "#000000", 0.14),
        "soft": mix(text, card, 0.91),
        "soft_hover": mix(text, card, 0.82),
        "header_bg": b.get("header_bg", mix(bg, "#000000", 0.25)),
        "row_alt": mix(text, field, 0.955),
        "select_bg": b.get("select_bg", acc),
        "danger": "#c0392b", "danger_hover": "#e04a3a",
    }
    p["accent_text"] = acc if abs(lum(acc) - lum(card)) > 0.35 else mix(acc, text, 0.55)
    p["select_fg"] = readable_on(p["select_bg"])
    p["header_fg"] = readable_on(p["header_bg"])
    p["header_muted"] = mix(p["header_fg"], p["header_bg"], 0.35)
    p["nav_bg"] = mix(p["header_fg"], p["header_bg"], 0.88)
    p["nav_hover"] = mix(p["header_fg"], p["header_bg"], 0.78)
    p["danger_text"] = "#ff6b5b" if lum(card) < 0.5 else "#b03a2e"
    return p


# ---------- storage ----------
def is_writable(folder):
    try:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".po_write_test_{uuid.uuid4().hex[:6]}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def read_settings():
    for p in SETTINGS_CANDIDATES:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
        except (OSError, ValueError):
            continue
    return {}


def write_settings(settings):
    for p in SETTINGS_CANDIDATES:
        if is_writable(p.parent):
            try:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump(settings, f, ensure_ascii=False, indent=2)
                return p
            except OSError:
                continue
    return None


def normalize(d):
    out = json.loads(json.dumps(DEFAULTS))
    if isinstance(d, dict):
        out.update(d)
    seen = set()
    today = date.today().isoformat()
    for key in ("entries", "trash"):
        clean = []
        for e in out.get(key) or []:
            if not isinstance(e, dict):
                continue
            e = dict(e)
            e["date"] = parse_date(str(e.get("date", ""))) or today
            e["start"] = parse_time(str(e.get("start", ""))) or "00:00"
            e["end"] = parse_time(str(e.get("end", ""))) or e["start"]
            sec = to_float(e.get("sec"), -1)
            e["sec"] = int(sec) if sec > 0 else entry_seconds(e["start"], e["end"])
            e["queue"] = str(e.get("queue") or QUEUES[0])
            e["note"] = str(e.get("note") or "")
            eid = str(e.get("id") or "")
            if not eid or eid in seen:
                eid = uuid.uuid4().hex[:10]
            seen.add(eid)
            e["id"] = eid
            clean.append(e)
        out[key] = clean
    if not isinstance(out.get("collapsed"), dict):
        out["collapsed"] = {}
    if not isinstance(out.get("custom_theme"), dict):
        out["custom_theme"] = dict(CUSTOM_DEFAULT)
    if out.get("theme") not in ("default", "dark", "custom"):
        out["theme"] = "default"
    t = out.get("timer")
    if not (isinstance(t, dict) and isinstance(t.get("start"), (int, float))):
        out["timer"] = None
    return out


def load_data(folder):
    folder = Path(folder)
    for name in (DATA_NAME, BACKUP_NAME):
        try:
            with open(folder / name, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return normalize(d)
        except (OSError, ValueError):
            continue
    return normalize({})


def save_data(data, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / DATA_NAME
    if target.exists():
        try:
            shutil.copy2(target, folder / BACKUP_NAME)
        except OSError:
            pass
    tmp = target.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, target)  # atomic: never leaves a half-written file


def same_path(a, b):
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except OSError:
        return False


def resolve_data_dir(parent, settings):
    """Work out where the log lives; ask the user on first run."""
    saved = settings.get("data_dir")
    if saved:
        d = Path(saved)
        if d.is_dir() and is_writable(d):
            return d
        if not messagebox.askyesno(
                APP_NAME,
                f"The folder where your overtime log is saved can't be reached:\n{saved}\n\n"
                "If it's on a USB or network drive, connect it and open the app again.\n\n"
                "Choose a different folder now? (No closes the app.)", parent=parent):
            parent.destroy()
            raise SystemExit(0)
    elif (LEGACY_DIR / DATA_NAME).exists():
        return LEGACY_DIR  # keep existing logs working; move them from View > Change save folder
    else:
        messagebox.showinfo(
            APP_NAME,
            "Welcome to Procore OT App!\n\nChoose the folder where your overtime log should be "
            "saved, for example a folder on D:\\ or in OneDrive.\n\n"
            "You can change it any time from View > Change save folder...", parent=parent)
    while True:
        picked = filedialog.askdirectory(parent=parent, title="Choose a folder for your overtime log",
                                         mustexist=False)
        if not picked:
            base = app_base_dir()
            fallback = base if is_writable(base) else LEGACY_DIR
            messagebox.showinfo(APP_NAME, f"No folder chosen. Your log will be saved here:\n{fallback}\n\n"
                                          "You can change this any time from View > Change save folder...",
                                parent=parent)
            return fallback
        d = Path(picked)
        if is_writable(d):
            return d
        messagebox.showerror(APP_NAME, f"The app can't save files in:\n{d}\n\nPlease choose another folder.",
                             parent=parent)


# ---------- pop-up pickers ----------
class Popup(tk.Toplevel):
    def __init__(self, app, anchor, title):
        master = anchor.winfo_toplevel()
        super().__init__(master)
        self.app, self.anchor = app, anchor
        p = app.p
        self._prev_grab = self.grab_current()
        self.title(title)
        self.configure(bg=p["card_bg"])
        self.resizable(False, False)
        self.transient(master)
        self.body = tk.Frame(self, bg=p["card_bg"], padx=14, pady=12,
                             highlightthickness=2, highlightbackground=p["accent"], highlightcolor=p["accent"])
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.close())
        self.protocol("WM_DELETE_WINDOW", self.close)

    def show(self):
        self.update_idletasks()
        a = self.anchor
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        x = a.winfo_rootx() + a.winfo_width() - w
        y = a.winfo_rooty() + a.winfo_height() + 4
        x = max(0, min(x, sw - w - 8))
        if y + h > sh - 48:
            y = max(0, a.winfo_rooty() - h - 4)
        self.geometry(f"+{x}+{y}")
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def close(self):
        prev = self._prev_grab
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        if prev is not None:
            try:
                if prev.winfo_exists():
                    prev.grab_set()
            except tk.TclError:
                pass


class DatePicker(Popup):
    def __init__(self, app, anchor, var):
        super().__init__(app, anchor, "Pick a date")
        p = app.p
        self.var = var
        iso = parse_date(var.get())
        self.selected = datetime.strptime(iso, "%Y-%m-%d").date() if iso else date.today()
        self.year, self.month = self.selected.year, self.selected.month

        head = tk.Frame(self.body, bg=p["card_bg"])
        head.pack(fill="x")
        ttk.Button(head, text="◀", width=3, style="Icon.TButton", cursor="hand2",
                   command=lambda: self.shift(-1)).pack(side="left")
        self.title_lbl = tk.Label(head, bg=p["card_bg"], fg=p["text"], font=app.f(1.1, "bold"))
        self.title_lbl.pack(side="left", expand=True, fill="x", padx=8)
        ttk.Button(head, text="▶", width=3, style="Icon.TButton", cursor="hand2",
                   command=lambda: self.shift(1)).pack(side="right")

        self.grid_fr = tk.Frame(self.body, bg=p["card_bg"])
        self.grid_fr.pack(pady=(10, 8))

        foot = tk.Frame(self.body, bg=p["card_bg"])
        foot.pack(fill="x")
        ttk.Button(foot, text="Today", cursor="hand2",
                   command=lambda: self.pick(date.today())).pack(side="left")
        ttk.Button(foot, text="Yesterday", cursor="hand2",
                   command=lambda: self.pick(date.today() - timedelta(days=1))).pack(side="left", padx=6)
        ttk.Button(foot, text="Close", cursor="hand2", command=self.close).pack(side="right")
        self.draw()
        self.show()

    def shift(self, step):
        m, y = self.month + step, self.year
        if m < 1:
            m, y = 12, y - 1
        elif m > 12:
            m, y = 1, y + 1
        self.month, self.year = m, y
        self.draw()

    def draw(self):
        p, f = self.app.p, self.app.f
        for w in self.grid_fr.winfo_children():
            w.destroy()
        self.title_lbl.config(text=f"{calendar.month_name[self.month]} {self.year}")
        for i, wd in enumerate(("Su", "Mo", "Tu", "We", "Th", "Fr", "Sa")):
            tk.Label(self.grid_fr, text=wd, width=4, bg=p["card_bg"], fg=p["muted"],
                     font=f(0.85, "bold")).grid(row=0, column=i, pady=(0, 4))
        today = date.today()
        weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(self.year, self.month)
        for r, week in enumerate(weeks, start=1):
            for c, d in enumerate(week):
                is_sel, is_today = d == self.selected, d == today
                if is_sel:
                    bg, fg = p["accent"], p["accent_fg"]
                elif is_today:
                    bg, fg = p["soft_hover"], p["text"]
                else:
                    bg, fg = p["card_bg"], p["text"] if d.month == self.month else p["muted"]
                tk.Button(self.grid_fr, text=str(d.day), width=3, relief="flat", bd=0, bg=bg, fg=fg,
                          activebackground=p["accent_hover"], activeforeground=p["accent_fg"],
                          cursor="hand2", font=f(0.95, "bold" if (is_sel or is_today) else "normal"),
                          command=lambda d=d: self.pick(d)).grid(row=r, column=c, padx=1, pady=1, ipady=2)

    def pick(self, d):
        self.var.set(d.isoformat())
        self.close()


class TimePicker(Popup):
    def __init__(self, app, anchor, var, title="Pick a time"):
        super().__init__(app, anchor, title)
        p, f = app.p, app.f
        self.var = var
        cur = parse_time(var.get()) or datetime.now().strftime("%H:%M")
        h24, mi = int(cur[:2]), int(cur[3:])
        self.h = tk.StringVar(value=str(h24 % 12 or 12))
        self.m = tk.StringVar(value=f"{mi:02d}")
        self.ap = "PM" if h24 >= 12 else "AM"

        tk.Label(self.body, text=title, bg=p["card_bg"], fg=p["text"], font=f(1.05, "bold")).pack(anchor="w")
        row = tk.Frame(self.body, bg=p["card_bg"])
        row.pack(pady=(10, 8))
        big = f(1.6, "bold", "Consolas")
        ttk.Spinbox(row, from_=1, to=12, wrap=True, width=3, textvariable=self.h, font=big,
                    justify="center").pack(side="left")
        tk.Label(row, text=":", bg=p["card_bg"], fg=p["text"], font=big).pack(side="left", padx=4)
        ttk.Spinbox(row, from_=0, to=59, wrap=True, width=3, textvariable=self.m, font=big,
                    justify="center", format="%02.0f").pack(side="left")
        self.ap_btns = {}
        for i, v in enumerate(("AM", "PM")):
            b = ttk.Button(row, text=v, width=4, cursor="hand2", command=lambda v=v: self.set_ap(v))
            b.pack(side="left", padx=(12 if i == 0 else 4, 0))
            self.ap_btns[v] = b
        self.set_ap(self.ap)

        quick = tk.Frame(self.body, bg=p["card_bg"])
        quick.pack(fill="x")
        tk.Label(quick, text="Minutes:", bg=p["card_bg"], fg=p["muted"], font=f(0.9)).pack(side="left", padx=(0, 6))
        for mins in (0, 15, 30, 45):
            ttk.Button(quick, text=f":{mins:02d}", style="Icon.TButton", cursor="hand2",
                       command=lambda v=mins: self.m.set(f"{v:02d}")).pack(side="left", padx=(0, 4))
        ttk.Button(quick, text="Now", cursor="hand2", command=self.now).pack(side="right")

        foot = tk.Frame(self.body, bg=p["card_bg"])
        foot.pack(fill="x", pady=(12, 0))
        ttk.Button(foot, text="Set time", style="Primary.TButton", cursor="hand2", command=self.ok).pack(side="right")
        ttk.Button(foot, text="Cancel", cursor="hand2", command=self.close).pack(side="right", padx=(0, 6))
        self.bind("<Return>", lambda e: self.ok())
        self.show()

    def set_ap(self, v):
        self.ap = v
        for k, b in self.ap_btns.items():
            b.configure(style="Primary.TButton" if k == v else "TButton")

    def now(self):
        n = datetime.now()
        self.h.set(str(n.hour % 12 or 12))
        self.m.set(f"{n.minute:02d}")
        self.set_ap("PM" if n.hour >= 12 else "AM")

    def ok(self):
        try:
            h, mi = int(self.h.get()), int(self.m.get())
        except ValueError:
            h, mi = -1, -1
        if not (1 <= h <= 12 and 0 <= mi <= 59):
            messagebox.showwarning("Check the time", "Hours go from 1 to 12 and minutes from 00 to 59.", parent=self)
            return
        h24 = h % 12 + (12 if self.ap == "PM" else 0)
        self.var.set(f"{h24:02d}:{mi:02d}")
        self.close()


# ---------- app ----------
class App:
    def __init__(self, root, data_dir):
        self.root = root
        self.data_dir = Path(data_dir)
        self.data = load_data(self.data_dir)
        self.scale = clamp_int(self.data.get("ui_scale"), 8, 18, 10)
        self.page = self.data.get("page") if self.data.get("page") in ("log", "summary") else "log"
        self.summary_period = "All time"
        self._period_map = {}
        self._search_text = ""
        self._settings_job = None
        self._save_error_shown = False
        self._bar_rows = []
        self._toolbar_mode = None
        self.search_var = None
        self.paned = None
        self.side_canvas = None
        self.status = tk.StringVar(value="Ready.")

        root.title(APP_NAME)
        root.minsize(980, 640)
        self._restore_window()
        for seq, step in (("<Control-plus>", 1), ("<Control-equal>", 1), ("<Control-KP_Add>", 1),
                          ("<Control-minus>", -1), ("<Control-KP_Subtract>", -1)):
            root.bind_all(seq, lambda e, s=step: self.change_scale(s))
        root.bind_all("<Control-Key-0>", lambda e: self.change_scale(10 - self.scale))
        root.protocol("WM_DELETE_WINDOW", self.close)

        self.rebuild(first=True)
        if same_path(self.data_dir, LEGACY_DIR):
            self.say("Tip: use 'Change save folder...' (bottom right) to keep your log outside C:\\Users.")
        else:
            self.say("All changes are saved automatically.")
        self.tick()

    # ----- palette, fonts, styles -----
    @property
    def p(self):
        return self._palette

    def compute_palette(self):
        key = self.data.get("theme", "default")
        base = self.data.get("custom_theme") if key == "custom" else PRESETS.get(key, PRESETS["default"])
        self._palette = build_palette(base or CUSTOM_DEFAULT)

    def f(self, mult=1.0, weight="normal", family="Segoe UI"):
        return (family, max(7, round(self.scale * mult)), weight)

    def char_w(self):
        try:
            return max(6, tkfont.Font(root=self.root, font=self.f()).measure("0"))
        except tk.TclError:
            return 8

    def style(self):
        p = self.p
        for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
            try:
                tkfont.nametofont(name).configure(family="Segoe UI", size=self.scale)
            except tk.TclError:
                pass
        s = ttk.Style(self.root)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        base = self.f()
        s.configure(".", font=base, background=p["card_bg"], foreground=p["text"], bordercolor=p["border"],
                    lightcolor=p["card_bg"], darkcolor=p["card_bg"], troughcolor=p["card_bg"],
                    focuscolor=p["accent"], selectbackground=p["select_bg"], selectforeground=p["select_fg"],
                    fieldbackground=p["field_bg"], insertcolor=p["field_fg"])
        s.configure("Card.TFrame", background=p["card_bg"])
        s.configure("Card.TLabel", background=p["card_bg"], foreground=p["text"], font=base)
        s.configure("Muted.TLabel", background=p["card_bg"], foreground=p["muted"], font=self.f(0.9))
        s.configure("Title.TLabel", background=p["card_bg"], foreground=p["text"], font=self.f(1.35, "bold"))
        s.configure("Clock.TLabel", background=p["card_bg"], foreground=p["text"],
                    font=self.f(3.0, "bold", "Consolas"))

        pad = (round(self.scale * 1.3), round(self.scale * 0.6))

        def button(name, bg, fg, hover, border=None, padding=pad, font=None):
            s.configure(name, background=bg, foreground=fg, bordercolor=border or bg, lightcolor=bg,
                        darkcolor=bg, relief="flat", borderwidth=1, padding=padding,
                        font=font or self.f(1.0, "bold"), anchor="center", focusthickness=0)
            s.map(name,
                  background=[("disabled", p["soft"]), ("pressed", hover), ("active", hover)],
                  lightcolor=[("pressed", hover), ("active", hover)],
                  darkcolor=[("pressed", hover), ("active", hover)],
                  bordercolor=[("focus", p["accent"]), ("active", border or hover)],
                  foreground=[("disabled", p["muted"])])

        button("TButton", p["soft"], p["text"], p["soft_hover"], border=p["border"])
        button("Primary.TButton", p["accent"], p["accent_fg"], p["accent_hover"])
        button("Danger.TButton", p["danger"], "#ffffff", p["danger_hover"])
        button("DangerSoft.TButton", p["soft"], p["danger_text"], p["soft_hover"], border=p["border"])
        button("Icon.TButton", p["soft"], p["text"], p["soft_hover"], border=p["border"],
               padding=(round(self.scale * 0.5), round(self.scale * 0.3)))
        button("Ghost.TButton", p["card_bg"], p["muted"], p["soft"], padding=(4, 0), font=self.f(1.2, "bold"))
        button("Nav.TButton", p["header_bg"], p["header_muted"], p["nav_bg"])
        button("NavActive.TButton", p["accent"], p["accent_fg"], p["accent_hover"])
        button("Bar.TButton", p["nav_bg"], p["header_fg"], p["nav_hover"],
               padding=(round(self.scale * 1.0), round(self.scale * 0.35)), font=self.f(0.9, "bold"))

        s.configure("Header.TMenubutton", background=p["nav_bg"], foreground=p["header_fg"],
                    bordercolor=p["nav_bg"], lightcolor=p["nav_bg"], darkcolor=p["nav_bg"],
                    arrowcolor=p["header_fg"], relief="flat", padding=pad, font=self.f(1.0, "bold"))
        s.map("Header.TMenubutton", background=[("active", p["nav_hover"]), ("pressed", p["nav_hover"])])

        field = dict(fieldbackground=p["field_bg"], foreground=p["field_fg"], insertcolor=p["field_fg"],
                     bordercolor=p["border"], lightcolor=p["field_bg"], darkcolor=p["field_bg"],
                     padding=(6, 4))
        s.configure("TEntry", **field)
        s.map("TEntry", bordercolor=[("focus", p["accent"])], lightcolor=[("focus", p["accent"])])
        for name in ("TCombobox", "TSpinbox"):
            s.configure(name, arrowcolor=p["text"], background=p["soft"], **field)
            s.map(name,
                  fieldbackground=[("readonly", p["field_bg"]), ("disabled", p["soft"])],
                  foreground=[("readonly", p["field_fg"]), ("disabled", p["muted"])],
                  selectbackground=[("readonly", p["field_bg"])],
                  selectforeground=[("readonly", p["field_fg"])],
                  bordercolor=[("focus", p["accent"])], lightcolor=[("focus", p["accent"])],
                  background=[("active", p["soft_hover"]), ("pressed", p["soft_hover"])])
        self.root.option_add("*TCombobox*Listbox.background", p["field_bg"])
        self.root.option_add("*TCombobox*Listbox.foreground", p["field_fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", p["accent_fg"])
        self.root.option_add("*TCombobox*Listbox.font", f"{{Segoe UI}} {self.scale}")

        s.configure("Treeview", background=p["field_bg"], fieldbackground=p["field_bg"],
                    foreground=p["field_fg"], rowheight=round(self.scale * 2.8), font=base,
                    bordercolor=p["border"], borderwidth=0)
        s.map("Treeview", background=[("selected", p["select_bg"])], foreground=[("selected", p["select_fg"])])
        s.configure("Treeview.Heading", background=p["soft"], foreground=p["text"], font=self.f(1.0, "bold"),
                    relief="flat", bordercolor=p["border"], lightcolor=p["soft"], darkcolor=p["soft"],
                    padding=(8, 6))
        s.map("Treeview.Heading", background=[("active", p["soft_hover"])])
        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        for orient in ("Vertical", "Horizontal"):
            s.configure(f"{orient}.TScrollbar", background=p["soft"], troughcolor=p["card_bg"],
                        bordercolor=p["card_bg"], arrowcolor=p["muted"], lightcolor=p["soft"],
                        darkcolor=p["soft"], gripcount=0)
            s.map(f"{orient}.TScrollbar", background=[("active", p["soft_hover"])])
        s.configure("TPanedwindow", background=p["bg"])
        s.configure("Sash", sashthickness=8, gripcount=0, background=p["bg"])

    # ----- building the window -----
    def rebuild(self, first=False):
        if self._settings_job:
            self.root.after_cancel(self._settings_job)
            self._settings_job = None
            self.persist()
        if not first:
            self._remember_layout()
        for w in list(self.root.winfo_children()):
            w.destroy()
        self.side_canvas = None
        self._toolbar_mode = None
        self.compute_palette()
        self.root.configure(bg=self.p["bg"])
        self.style()
        self.build()
        self.show_page(self.page)
        self.refresh()
        self.sync_timer_ui()
        self.root.after(150, self._restore_sash)

    def build(self):
        self.build_header()
        tk.Frame(self.root, bg=self.p["accent"], height=3).pack(fill="x", side="top")
        self.build_statusbar()
        self.paned = ttk.PanedWindow(self.root, orient="horizontal")
        self.paned.pack(fill="both", expand=True, padx=12, pady=(12, 10))
        left = self.build_sidebar(self.paned)
        right = tk.Frame(self.paned, bg=self.p["bg"])
        self.paned.add(left, weight=0)
        self.paned.add(right, weight=1)
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.pages = {"log": self.build_log_page(right), "summary": self.build_summary_page(right)}
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky="nsew", padx=(10, 0))

    def build_header(self):
        p = self.p
        head = tk.Frame(self.root, bg=p["header_bg"])
        head.pack(fill="x", side="top")
        inner = tk.Frame(head, bg=p["header_bg"])
        inner.pack(fill="x", padx=16, pady=10)
        tk.Label(inner, text="⏱  Procore OT App", bg=p["header_bg"], fg=p["header_fg"],
                 font=self.f(1.55, "bold")).pack(side="left")
        self.header_name = tk.Label(inner, text="", bg=p["header_bg"], fg=p["header_muted"], font=self.f(0.95))
        self.header_name.pack(side="left", padx=(12, 0))

        nav = tk.Frame(inner, bg=p["header_bg"])
        nav.pack(side="left", padx=(28, 0))
        self.nav_buttons = {}
        for key, label in (("log", "≡  Overtime log"), ("summary", "▦  Overtime summary")):
            b = ttk.Button(nav, text=label, style="Nav.TButton", cursor="hand2",
                           command=lambda k=key: self.show_page(k))
            b.pack(side="left", padx=(0, 6))
            self.nav_buttons[key] = b

        self.build_view_menu(inner).pack(side="right")
        pills = tk.Frame(inner, bg=p["header_bg"])
        pills.pack(side="right", padx=(0, 14))
        self.pill_total = self.pill(pills, "TOTAL TIME")
        self.pill_pay = self.pill(pills, "TO BE PAID")

    def pill(self, parent, caption):
        p = self.p
        fr = tk.Frame(parent, bg=p["nav_bg"], padx=12, pady=3)
        fr.pack(side="left", padx=4)
        tk.Label(fr, text=caption, bg=p["nav_bg"], fg=p["header_muted"], font=self.f(0.7, "bold")).pack(anchor="w")
        val = tk.Label(fr, text="-", bg=p["nav_bg"], fg=p["header_fg"], font=self.f(1.05, "bold"))
        val.pack(anchor="w")
        return val

    def build_view_menu(self, parent):
        p = self.p
        mb = ttk.Menubutton(parent, text="◐  View", style="Header.TMenubutton", cursor="hand2")
        m = tk.Menu(mb, tearoff=0, bg=p["card_bg"], fg=p["text"], activebackground=p["accent"],
                    activeforeground=p["accent_fg"], selectcolor=p["accent"], bd=0, font=self.f())
        self.theme_var = tk.StringVar(value=self.data.get("theme", "default"))
        m.add_command(label="Theme", state="disabled")
        for key, label in (("default", "☀  Default (teal & cream)"), ("dark", "☾  Dark mode"),
                           ("custom", "◆  Custom colors")):
            m.add_radiobutton(label=label, value=key, variable=self.theme_var,
                              command=lambda k=key: self.set_theme(k))
        m.add_command(label="✎  Customize colors...", command=self.open_color_picker)
        m.add_separator()
        m.add_command(label="Zoom in", accelerator="Ctrl +", command=lambda: self.change_scale(1))
        m.add_command(label="Zoom out", accelerator="Ctrl -", command=lambda: self.change_scale(-1))
        m.add_command(label="Reset zoom", accelerator="Ctrl 0", command=lambda: self.change_scale(10 - self.scale))
        m.add_separator()
        m.add_command(label="Change save folder...", command=self.change_folder)
        m.add_command(label="Open save folder", command=self.open_folder)
        mb["menu"] = m
        return mb

    def build_statusbar(self):
        p = self.p
        bar = tk.Frame(self.root, bg=p["header_bg"])
        bar.pack(side="bottom", fill="x")
        inner = tk.Frame(bar, bg=p["header_bg"])
        inner.pack(fill="x", padx=14, pady=6)
        tk.Label(inner, textvariable=self.status, bg=p["header_bg"], fg=p["header_muted"], anchor="w",
                 font=self.f(0.9)).pack(side="left", fill="x", expand=True)
        ttk.Button(inner, text="Open folder", style="Bar.TButton", cursor="hand2",
                   command=self.open_folder).pack(side="right", padx=(6, 0))
        ttk.Button(inner, text="Change save folder...", style="Bar.TButton", cursor="hand2",
                   command=self.change_folder).pack(side="right", padx=(6, 0))
        self.location_lbl = tk.Label(inner, text="", bg=p["header_bg"], fg=p["header_fg"], font=self.f(0.9))
        self.location_lbl.pack(side="right", padx=(12, 4))
        self.update_location_label()

    def update_location_label(self):
        try:
            self.location_lbl.config(text=f"●  Auto-saving to:  {self.data_dir}")
        except (AttributeError, tk.TclError):
            pass

    def build_sidebar(self, parent):
        p = self.p
        outer = tk.Frame(parent, bg=p["bg"])
        try:
            width = int(self.root.winfo_fpixels("1i") * 4.0 * self.scale / 10)
        except tk.TclError:
            width = 400
        canvas = tk.Canvas(outer, bg=p["bg"], highlightthickness=0, bd=0, width=width)
        vs = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=p["bg"])
        win = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.configure(yscrollcommand=vs.set)
        canvas.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y", padx=(4, 0))
        self.side_canvas = canvas
        self.root.bind_all("<MouseWheel>", self._on_wheel)
        self.build_cards(inner)
        return outer

    def _on_wheel(self, event):
        c = self.side_canvas
        if c is None:
            return
        try:
            w = self.root.winfo_containing(event.x_root, event.y_root)
        except (KeyError, tk.TclError):
            return
        if w is not None and str(w).startswith(str(c)):
            step = -1 if event.delta > 0 else 1
            c.yview_scroll(step * max(1, abs(int(event.delta / 120))), "units")

    def card(self, parent, key, title, icon=""):
        p = self.p
        outer = tk.Frame(parent, bg=p["card_bg"], highlightthickness=1, highlightbackground=p["border"],
                         highlightcolor=p["border"])
        outer.pack(fill="x", pady=(0, 12))
        tk.Frame(outer, bg=p["accent"], width=4).pack(side="left", fill="y")
        body = tk.Frame(outer, bg=p["card_bg"])
        body.pack(side="left", fill="both", expand=True, padx=14, pady=12)
        head = tk.Frame(body, bg=p["card_bg"])
        head.pack(fill="x")
        tk.Label(head, text=f"{icon}  {title}" if icon else title, bg=p["card_bg"], fg=p["text"],
                 font=self.f(1.15, "bold")).pack(side="left")
        content = ttk.Frame(body, style="Card.TFrame")
        content.columnconfigure(1, weight=1)
        collapsed = bool(self.data["collapsed"].get(key))
        btn = ttk.Button(head, text="▸" if collapsed else "▾", width=2, style="Ghost.TButton", cursor="hand2")
        btn.pack(side="right")

        def toggle():
            now_collapsed = not self.data["collapsed"].get(key, False)
            self.data["collapsed"][key] = now_collapsed
            if now_collapsed:
                content.pack_forget()
                btn.config(text="▸")
            else:
                content.pack(fill="x", pady=(10, 0))
                btn.config(text="▾")
            self.persist()

        btn.configure(command=toggle)
        if not collapsed:
            content.pack(fill="x", pady=(10, 0))
        return content

    def field(self, parent, label, row, var, picker=None, width=None):
        ttk.Label(parent, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
        e = ttk.Entry(parent, textvariable=var, font=self.f())
        if width:
            e.configure(width=width)
        e.grid(row=row, column=1, sticky="ew", pady=4, columnspan=1 if picker else 2)
        if picker:
            icon, action = picker
            b = ttk.Button(parent, text=icon, width=3, style="Icon.TButton", cursor="hand2")
            b.configure(command=lambda: action(b))
            b.grid(row=row, column=2, sticky="e", padx=(6, 0), pady=4)
        return e

    def build_cards(self, parent):
        # Pay settings
        c = self.card(parent, "pay", "Pay settings", "⚙")
        self.v_name = tk.StringVar(value=self.data.get("name", ""))
        self.v_rate = tk.StringVar(value=str(self.data.get("rate", "")))
        self.v_mult = tk.StringVar(value=str(self.data.get("mult", "1")))
        self.field(c, "Name", 0, self.v_name)
        self.field(c, "Rate (EGP/hour)", 1, self.v_rate)
        self.field(c, "Multiplier", 2, self.v_mult)
        ttk.Label(c, text="Your name is added to every new log entry.", style="Muted.TLabel").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(4, 0))
        for v in (self.v_name, self.v_rate, self.v_mult):
            v.trace_add("write", self.on_settings)

        # Live timer
        c = self.card(parent, "timer", "Live timer", "⏱")
        self.clock = ttk.Label(c, text="00:00:00", style="Clock.TLabel", anchor="center")
        self.clock.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.timer_info = ttk.Label(c, text="", style="Muted.TLabel", anchor="center")
        self.timer_info.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        self.t_queue = ttk.Combobox(c, values=QUEUES, state="readonly", font=self.f())
        self.t_queue.set(QUEUES[0])
        self.t_queue.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(0, 8))
        self.t_btn = ttk.Button(c, text="▶  Start", style="Primary.TButton", cursor="hand2",
                                command=self.toggle_timer)
        self.t_btn.grid(row=2, column=2, sticky="e")

        # Add overtime manually
        c = self.card(parent, "manual", "Add overtime manually", "✚")
        self.m_date = tk.StringVar(value=date.today().isoformat())
        self.m_start, self.m_end, self.m_note = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.field(c, "Date", 0, self.m_date, ("📅", lambda b: DatePicker(self, b, self.m_date)))
        ttk.Label(c, text="Queue", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=4, padx=(0, 10))
        self.m_queue = ttk.Combobox(c, values=QUEUES, state="readonly", font=self.f())
        self.m_queue.set(QUEUES[0])
        self.m_queue.grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)
        self.field(c, "Start time", 2, self.m_start,
                   ("🕒", lambda b: TimePicker(self, b, self.m_start, "Start time")))
        self.field(c, "End time", 3, self.m_end,
                   ("🕒", lambda b: TimePicker(self, b, self.m_end, "End time")))
        self.field(c, "Note", 4, self.m_note)
        self.m_preview = ttk.Label(c, text="", style="Muted.TLabel")
        self.m_preview.grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Button(c, text="✚  Add entry", style="Primary.TButton", cursor="hand2",
                   command=self.add_manual).grid(row=6, column=0, columnspan=3, sticky="e", pady=(10, 0))
        for v in (self.m_date, self.m_start, self.m_end):
            v.trace_add("write", self.update_preview)

    def panel(self, parent, title=None):
        p = self.p
        outer = tk.Frame(parent, bg=p["card_bg"], highlightthickness=1, highlightbackground=p["border"])
        inner = tk.Frame(outer, bg=p["card_bg"])
        inner.pack(fill="both", expand=True, padx=14, pady=12)
        if title:
            tk.Label(inner, text=title, bg=p["card_bg"], fg=p["text"],
                     font=self.f(1.1, "bold")).pack(anchor="w", pady=(0, 8))
        return outer, inner

    def make_table(self, parent, specs, height=8, select="browse"):
        holder = tk.Frame(parent, bg=self.p["card_bg"])
        holder.pack(fill="both", expand=True)
        tv = ttk.Treeview(holder, columns=[s[0] for s in specs], show="headings", selectmode=select,
                          height=height)
        u = self.char_w()
        for key, title, chars, anchor in specs:
            tv.heading(key, text=title, anchor=anchor)
            tv.column(key, width=int(chars * u), minwidth=int(min(chars, 6) * u), anchor=anchor, stretch=True)
        tv.tag_configure("odd", background=self.p["row_alt"])
        tv.tag_configure("even", background=self.p["field_bg"])
        sb = ttk.Scrollbar(holder, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return tv

    def build_log_page(self, parent):
        p = self.p
        page = tk.Frame(parent, bg=p["bg"])
        outer, panel = self.panel(page)
        outer.pack(fill="both", expand=True)

        top = tk.Frame(panel, bg=p["card_bg"])
        top.pack(fill="x", pady=(0, 10))
        tk.Label(top, text="Overtime log", bg=p["card_bg"], fg=p["text"], font=self.f(1.35, "bold")).pack(side="left")
        self.log_count = tk.Label(top, text="", bg=p["card_bg"], fg=p["muted"], font=self.f(0.9))
        self.log_count.pack(side="left", padx=(12, 0))
        self.search_var = tk.StringVar(value=self._search_text)
        ttk.Entry(top, textvariable=self.search_var, width=22, font=self.f()).pack(side="right")
        tk.Label(top, text="Search", bg=p["card_bg"], fg=p["muted"], font=self.f(0.9)).pack(side="right", padx=(0, 6))
        self.search_var.trace_add("write", lambda *a: self.refresh_log())

        self.tree = self.make_table(panel, [
            ("name", "Name", 13, "w"), ("date", "Date", 11, "w"), ("queue", "Queue", 11, "w"),
            ("time", "Time", 19, "w"), ("dur", "Duration", 9, "center"), ("pay", "Pay", 13, "e"),
            ("note", "Note", 20, "w")], height=14, select="extended")
        t = self.tree
        t.bind("<Double-1>", lambda e: self.edit_selected() if t.identify_region(e.x, e.y) == "cell" else None)
        t.bind("<Delete>", lambda e: (self.delete_selected(), "break")[1])
        t.bind("<Control-a>", self._select_all)
        t.bind("<Control-A>", self._select_all)
        t.bind("<<TreeviewSelect>>", lambda e: self.update_log_count())
        t.bind("<Button-3>", self._tree_menu)

        bar = tk.Frame(panel, bg=p["card_bg"])
        bar.pack(fill="x", pady=(12, 0))
        bar.columnconfigure(1, weight=1)
        lg = tk.Frame(bar, bg=p["card_bg"])
        rg = tk.Frame(bar, bg=p["card_bg"])
        for text, cmd, st in (("✎  Edit", self.edit_selected, "TButton"),
                              ("✕  Delete selected", self.delete_selected, "DangerSoft.TButton"),
                              ("✖  Clear all", self.clear_all, "Danger.TButton")):
            ttk.Button(lg, text=text, command=cmd, style=st, cursor="hand2").pack(side="left", padx=(0, 8))
        for text, cmd, st in (("❐  Copy log", self.copy_log, "Primary.TButton"),
                              ("⇩  Export CSV", self.export_csv, "TButton"),
                              ("♻  Recently deleted", self.show_trash, "TButton")):
            ttk.Button(rg, text=text, command=cmd, style=st, cursor="hand2").pack(side="right", padx=(8, 0))

        def relayout(event=None):
            wide = bar.winfo_width() >= lg.winfo_reqwidth() + rg.winfo_reqwidth() + 16
            mode = "wide" if wide else "narrow"
            if mode == self._toolbar_mode:
                return
            self._toolbar_mode = mode
            if wide:
                lg.grid(row=0, column=0, columnspan=1, sticky="w")
                rg.grid(row=0, column=1, columnspan=1, sticky="e", pady=0)
            else:
                lg.grid(row=0, column=0, columnspan=2, sticky="w")
                rg.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        bar.bind("<Configure>", relayout)
        relayout()
        return page

    def build_summary_page(self, parent):
        p = self.p
        page = tk.Frame(parent, bg=p["bg"])

        top_o, top = self.panel(page)
        top_o.pack(fill="x")
        row = tk.Frame(top, bg=p["card_bg"])
        row.pack(fill="x")
        tk.Label(row, text="Overtime summary", bg=p["card_bg"], fg=p["text"], font=self.f(1.35, "bold")).pack(side="left")
        self.sum_sub = tk.Label(row, text="", bg=p["card_bg"], fg=p["muted"], font=self.f(0.9))
        self.sum_sub.pack(side="left", padx=(12, 0))
        ttk.Button(row, text="❐  Copy summary", style="Primary.TButton", cursor="hand2",
                   command=self.copy_summary).pack(side="right")
        self.period_box = ttk.Combobox(row, state="readonly", width=16, font=self.f())
        self.period_box.pack(side="right", padx=(0, 10))
        self.period_box.bind("<<ComboboxSelected>>", lambda e: self.on_period(self.period_box.get()))
        tk.Label(row, text="Period", bg=p["card_bg"], fg=p["muted"], font=self.f(0.9)).pack(side="right", padx=(0, 6))

        tiles = tk.Frame(page, bg=p["bg"])
        tiles.pack(fill="x", pady=12)
        self.tiles = {}
        for i, (key, caption) in enumerate((("time", "Total time"), ("pay", "To be paid"),
                                            ("count", "Entries"), ("avg", "Average per entry"))):
            tiles.columnconfigure(i, weight=1, uniform="tile")
            t = tk.Frame(tiles, bg=p["card_bg"], highlightthickness=1, highlightbackground=p["border"])
            t.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 10, 0))
            tk.Frame(t, bg=p["accent"], height=3).pack(fill="x")
            tk.Label(t, text=caption.upper(), bg=p["card_bg"], fg=p["muted"],
                     font=self.f(0.8, "bold")).pack(anchor="w", padx=14, pady=(10, 0))
            v = tk.Label(t, text="-", bg=p["card_bg"], fg=p["accent_text"] if key == "pay" else p["text"],
                         font=self.f(1.7, "bold"))
            v.pack(anchor="w", padx=14)
            sub = tk.Label(t, text="", bg=p["card_bg"], fg=p["muted"], font=self.f(0.85))
            sub.pack(anchor="w", padx=14, pady=(0, 10))
            self.tiles[key] = (v, sub)

        low = tk.Frame(page, bg=p["bg"])
        low.pack(fill="both", expand=True)
        low.columnconfigure(0, weight=3, uniform="low")
        low.columnconfigure(1, weight=2, uniform="low")
        low.rowconfigure(1, weight=1)

        qo, qi = self.panel(low, "By queue")
        qo.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 12))
        self.bar_canvas = tk.Canvas(qi, bg=p["card_bg"], highlightthickness=0, bd=0,
                                    height=int(self.scale * 3.4 * len(QUEUES)) + 12)
        self.bar_canvas.pack(fill="x")
        self.bar_canvas.bind("<Configure>", self.draw_bars)

        do, di = self.panel(low, "By day")
        do.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        self.day_table = self.make_table(di, [("date", "Date", 12, "w"), ("count", "Entries", 7, "center"),
                                              ("time", "Time", 10, "center"), ("pay", "Pay", 14, "e")], height=6)

        mo, mi = self.panel(low, "By month")
        mo.grid(row=0, column=1, rowspan=2, sticky="nsew", padx=(6, 0))
        tk.Label(mi, text="Double-click a month to focus the summary on it.", bg=p["card_bg"], fg=p["muted"],
                 font=self.f(0.85)).pack(anchor="w", pady=(0, 6))
        self.month_table = self.make_table(mi, [("month", "Month", 14, "w"), ("count", "Entries", 7, "center"),
                                                ("time", "Time", 10, "center"), ("pay", "Pay", 14, "e")], height=10)
        self.month_table.bind("<Double-1>", self._month_double)
        return page

    def show_page(self, key):
        if key not in self.pages:
            key = "log"
        self.page = key
        self.data["page"] = key
        self.pages[key].tkraise()
        for k, b in self.nav_buttons.items():
            b.configure(style="NavActive.TButton" if k == key else "Nav.TButton")
        if key == "summary":
            self.root.after(60, self.draw_bars)

    # ----- layout memory -----
    def _restore_window(self):
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        m = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", str(self.data.get("geometry") or ""))
        if m:
            w, h, x, y = map(int, m.groups())
            w, h = min(w, sw), min(h, sh)
            if 0 <= x < sw - 120 and 0 <= y < sh - 120:
                self.root.geometry(f"{w}x{h}+{x}+{y}")
            else:
                self.root.geometry(f"{w}x{h}")
        else:
            self.root.geometry(f"{int(sw * 0.82)}x{int(sh * 0.8)}")
        if self.data.get("maximized"):
            try:
                self.root.state("zoomed")
            except tk.TclError:
                pass

    def _remember_layout(self):
        try:
            if self.paned is not None and self.paned.winfo_exists():
                self.data["sash"] = int(self.paned.sashpos(0))
        except (tk.TclError, ValueError):
            pass
        if self.search_var is not None:
            try:
                self._search_text = self.search_var.get()
            except tk.TclError:
                pass

    def _restore_sash(self):
        pos = self.data.get("sash")
        try:
            if isinstance(pos, int) and 220 <= pos <= self.paned.winfo_width() - 360:
                self.paned.sashpos(0, pos)
        except (tk.TclError, AttributeError):
            pass

    # ----- settings & state -----
    def rate(self):
        return to_float(self.data.get("rate"), 0.0)

    def mult(self):
        return to_float(self.data.get("mult"), 1.0)

    def say(self, msg):
        self.status.set(msg)

    def persist(self):
        try:
            save_data(self.data, self.data_dir)
            self._save_error_shown = False
            return True
        except OSError as e:
            if not self._save_error_shown:
                self._save_error_shown = True
                messagebox.showerror("Could not save",
                                     f"Your log could not be saved to:\n{self.data_dir}\n\n{e}\n\n"
                                     "Use 'Change save folder...' to pick another folder.")
            return False

    def on_settings(self, *_):
        self.data.update(name=self.v_name.get(), rate=self.v_rate.get().strip(), mult=self.v_mult.get().strip())
        if self._settings_job:
            self.root.after_cancel(self._settings_job)
        self._settings_job = self.root.after(350, self._commit_settings)

    def _commit_settings(self):
        self._settings_job = None
        self.persist()
        self.refresh()

    def set_theme(self, key):
        self.data["theme"] = key
        self.persist()
        self.rebuild()
        self.say({"default": "Default theme on.", "dark": "Dark mode on.", "custom": "Custom colors on."}[key])

    def change_scale(self, step):
        new = max(8, min(18, self.scale + step))
        if new != self.scale:
            self.scale = new
            self.data["ui_scale"] = new
            self.persist()
            self.rebuild()
            self.say(f"Zoom {round(new * 10)}%")

    def entries_sorted(self, newest_first=True):
        return sorted(self.data["entries"], key=sort_key, reverse=newest_first)

    def entry_name(self, e):
        return (e.get("name") or self.data.get("name") or "").strip()

    # ----- refresh -----
    def refresh(self):
        self.refresh_log()
        self.refresh_summary()
        self.refresh_header()
        self.update_preview()

    def refresh_header(self):
        tot = sum(e["sec"] for e in self.data["entries"])
        self.pill_total.config(text=fmt_dur(tot))
        self.pill_pay.config(text=money(pay_for(tot, self.rate(), self.mult())))
        name = (self.data.get("name") or "").strip()
        self.header_name.config(text=name if name else "Overtime tracker")

    def refresh_log(self):
        sel = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        r, m = self.rate(), self.mult()
        q = (self.search_var.get() if self.search_var is not None else "").strip().lower()
        shown = 0
        for e in self.entries_sorted():
            vals = (self.entry_name(e), mdy(e["date"]), e["queue"], f"{fmt12(e['start'])}  →  {fmt12(e['end'])}",
                    fmt_dur(e["sec"]), money(pay_for(e["sec"], r, m)), e.get("note", ""))
            if q and q not in " ".join(str(v) for v in vals).lower() and q not in e["date"]:
                continue
            self.tree.insert("", "end", iid=e["id"], values=vals, tags=("odd" if shown % 2 else "even",))
            shown += 1
        keep = [i for i in sel if self.tree.exists(i)]
        if keep:
            self.tree.selection_set(keep)
        self._shown = shown
        self.update_log_count()

    def update_log_count(self):
        total = len(self.data["entries"])
        shown = getattr(self, "_shown", total)
        n_sel = len(self.tree.selection())
        txt = f"{total} entr{'y' if total == 1 else 'ies'}"
        if shown != total:
            txt += f"  ·  {shown} shown"
        txt += f"  ·  {n_sel} selected" if n_sel else "  ·  Ctrl/Shift-click to select several"
        self.log_count.config(text=txt)

    def update_preview(self, *_):
        s, e = parse_time(self.m_start.get()), parse_time(self.m_end.get())
        if s and e and s != e:
            sec = entry_seconds(s, e)
            txt = f"⏲  {fmt_dur(sec)}  ·  {money(pay_for(sec, self.rate(), self.mult()))}"
            if e < s:
                txt += "  (overnight)"
        elif s and e:
            txt = "Start and end time are the same."
        else:
            txt = "Type times like 22:46 or 10:46 PM, or use the 📅 / 🕒 pickers."
        self.m_preview.config(text=txt)

    def period_entries(self):
        key = self._period_map.get(self.summary_period)
        return [e for e in self.data["entries"] if not key or e["date"][:7] == key]

    def on_period(self, label):
        self.summary_period = label
        self.refresh_summary()

    def _month_double(self, event):
        row = self.month_table.identify_row(event.y)
        if row:
            label = month_label(row)
            if label in self._period_map:
                self.on_period(label)

    def queue_totals(self, entries):
        names = list(QUEUES) + sorted({e["queue"] for e in entries} - set(QUEUES))
        by = {q: [0, 0] for q in names}
        for e in entries:
            by[e["queue"]][0] += e["sec"]
            by[e["queue"]][1] += 1
        return by

    def refresh_summary(self):
        r, m = self.rate(), self.mult()
        months = sorted({e["date"][:7] for e in self.data["entries"]}, reverse=True)
        self._period_map = {month_label(k): k for k in months}
        labels = ["All time"] + list(self._period_map)
        if self.summary_period not in labels:
            self.summary_period = "All time"
        self.period_box["values"] = labels
        self.period_box.set(self.summary_period)

        es = self.period_entries()
        tot, n = sum(e["sec"] for e in es), len(es)
        days = len({e["date"] for e in es})
        name = (self.data.get("name") or "").strip()
        self.sum_sub.config(text="  ·  ".join(x for x in (name, self.summary_period) if x))
        tv, ts = self.tiles["time"]
        tv.config(text=fmt_dur(tot))
        ts.config(text=f"{tot / 3600:.2f} decimal hours")
        pv, ps = self.tiles["pay"]
        pv.config(text=money(pay_for(tot, r, m)))
        ps.config(text=f"{fmt_num(r)} EGP/h  ×  {fmt_num(m)}")
        cv, cs = self.tiles["count"]
        cv.config(text=str(n))
        cs.config(text=f"on {days} day{'s' if days != 1 else ''}")
        av, avs = self.tiles["avg"]
        av.config(text=fmt_dur(tot / n) if n else "-")
        avs.config(text=f"≈ {money(pay_for(tot / n, r, m))} each" if n else "No entries yet")

        by = self.queue_totals(es)
        self._bar_rows = [(q, v[0], v[1], pay_for(v[0], r, m), (v[0] / tot * 100) if tot else 0)
                          for q, v in by.items()]
        want_h = int(self.scale * 3.4 * len(self._bar_rows)) + 12
        if int(self.bar_canvas.cget("height")) != want_h:
            self.bar_canvas.configure(height=want_h)
        self.draw_bars()

        self.day_table.delete(*self.day_table.get_children())
        by_day = {}
        for e in es:
            d = by_day.setdefault(e["date"], [0, 0])
            d[0] += e["sec"]
            d[1] += 1
        for i, (d, (sec, cnt)) in enumerate(sorted(by_day.items(), reverse=True)):
            self.day_table.insert("", "end", values=(mdy(d), cnt, fmt_dur(sec), money(pay_for(sec, r, m))),
                                  tags=("odd" if i % 2 else "even",))

        self.month_table.delete(*self.month_table.get_children())
        by_month = {}
        for e in self.data["entries"]:
            d = by_month.setdefault(e["date"][:7], [0, 0])
            d[0] += e["sec"]
            d[1] += 1
        for i, (k, (sec, cnt)) in enumerate(sorted(by_month.items(), reverse=True)):
            self.month_table.insert("", "end", iid=k, values=(month_label(k), cnt, fmt_dur(sec),
                                                               money(pay_for(sec, r, m))),
                                    tags=("odd" if i % 2 else "even",))

    def draw_bars(self, event=None):
        c = getattr(self, "bar_canvas", None)
        if c is None:
            return
        try:
            if not c.winfo_exists():
                return
            w = c.winfo_width()
        except tk.TclError:
            return
        if w < 80:
            return
        p = self.p
        c.delete("all")
        rows = self._bar_rows
        lh = int(self.scale * 3.4)
        label_font, value_font = self.f(1.0, "bold"), self.f(0.9)
        lf, vf = tkfont.Font(root=self.root, font=label_font), tkfont.Font(root=self.root, font=value_font)
        values = [f"{fmt_dur(sec)}  ·  {money(pay)}  ·  {share:.0f}%" for _, sec, _, pay, share in rows]
        label_w = max([lf.measure(r[0]) for r in rows] + [40]) + 16
        value_w = max([vf.measure(v) for v in values] + [40]) + 16
        x0, x1 = label_w, max(label_w + 40, w - value_w)
        maxsec = max([r[1] for r in rows] + [1])
        y = 6
        for (label, sec, _cnt, _pay, _share), value in zip(rows, values):
            mid = y + lh / 2
            c.create_text(0, mid, text=label, anchor="w", fill=p["text"], font=label_font)
            c.create_rectangle(x0, y + lh * 0.25, x1, y + lh * 0.75, fill=p["soft"], outline="")
            if sec:
                c.create_rectangle(x0, y + lh * 0.25, x0 + (x1 - x0) * sec / maxsec, y + lh * 0.75,
                                   fill=p["accent"], outline="")
            c.create_text(w - 2, mid, text=value, anchor="e", fill=p["muted"], font=value_font)
            y += lh

    # ----- timer -----
    def tick(self):
        try:
            t = self.data.get("timer")
            if t:
                s = max(0, int(time.time() - t["start"]))
                self.clock.config(text=f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")
                started = datetime.fromtimestamp(t["start"]).strftime("%H:%M")
                self.timer_info.config(text=f"●  {t['queue']} since {fmt12(started)}  ·  "
                                            f"≈ {money(pay_for(s, self.rate(), self.mult()))}")
            else:
                self.clock.config(text="00:00:00")
                self.timer_info.config(text="Pick a queue, then press Start.")
        except (tk.TclError, AttributeError):
            pass
        self.root.after(500, self.tick)

    def sync_timer_ui(self):
        running = bool(self.data.get("timer"))
        self.t_btn.config(text="■  Stop & save" if running else "▶  Start",
                          style="Danger.TButton" if running else "Primary.TButton")
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
                "id": uuid.uuid4().hex[:10], "name": (self.data.get("name") or "").strip(),
                "date": start.strftime("%Y-%m-%d"), "queue": t["queue"], "start": start.strftime("%H:%M"),
                "end": datetime.now().strftime("%H:%M"), "sec": sec, "note": ""})
            self.data["timer"] = None
            self.say(f"Saved {fmt_dur(sec)} on {t['queue']}.")
        self.persist()
        self.sync_timer_ui()
        self.refresh()

    # ----- add / edit / delete -----
    def validate(self, d, s, e, parent=None):
        d2, s2, e2 = parse_date(d), parse_time(s), parse_time(e)
        if not (d2 and s2 and e2):
            messagebox.showwarning("Check the entry",
                                   "Use a date like 2026-10-17 and times like 22:46 or 10:46 PM\n"
                                   "(or pick them with the 📅 and 🕒 buttons).", parent=parent or self.root)
            return None
        if s2 == e2:
            messagebox.showwarning("Check the entry", "Start and end time are the same.", parent=parent or self.root)
            return None
        return d2, s2, e2

    def add_manual(self):
        v = self.validate(self.m_date.get(), self.m_start.get(), self.m_end.get())
        if not v:
            return
        d, s, e = v
        self.data["entries"].append({"id": uuid.uuid4().hex[:10], "name": (self.data.get("name") or "").strip(),
                                     "date": d, "queue": self.m_queue.get(), "start": s, "end": e,
                                     "sec": entry_seconds(s, e), "note": self.m_note.get().strip()})
        self.m_start.set("")
        self.m_end.set("")
        self.m_note.set("")
        self.persist()
        self.refresh()
        self.say(f"Entry added: {mdy(d)}, {fmt_dur(entry_seconds(s, e))}.")

    def selected_entries(self):
        ids = set(self.tree.selection())
        return [e for e in self.data["entries"] if e["id"] in ids]

    def _select_all(self, event=None):
        self.tree.selection_set(self.tree.get_children())
        return "break"

    def _tree_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row and row not in self.tree.selection():
            self.tree.selection_set(row)
        p = self.p
        n = len(self.tree.selection())
        m = tk.Menu(self.root, tearoff=0, bg=p["card_bg"], fg=p["text"], activebackground=p["accent"],
                    activeforeground=p["accent_fg"], bd=0, font=self.f())
        m.add_command(label="✎  Edit", command=self.edit_selected, state="normal" if n == 1 else "disabled")
        m.add_command(label=f"✕  Delete selected ({n})", command=self.delete_selected,
                      state="normal" if n else "disabled")
        m.add_command(label="❐  Copy selected", command=self.copy_selected, state="normal" if n else "disabled")
        m.add_separator()
        m.add_command(label="Select all", accelerator="Ctrl+A", command=self._select_all)
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    def dialog(self, title):
        p = self.p
        win = tk.Toplevel(self.root)
        win.title(title)
        win.configure(bg=p["card_bg"])
        win.transient(self.root)
        frame = ttk.Frame(win, style="Card.TFrame", padding=18)
        frame.pack(fill="both", expand=True)
        win.bind("<Escape>", lambda e: win.destroy())
        return win, frame

    def open_dialog(self, win, modal=True):
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - w) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - h) // 3)
        win.geometry(f"+{x}+{y}")
        if modal:
            try:
                win.grab_set()
            except tk.TclError:
                pass
        win.focus_force()

    def edit_selected(self):
        items = self.selected_entries()
        if not items:
            messagebox.showinfo("Select an entry", "Click an entry in the log first.")
            return
        if len(items) > 1:
            messagebox.showinfo("Edit one entry at a time",
                                f"You selected {len(items)} entries. Select just one to edit it.")
            return
        e = items[0]
        win, f = self.dialog("Edit entry")
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="✎  Edit entry", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w",
                                                                      pady=(0, 12))
        vn = tk.StringVar(value=self.entry_name(e))
        vd, vs, ve = tk.StringVar(value=e["date"]), tk.StringVar(value=e["start"]), tk.StringVar(value=e["end"])
        vnote = tk.StringVar(value=e.get("note", ""))
        self.field(f, "Name", 1, vn, width=28)
        self.field(f, "Date", 2, vd, ("📅", lambda b: DatePicker(self, b, vd)), width=28)
        ttk.Label(f, text="Queue", style="Card.TLabel").grid(row=3, column=0, sticky="w", pady=4, padx=(0, 10))
        q = ttk.Combobox(f, values=sorted(set(QUEUES) | {e["queue"]}, key=lambda x: (x not in QUEUES, x)),
                         state="readonly", font=self.f())
        q.set(e["queue"])
        q.grid(row=3, column=1, columnspan=2, sticky="ew", pady=4)
        self.field(f, "Start time", 4, vs, ("🕒", lambda b: TimePicker(self, b, vs, "Start time")), width=28)
        self.field(f, "End time", 5, ve, ("🕒", lambda b: TimePicker(self, b, ve, "End time")), width=28)
        self.field(f, "Note", 6, vnote, width=28)

        def save():
            v = self.validate(vd.get(), vs.get(), ve.get(), parent=win)
            if not v:
                return
            e.update(name=vn.get().strip(), date=v[0], start=v[1], end=v[2], queue=q.get(),
                     sec=entry_seconds(v[1], v[2]), note=vnote.get().strip())
            self.persist()
            self.refresh()
            self.say("Entry updated.")
            win.destroy()

        row = ttk.Frame(f, style="Card.TFrame")
        row.grid(row=7, column=0, columnspan=3, sticky="e", pady=(14, 0))
        ttk.Button(row, text="Cancel", cursor="hand2", command=win.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(row, text="Save changes", style="Primary.TButton", cursor="hand2", command=save).pack(side="left")
        win.bind("<Return>", lambda ev: save())
        self.open_dialog(win)

    def delete_selected(self):
        items = self.selected_entries()
        if not items:
            messagebox.showinfo("Select entries", "Select one or more entries first.\n\n"
                                                  "Tip: Ctrl+click or Shift+click to select several, Ctrl+A for all.")
            return
        if len(items) > 1 and not messagebox.askyesno(
                "Delete entries", f"Move {len(items)} entries to Recently deleted?"):
            return
        self._to_trash(items)
        self.say(f"Moved {len(items)} entr{'y' if len(items) == 1 else 'ies'} to Recently deleted.")

    def clear_all(self):
        n = len(self.data["entries"])
        if not n:
            self.say("The log is already empty.")
            return
        if not messagebox.askyesno("Clear all logs",
                                   f"Move all {n} entries to Recently deleted?\n\n"
                                   "You can still restore them from ♻ Recently deleted.", icon="warning"):
            return
        self._to_trash(list(self.data["entries"]))
        self.say(f"Cleared {n} entries. Restore them from Recently deleted if needed.")

    def _to_trash(self, items):
        ids = {e["id"] for e in items}
        self.data["entries"] = [e for e in self.data["entries"] if e["id"] not in ids]
        now = time.time()
        for e in items:
            e["deletedAt"] = now
        self.data["trash"] = list(items) + self.data["trash"]
        self.persist()
        self.refresh()

    def show_trash(self):
        win, f = self.dialog("Recently deleted")
        ttk.Label(f, text="♻  Recently deleted", style="Title.TLabel").pack(anchor="w")
        ttk.Label(f, text="Restore entries you removed by mistake. Ctrl/Shift-click to pick several.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 10))
        holder = ttk.Frame(f, style="Card.TFrame")
        holder.pack(fill="both", expand=True)
        tv = self.make_table(holder, [("name", "Name", 12, "w"), ("date", "Date", 11, "w"),
                                      ("queue", "Queue", 10, "w"), ("time", "Time", 19, "w"),
                                      ("pay", "Pay", 13, "e"), ("deleted", "Deleted", 14, "w")],
                             height=10, select="extended")

        def fill():
            tv.delete(*tv.get_children())
            r, m = self.rate(), self.mult()
            for i, e in enumerate(self.data["trash"]):
                when = e.get("deletedAt")
                when = datetime.fromtimestamp(when).strftime("%m/%d %I:%M %p") if isinstance(when, (int, float)) else ""
                tv.insert("", "end", iid=e["id"], tags=("odd" if i % 2 else "even",), values=(
                    self.entry_name(e), mdy(e["date"]), e["queue"], f"{fmt12(e['start'])}  →  {fmt12(e['end'])}",
                    money(pay_for(e["sec"], r, m)), when))

        def picked():
            ids = set(tv.selection())
            return [e for e in self.data["trash"] if e["id"] in ids]

        def restore(items):
            if not items:
                messagebox.showinfo("Nothing selected", "Select one or more entries first.", parent=win)
                return
            ids = {e["id"] for e in items}
            self.data["trash"] = [e for e in self.data["trash"] if e["id"] not in ids]
            for e in items:
                e.pop("deletedAt", None)
                self.data["entries"].append(e)
            self.persist()
            self.refresh()
            fill()
            self.say(f"Restored {len(items)} entr{'y' if len(items) == 1 else 'ies'}.")

        def forever():
            items = picked()
            if not items:
                messagebox.showinfo("Nothing selected", "Select one or more entries first.", parent=win)
                return
            if messagebox.askyesno("Delete forever", f"Permanently delete {len(items)} entr"
                                                     f"{'y' if len(items) == 1 else 'ies'}?", parent=win):
                ids = {e["id"] for e in items}
                self.data["trash"] = [e for e in self.data["trash"] if e["id"] not in ids]
                self.persist()
                fill()

        def empty():
            if self.data["trash"] and messagebox.askyesno(
                    "Empty Recently deleted", f"Permanently delete all {len(self.data['trash'])} entries?",
                    parent=win, icon="warning"):
                self.data["trash"] = []
                self.persist()
                fill()

        bar = ttk.Frame(f, style="Card.TFrame")
        bar.pack(fill="x", pady=(12, 0))
        ttk.Button(bar, text="Restore selected", style="Primary.TButton", cursor="hand2",
                   command=lambda: restore(picked())).pack(side="left")
        ttk.Button(bar, text="Restore all", cursor="hand2",
                   command=lambda: restore(list(self.data["trash"]))).pack(side="left", padx=8)
        ttk.Button(bar, text="Delete forever", style="DangerSoft.TButton", cursor="hand2",
                   command=forever).pack(side="left")
        ttk.Button(bar, text="Empty bin", style="Danger.TButton", cursor="hand2",
                   command=empty).pack(side="left", padx=8)
        ttk.Button(bar, text="Close", cursor="hand2", command=win.destroy).pack(side="right")
        fill()
        self.open_dialog(win, modal=False)

    # ----- colors -----
    def open_color_picker(self):
        p = self.p
        win, f = self.dialog("Customize theme colors")
        stored = self.data.get("custom_theme") or {}
        cur = {k: stored.get(k) if valid_hex(stored.get(k)) else CUSTOM_DEFAULT[k] for k in BASE_KEYS}
        ttk.Label(f, text="✎  Custom colors", style="Title.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(f, text="Pick 5 colors. Buttons, borders and hover shades are matched automatically.",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=4, sticky="w", pady=(2, 12))
        rows = [("Window background", "bg"), ("Cards & panels", "card_bg"),
                ("Accent (buttons & highlights)", "accent"), ("Text", "text"), ("Input fields & table", "field_bg")]
        swatches, codes = {}, {}

        def paint(key):
            swatches[key].configure(bg=cur[key])
            codes[key].configure(text=cur[key].upper())

        def pick(key):
            res = colorchooser.askcolor(color=cur[key], parent=win, title="Choose a color")
            if res and res[1]:
                cur[key] = res[1]
                paint(key)

        for i, (label, key) in enumerate(rows, start=2):
            ttk.Label(f, text=label, style="Card.TLabel").grid(row=i, column=0, sticky="w", pady=5)
            swatches[key] = tk.Label(f, width=5, height=1, bg=cur[key], relief="flat", highlightthickness=1,
                                     highlightbackground=p["border"])
            swatches[key].grid(row=i, column=1, padx=10)
            codes[key] = ttk.Label(f, text=cur[key].upper(), style="Muted.TLabel", width=9)
            codes[key].grid(row=i, column=2)
            ttk.Button(f, text="Choose...", cursor="hand2", command=lambda k=key: pick(k)).grid(
                row=i, column=3, padx=(8, 0), pady=5)

        ttk.Label(f, text="Or start from a ready-made palette:", style="Card.TLabel").grid(
            row=8, column=0, columnspan=4, sticky="w", pady=(14, 6))
        presets = ttk.Frame(f, style="Card.TFrame")
        presets.grid(row=9, column=0, columnspan=4, sticky="w")

        def use(base):
            for k in BASE_KEYS:
                cur[k] = base[k]
                paint(k)

        for i, (label, base) in enumerate(STARTER_PALETTES):
            ttk.Button(presets, text=label, style="Icon.TButton", cursor="hand2",
                       command=lambda b=base: use(b)).grid(row=i // 4, column=i % 4, padx=(0, 6), pady=3, sticky="w")

        def apply():
            self.data["custom_theme"] = dict(cur)
            self.data["theme"] = "custom"
            self.persist()
            win.destroy()
            self.rebuild()
            self.say("Custom colors applied and saved.")

        btns = ttk.Frame(f, style="Card.TFrame")
        btns.grid(row=10, column=0, columnspan=4, sticky="e", pady=(16, 0))
        ttk.Button(btns, text="Cancel", cursor="hand2", command=win.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="Apply colors", style="Primary.TButton", cursor="hand2", command=apply).pack(side="left")
        self.open_dialog(win)

    # ----- output -----
    def log_text(self, entries=None):
        r, m = self.rate(), self.mult()
        es = sorted(entries if entries is not None else self.data["entries"], key=sort_key)
        return "\n".join(log_line(e, r, m) for e in es)

    def _clip(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()  # keeps the text on the clipboard after the app closes

    def copy_log(self):
        if not self.data["entries"]:
            self.say("Nothing to copy yet.")
            return
        self._clip(self.log_text())
        self.say(f"Log copied ({len(self.data['entries'])} entries).")

    def copy_selected(self):
        items = self.selected_entries()
        if items:
            self._clip(self.log_text(items))
            self.say(f"Copied {len(items)} selected entr{'y' if len(items) == 1 else 'ies'}.")

    def copy_summary(self):
        r, m = self.rate(), self.mult()
        es = self.period_entries()
        tot = sum(e["sec"] for e in es)
        name = (self.data.get("name") or "").strip()
        lines = [f"Procore OT App summary{' - ' + name if name else ''} ({self.summary_period})",
                 f"Total: {fmt_dur(tot)} ({tot / 3600:.2f} h) = {money(pay_for(tot, r, m))}",
                 f"Entries: {len(es)}", ""]
        for q, (sec, cnt) in self.queue_totals(es).items():
            lines.append(f"{q}: {fmt_dur(sec)} ({cnt} entr{'y' if cnt == 1 else 'ies'}) = {money(pay_for(sec, r, m))}")
        self._clip("\n".join(lines))
        self.say("Summary copied.")

    def export_csv(self):
        if not self.data["entries"]:
            self.say("Nothing to export yet.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV file", "*.csv")], initialdir=str(self.data_dir),
            initialfile=f"procore-ot-app-{datetime.now():%Y-%m-%d}.csv")
        if not path:
            return
        r, m = self.rate(), self.mult()
        tot = 0
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["Name", "Date", "Queue", "Start", "End", "Duration (h:mm)",
                            "Decimal hours", "Rate (EGP/h)", "Multiplier", "Pay (EGP)", "Note"])
                for e in self.entries_sorted(newest_first=False):
                    t = round(e["sec"] / 60)
                    tot += e["sec"]
                    w.writerow([self.entry_name(e), e["date"], e["queue"], e["start"], e["end"],
                                f"{t // 60}:{t % 60:02d}", f"{e['sec'] / 3600:.2f}", r, m,
                                f"{pay_for(e['sec'], r, m):.2f}", e.get("note", "")])
                t = round(tot / 60)
                w.writerow([(self.data.get("name") or "").strip(), "TOTAL", "", "", "", f"{t // 60}:{t % 60:02d}",
                            f"{tot / 3600:.2f}", r, m, f"{pay_for(tot, r, m):.2f}", ""])
        except OSError as e:
            messagebox.showerror("Could not export", str(e))
            return
        self.say(f"Exported to {path}")

    # ----- save folder -----
    def change_folder(self):
        picked = filedialog.askdirectory(parent=self.root, title="Choose a folder for your overtime log",
                                         initialdir=str(self.data_dir), mustexist=False)
        if not picked:
            return
        new_dir = Path(picked)
        if same_path(new_dir, self.data_dir):
            self.say("That's already your save folder.")
            return
        if not is_writable(new_dir):
            messagebox.showerror("Can't use this folder", f"The app can't save files in:\n{new_dir}")
            return
        old_dir = self.data_dir
        if (new_dir / DATA_NAME).exists():
            ans = messagebox.askyesnocancel(
                "A log already exists there",
                f"{new_dir} already has an overtime log.\n\n"
                "Yes - open the log that's in that folder\n"
                "No - replace it with your current log\n"
                "Cancel - keep using the current folder")
            if ans is None:
                return
            if ans:
                self.data_dir = new_dir
                write_settings({**read_settings(), "data_dir": str(new_dir)})
                self.data = load_data(new_dir)
                self.scale = clamp_int(self.data.get("ui_scale"), 8, 18, 10)
                self.rebuild()
                self.say(f"Now using the log saved in {new_dir}")
                return
        self.data_dir = new_dir
        if not self.persist():
            self.data_dir = old_dir
            return
        write_settings({**read_settings(), "data_dir": str(new_dir)})
        self.update_location_label()
        self.say(f"Your log is now saved in {new_dir}")
        if (old_dir / DATA_NAME).exists() and messagebox.askyesno(
                "Remove the old copy?",
                f"Your log was copied to:\n{new_dir}\n\nDelete the old copy from:\n{old_dir} ?"):
            for name in (DATA_NAME, BACKUP_NAME, Path(DATA_NAME).with_suffix(".tmp").name):
                try:
                    (old_dir / name).unlink()
                except OSError:
                    pass
            if same_path(old_dir, LEGACY_DIR):
                try:
                    old_dir.rmdir()  # only succeeds if the folder is now empty
                except OSError:
                    pass
            self.say(f"Your log is now saved in {new_dir} (old copy removed).")

    def open_folder(self):
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(self.data_dir)  # noqa
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.data_dir)])
            else:
                subprocess.Popen(["xdg-open", str(self.data_dir)])
        except OSError as e:
            messagebox.showerror("Could not open folder", str(e))

    def close(self):
        self._remember_layout()
        try:
            state = self.root.state()
            self.data["maximized"] = state == "zoomed"
            if state == "normal":
                self.data["geometry"] = self.root.geometry()
        except tk.TclError:
            pass
        if self._settings_job:
            self.root.after_cancel(self._settings_job)
        self.persist()
        self.root.destroy()


def main():
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on Windows
    except Exception:
        pass
    root = tk.Tk()
    root.withdraw()
    root.title(APP_NAME)
    settings = read_settings()
    data_dir = resolve_data_dir(root, settings)
    if settings.get("data_dir") != str(data_dir):
        write_settings({**settings, "data_dir": str(data_dir)})
    root.deiconify()
    App(root, data_dir)
    root.mainloop()


if __name__ == "__main__":
    main()
