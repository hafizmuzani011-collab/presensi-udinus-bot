"""Dashboard Web untuk Presensi Udinus Bot.
Fitur: status, jadwal, deadline, log, search, filter, notif, settings,
       chart timeline, export CSV, history presensi, dark mode, calendar view.
"""
import csv
import io
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from flask import Flask, jsonify, request, send_file, abort, Response

# === Path ke file2 bot ===
ROOT = Path(__file__).parent

# === Import dari config (single source of truth) ===
from config import (
    LOG_FILE, TASKS_DEADLINE_FILE, SCHEDULES_FILE,
    SCREENSHOT_TUGAS, SCREENSHOT_PRESENSI,
    KULINO_ACCOUNTS, BOT_START_TIME, LOG_DIR,
    get_stats_snapshot,
)

# === Logger ===
logger = logging.getLogger(__name__)

# === Auth token (WAJIB dari env) ===
DASH_TOKEN = os.environ.get("DASH_TOKEN")
if not DASH_TOKEN:
    raise RuntimeError("DASH_TOKEN tidak ditemukan! Set environment variable DASH_TOKEN, atau isi di .env")
logger.info("Dashboard token loaded from DASH_TOKEN env")

app = Flask(__name__)

# === Control & History (in-memory, share dengan bot.py) ===
# NOTE: CONTROL di-akses langsung oleh bot.py via `from web_dashboard import CONTROL`.
# Dilarang self-import (modul sedang loading), jadi CONTROL didefinisikan langsung.
CONTROL = {"autopilot": True, "trigger_tugas": 0, "last_msg": ""}
CONTROL_LOCK = threading.Lock()


def get_control(key: str, default=None):
    with CONTROL_LOCK:
        return CONTROL.get(key, default)


def set_control(key: str, value) -> None:
    with CONTROL_LOCK:
        CONTROL[key] = value


def consume_control(key: str, default=None):
    """Atomically read & reset control key. Pakai untuk trigger_*. Returns prior value."""
    with CONTROL_LOCK:
        val = CONTROL.get(key, default)
        if key in CONTROL:
            CONTROL[key] = default if default is not None else ""
        return val


PRESENSI_HISTORY_FILE = ROOT / "presensi_history.json"

def load_history():
    if not PRESENSI_HISTORY_FILE.exists():
        return []
    try:
        return json.loads(PRESENSI_HISTORY_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return []

def save_history(items):
    PRESENSI_HISTORY_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False))


# ============ Helpers ============
_log_cache = {"mtime": 0.0, "lines": []}
_log_cache_lock = threading.Lock()


def read_file_lines(path, n=200):
    try:
        mt = os.path.getmtime(path)
    except OSError:
        return []
    with _log_cache_lock:
        if mt != _log_cache["mtime"] or len(_log_cache["lines"]) > 5000:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    _log_cache["lines"] = f.readlines()
            except OSError:
                return []
            _log_cache["mtime"] = mt
    return [l.strip() for l in _log_cache["lines"][-n:] if l.strip()]

def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, FileNotFoundError, json.JSONDecodeError):
        return {}

def get_uptime():
    try:
        delta = datetime.now() - BOT_START_TIME
        d, r = delta.days, delta.seconds
        return f"{d}h {r//3600}j {(r%3600)//60}m"
    except Exception:
        return "?"

def format_tanggal(dt_str):
    try:
        dt = datetime.fromisoformat(dt_str)
        selisih = dt - datetime.now()
        sisa = ""
        if selisih.total_seconds() > 0:
            jam = int(selisih.total_seconds() / 3600)
            if jam > 72:
                sisa = f" ({jam//24}h)"
            else:
                sisa = f" ({jam}j)"
        return f"{dt.strftime('%d/%m %H:%M')}{sisa}"
    except ValueError:
        return dt_str

def _require_token_decorator(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        tok = request.args.get("token") or request.headers.get("X-Dash-Token")
        if tok != DASH_TOKEN:
            abort(401)
        return f(*args, **kwargs)
    return wrapper

_require_token = _require_token_decorator


# ============ API Routes ============
@app.route("/")
def index():
    return dashboard_page(request.args.get("token") or request.headers.get("X-Dash-Token") or DASH_TOKEN)

@app.route("/<page>")
def pages(page):
    if page in ("dashboard","jadwal","deadline","history","calendar","log","settings","logbook"):
        from flask import redirect
        return redirect(f"/?token={request.args.get('token') or request.headers.get('X-Dash-Token') or DASH_TOKEN}#{page}")
    return index()

def dashboard_page(token, page="dashboard"):
    nav_items = [
        ("dashboard", "Dashboard", "M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"),
        ("jadwal", "Jadwal", "M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"),
        ("deadline", "Deadline", "M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"),
        ("history", "Presensi", "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"),
        ("logbook", "Logbook", "M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z"),
        ("calendar", "Calendar", "M8 7V3m0 2.586l5.293-5.293a1 1 0 011.414 0L20 5.414V17a2 2 0 01-2 2H6a2 2 0 01-2-2V5a2 2 0 012-2h2z"),
        ("log", "Log", "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"),
        ("settings", "Settings", "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"),
    ]
    nav_html = ""
    for key, label, path in nav_items:
        nav_html += f'<a onclick="navigate(\'{key}\')" class="nav-link flex items-center gap-3 px-3 py-2.5 text-sm rounded-lg cursor-pointer" data-page="{key}"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="{path}"/></svg>{label}</a>'

    return f"""<!DOCTYPE html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Presensi Udinus</title>
<link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0b1c30">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
* {{ font-family: 'Inter', system-ui, sans-serif; }}
@keyframes pulse-dot {{ 0%,100%{{opacity:1}} 50%{{opacity:.4}} }}
.live {{ animation: pulse-dot 2s infinite; }}
.dark body {{ background:#0a0a0a; color:#e5e5e5; }}
.dark .bg-white {{ background:#171717 !important; border-color:#262626 !important; }}
.dark .text-gray-900 {{ color:#e5e5e5 !important; }}
.dark .text-gray-600 {{ color:#a3a3a3 !important; }}
.dark .text-gray-500 {{ color:#737373 !important; }}
.dark .text-gray-400 {{ color:#525252 !important; }}
.dark .bg-gray-50, .dark .bg-gray-100 {{ background:#0f0f0f !important; }}
.dark .border-gray-100, .dark .border-gray-200 {{ border-color:#262626 !important; }}
</style>
</head>
<body class="bg-gray-50 text-gray-900 min-h-screen">
<div class="flex min-h-screen">
<nav class="hidden lg:flex flex-col w-64 bg-white border-r border-gray-200 fixed h-full">
<div class="p-6 border-b border-gray-100">
<h1 class="text-lg font-bold tracking-tight">presensi/udinus</h1>
<p class="text-xs text-gray-500 mt-1">Monitoring & Control</p>
</div>
<div class="flex-1 px-4 py-4 space-y-1">
{nav_html}
</div>
<div class="p-4 border-t border-gray-100 space-y-3">
<button onclick="ta()" class="w-full flex items-center justify-between px-4 py-2.5 text-sm bg-gray-50 rounded-full border border-gray-200 hover:bg-gray-100 transition-colors">
<span>Autopilot</span>
<div id="at" class="w-10 h-5 bg-gray-300 rounded-full relative transition-colors"><div class="absolute left-0.5 top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform"></div></div>
</button>
<button id="installBtn" onclick="doInstall()" class="hidden w-full px-4 py-2 text-xs text-green-700 bg-green-50 border border-green-200 rounded-full hover:bg-green-100 flex items-center justify-center gap-2">
<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg> Install App
</button>
<button onclick="toggleTheme()" class="w-full px-4 py-2 text-xs text-gray-600 border border-gray-200 rounded-full hover:bg-gray-50 flex items-center justify-center gap-2" id="themeBtn">
<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z"/></svg>
Dark Mode
</button>
</div>
</nav>
<div class="flex-1 lg:ml-64">
<header class="sticky top-0 z-10 bg-white border-b border-gray-200 px-6 h-16 flex items-center justify-between">
<div class="flex items-center gap-3">
<span class="w-2 h-2 rounded-full live" id="liveDot" style="background:#22c55e"></span>
<span class="text-xs text-gray-500 font-medium tracking-wide" id="meta">Loading...</span>
<span id="realtimeClock" class="text-xs text-gray-400 font-mono ml-2">—</span>
</div>
<div class="flex items-center gap-2">
<div class="relative">
<input id="searchInput" oninput="filterCards(this.value)" type="text" placeholder="Cari tugas..." class="text-sm border border-gray-200 rounded-full pl-9 pr-3 py-1.5 w-48 focus:outline-none focus:border-gray-400 dark:bg-gray-800 dark:border-gray-700">
<svg class="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
</div>
<select id="filterUser" onchange="filterByUser(this.value)" class="text-sm border border-gray-200 rounded-full px-3 py-1.5 focus:outline-none focus:border-gray-400 dark:bg-gray-800">
<option value="all">Semua</option>
<option value="Hafizh">Hafizh</option>
<option value="Azfa">Azfa</option>
</select>
<button onclick="exportCSV()" title="Export CSV" class="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">
<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
</button>
<button onclick="openNotif()" class="relative p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">
<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9"/></svg>
<span id="notifBadge" class="absolute -top-0.5 -right-0.5 bg-red-500 text-white text-[10px] font-bold rounded-full w-4 h-4 flex items-center justify-center hidden">!</span>
</button>
<button onclick="tt()" class="text-sm font-medium text-white bg-gray-900 px-4 py-1.5 rounded-full hover:bg-gray-800 transition-colors">Cek Tugas</button>
<button onclick="rf()" class="p-2 text-gray-500 hover:text-gray-700 hover:bg-gray-100 rounded-lg transition-colors">
<svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
</button>
</div>
</header>
<main class="p-6 max-w-7xl mx-auto">

<!-- DASHBOARD -->
<div id="page-dashboard" class="page-section">
<div class="mb-8 flex items-center justify-between">
<div><p class="text-xs font-medium text-gray-500 uppercase tracking-wider">Dashboard</p><h2 class="text-2xl font-bold mt-1">Overview</h2></div>
<div class="text-xs text-gray-500" id="lastUpdate">—</div>
</div>
<div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
<div class="lg:col-span-4 bg-white border border-gray-200 rounded-xl p-6">
<h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-5">Status</h3>
<div id="sw" class="space-y-4"><p class="text-sm text-gray-400">Memuat...</p></div>
</div>
<div class="lg:col-span-8 bg-white border border-gray-200 rounded-xl p-6">
<div class="flex items-center justify-between mb-3">
<h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wider">Presensi Minggu Ini</h3>
<span class="text-xs text-gray-400">7 hari terakhir</span>
</div>
<svg id="presensiChart" viewBox="0 0 700 200" class="w-full h-48"></svg>
</div>
<div class="lg:col-span-6 bg-white border border-gray-200 rounded-xl overflow-hidden">
<div class="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
<h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wider">Jadwal Hari Ini</h3>
<span class="text-xs text-gray-400" id="jadwalCount">0</span>
</div>
<div id="jw" class="max-h-96 overflow-y-auto"><p class="px-6 py-4 text-sm text-gray-400 italic">Memuat...</p></div>
</div>
<div class="lg:col-span-6 bg-white border border-gray-200 rounded-xl p-6">
<div class="flex items-center justify-between mb-5">
<h3 class="text-sm font-semibold text-gray-500 uppercase tracking-wider">Deadline</h3>
<span class="text-xs text-gray-400" id="deadlineCount">0</span>
</div>
<div id="dw"><p class="text-sm text-gray-400 italic">Memuat...</p></div>
</div>
</div>
</div>

<!-- JADWAL -->
<div id="page-jadwal" class="page-section hidden">
<div class="mb-8 flex items-center justify-between"><h2 class="text-2xl font-bold">Jadwal Kuliah</h2><span class="text-xs text-gray-400" id="jadwalFullCount">—</span></div>
<div id="jadwalFull" class="space-y-4"><p class="text-sm text-gray-400 italic">Memuat...</p></div>
</div>

<!-- DEADLINE -->
<div id="page-deadline" class="page-section hidden">
<div class="mb-8 flex items-start justify-between">
<div><h2 class="text-2xl font-bold">Deadline</h2></div>
<div class="flex gap-2">
<input id="searchDeadline" oninput="doSearch()" type="text" placeholder="Cari..." class="text-sm border border-gray-200 rounded-lg px-3 py-1.5">
<select id="filterDeadline" onchange="doSearch()" class="text-sm border border-gray-200 rounded-lg px-2 py-1.5">
<option value="all">Semua</option>
<option value="Hafizh">Hafizh</option>
<option value="Azfa">Azfa</option>
</select>
</div>
</div>
<div class="bg-white border border-gray-200 rounded-xl p-6">
<div id="deadlineFull"><p class="text-sm text-gray-400 italic">Memuat...</p></div>
</div>
</div>

<!-- HISTORY PRESENSI -->
<div id="page-history" class="page-section hidden">
<div class="mb-8 flex items-center justify-between">
<h2 class="text-2xl font-bold">History Presensi</h2>
<button onclick="clearHistory()" class="text-sm text-red-600 hover:text-red-800">Hapus</button>
</div>
<div class="bg-white border border-gray-200 rounded-xl p-6">
<div id="historyList"><p class="text-sm text-gray-400 italic">Memuat...</p></div>
</div>
</div>

<!-- CALENDAR VIEW -->
<div id="page-calendar" class="page-section hidden">
<div class="mb-8 flex items-center justify-between">
<h2 class="text-2xl font-bold">Kalender Mingguan</h2>
<div class="flex gap-2">
<button onclick="calNav(-1)" class="p-2 border border-gray-200 rounded-lg hover:bg-gray-50">‹</button>
<span class="text-sm font-medium px-3 py-2" id="calWeek">—</span>
<button onclick="calNav(1)" class="p-2 border border-gray-200 rounded-lg hover:bg-gray-50">›</button>
</div>
</div>
<div class="bg-white border border-gray-200 rounded-xl p-6">
<div id="calendarView"><p class="text-sm text-gray-400 italic">Memuat...</p></div>
</div>
</div>

<!-- LOG -->
<div id="page-log" class="page-section hidden">
<div class="mb-8 flex items-center justify-between"><h2 class="text-2xl font-bold">Log Aktivitas</h2><button onclick="document.getElementById('logFull').innerHTML='<p class=\\'text-sm text-gray-400 italic\\'>Kosong</p>'" class="text-sm text-red-600 hover:text-red-800">Hapus</button></div>
<div class="bg-white border border-gray-200 rounded-xl p-6">
<div id="logFull" class="max-h-[600px] overflow-y-auto"><p class="text-sm text-gray-400 italic">Memuat...</p></div>
</div>
</div>

<!-- LOGBOOK -->
<div id="page-logbook" class="page-section hidden">
<div class="mb-8 flex items-center justify-between">
<h2 class="text-2xl font-bold">Logbook Presensi</h2>
<div class="flex items-center gap-2">
<select id="logbookFilter" onchange="renderLogbook()" class="text-sm bg-gray-50 border border-gray-200 rounded-lg px-3 py-1.5">
<option value="all">Semua</option>
<option value="saya">Hafizh (Saya)</option>
<option value="pacar">Azfa (Pacar)</option>
</select>
</div>
</div>
<div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
<div class="bg-white border border-gray-200 rounded-xl p-4"><div class="text-xs text-gray-500 uppercase">Total Presensi</div><div class="text-2xl font-bold text-gray-900" id="lbTotal">—</div></div>
<div class="bg-white border border-gray-200 rounded-xl p-4"><div class="text-xs text-gray-500 uppercase">Hadir</div><div class="text-2xl font-bold text-green-600" id="lbHadir">—</div></div>
<div class="bg-white border border-gray-200 rounded-xl p-4"><div class="text-xs text-gray-500 uppercase">Persentase</div><div class="text-2xl font-bold text-indigo-600" id="lbPct">—</div></div>
</div>
<div id="logbookList" class="space-y-4"></div>
</div>

<!-- SETTINGS -->
<div id="page-settings" class="page-section hidden">
<div class="mb-8"><h2 class="text-2xl font-bold">Settings</h2></div>
<div class="bg-white border border-gray-200 rounded-xl p-6 max-w-lg space-y-4">
<div><label class="text-xs font-semibold text-gray-500 uppercase">Token</label><div class="mt-1 text-sm font-mono bg-gray-50 px-3 py-2 rounded border border-gray-200">********</div></div>
<div><label class="text-xs font-semibold text-gray-500 uppercase">Autopilot</label><div class="mt-1"><span id="settAuto" class="px-2 py-0.5 text-xs font-semibold rounded-full bg-gray-100 text-gray-600">—</span></div></div>
<div><label class="text-xs font-semibold text-gray-500 uppercase">Chat IDs</label><div class="mt-1 text-sm font-mono bg-gray-50 px-3 py-2 rounded border border-gray-200" id="settIds">—</div></div>
<div class="pt-4 border-t border-gray-100 space-y-2">
<button onclick="doCleanup()" class="text-sm bg-red-50 text-red-700 px-4 py-2 rounded-lg hover:bg-red-100 border border-red-200 w-full">Hapus Deadline Lewat</button>
<div class="flex gap-2">
<select id="presensiWho" class="text-sm bg-gray-50 border border-gray-200 rounded-lg px-2 py-2 text-gray-700">
<option value="saya">Hafizh (Saya)</option>
<option value="pacar">Azfa (Pacar)</option>
</select>
<button onclick="triggerPresensi()" class="flex-1 text-sm bg-indigo-50 text-indigo-700 px-4 py-2 rounded-lg hover:bg-indigo-100 border border-indigo-200">Presensi Sekarang</button>
</div>
<button onclick="browserNotifyTest()" class="text-sm bg-blue-50 text-blue-700 px-4 py-2 rounded-lg hover:bg-blue-100 border border-blue-200 w-full">Test Browser Notification</button>
<a href="https://t.me/PresensiUdinus_bot" target="_blank" class="block text-sm bg-green-50 text-green-700 px-4 py-2 rounded-lg hover:bg-green-100 border border-green-200 text-center">Buka Bot di Telegram ↗</a>
</div>
</div>
</div>

</main>
</div>
</div>

<!-- Notification Panel -->
<div id="notifPanel" class="hidden fixed top-16 right-4 w-80 bg-white border border-gray-200 rounded-xl shadow-2xl z-50 p-4 max-h-96 overflow-y-auto">
<div class="flex items-center justify-between mb-3"><h4 class="font-semibold text-sm">Notifikasi</h4><button onclick="closeNotif()" class="text-gray-400 hover:text-gray-600">&times;</button></div>
<div id="notifList" class="space-y-2 text-sm"><p class="text-gray-400 italic">Belum ada notifikasi</p></div>
</div>

<script>
const TOKEN={json.dumps(token)};
let currentData=null;
let calOffset=0;

function esc(s){{return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}}

function g(id){{document.getElementById(id)?.scrollIntoView({{behavior:'smooth'}});}}
function ap(p,m){{return fetch(p+'?token='+TOKEN,{{method:m||'GET'}}).then(r=>r.ok?r.json():{{}}).catch(e=>({{}}));}}
function notify(msg,type='info'){{
const p=document.getElementById('notifPanel');
const l=document.getElementById('notifList');
if(l.querySelector('p.italic'))l.innerHTML='';
const d=document.createElement('div');
d.className='p-2 rounded '+(type==='error'?'bg-red-50 text-red-700':type==='success'?'bg-green-50 text-green-700':'bg-blue-50 text-blue-700');
d.textContent=msg;
l.prepend(d);
document.getElementById('notifBadge').classList.remove('hidden');
}}
function openNotif(){{document.getElementById('notifPanel').classList.toggle('hidden');}}
function closeNotif(){{document.getElementById('notifPanel').classList.add('hidden');}}
function toggleTheme(){{document.documentElement.classList.toggle('dark');localStorage.setItem('theme',document.documentElement.classList.contains('dark')?'dark':'light');}}
function browserNotifyTest(){{if(Notification.permission==='granted')new Notification('Presensi Udinus',{{body:'Test notifikasi berhasil!'}});else if(Notification.permission!=='denied')Notification.requestPermission().then(p=>p==='granted'&&new Notification('Presensi Udinus',{{body:'Test notifikasi berhasil!'}}));else notify('Notifikasi browser diblokir','error');}}
function navigate(page){{
document.querySelectorAll('.nav-link').forEach(l=>{{l.classList.remove('bg-gray-100','text-gray-900','font-medium');l.classList.add('text-gray-600','hover:text-gray-900','hover:bg-gray-50');if(l.dataset.page===page){{l.classList.remove('text-gray-600','hover:text-gray-900','hover:bg-gray-50');l.classList.add('bg-gray-100','text-gray-900','font-medium');}}}});
document.querySelectorAll('.page-section').forEach(s=>s.classList.add('hidden'));
const tgt=document.getElementById('page-'+page);
if(tgt)tgt.classList.remove('hidden');
location.hash=page;
if(page==='history')renderHistory();
if(page==='calendar')renderCalendar();
if(page==='jadwal')renderJadwalFull();
if(page==='deadline')doSearch();
if(page==='log')renderLogFull();
if(page==='logbook')renderLogbook();
}}
function showPageFromHash(){{const h=location.hash.replace('#','')||'dashboard';navigate(h);}}
function clearHistory(){{if(!confirm('Hapus semua history presensi?'))return;ap('/history/clear','POST').then(r=>{{notify('History dihapus','success');renderHistory();}});}}
function exportCSV(){{window.location='/export/csv?token='+TOKEN;}}
function calNav(d){{calOffset+=d;renderCalendar();}}
function pad(n){{return String(n).padStart(2,'0');}}

async function ld(){{
try{{
var s=await ap('/status');
if(!s)return;
currentData=s;
var m=document.getElementById('meta');
if(m)m.textContent=s.waktu||'-';
document.getElementById('lastUpdate').textContent='Update: '+new Date().toLocaleTimeString('id-ID');
var st=s.stat||{{}};
var on=s.autopilot==='Aktif';
var sw=document.getElementById('sw');
if(sw)sw.innerHTML=
'<div class="flex items-center justify-between"><span class="text-sm text-gray-600">Autopilot</span><span class="text-xs font-semibold px-2 py-0.5 rounded-full '+(on?'bg-green-100 text-green-700':'bg-gray-100 text-gray-500')+'">'+s.autopilot+'</span></div>'+
'<div class="h-px bg-gray-100"></div>'+
'<div class="flex items-center justify-between"><span class="text-sm text-gray-600">Pesan</span><span class="font-semibold">'+(st.messages_sent||0)+'</span></div>'+
'<div class="h-px bg-gray-100"></div>'+
'<div class="flex items-center justify-between"><span class="text-sm text-gray-600">Cek Tugas</span><span class="font-semibold">'+(st.tugas_checks||0)+'</span></div>'+
'<div class="h-px bg-gray-100"></div>'+
'<div class="flex items-center justify-between"><span class="text-sm text-gray-600">Presensi</span><span class="font-semibold">'+(st.presensi_done||0)+'</span></div>'+
'<div class="h-px bg-gray-100"></div>'+
'<div class="flex items-center justify-between"><span class="text-sm text-gray-600">Uptime</span><span class="font-semibold">'+(s.uptime||'?')+'</span></div>';
var t=document.getElementById('at');
if(t){{if(on){{t.style.background='#059669';t.children[0].style.transform='translateX(20px)';}}else{{t.style.background='#d1d5db';t.children[0].style.transform='';}}}}
var j=s.jadwal_hari_ini,h='',cnt=0;
if(j)for(var n in j){{h+='<div class="px-6 py-3 border-b border-gray-100"><div class="text-xs font-semibold text-gray-500 uppercase mb-2">'+esc(n)+'</div>';
if(j[n].length===0)h+='<p class="text-sm text-gray-400 italic px-1 pb-2">Libur</p>';
else {{for(var x of j[n]){{h+='<div class="flex items-center gap-3 py-1.5"><div class="w-12 h-10 rounded-lg bg-blue-50 text-blue-700 flex items-center justify-center text-xs font-bold shrink-0">'+esc((x.jam||'').split('-')[0])+'</div><div><div class="text-sm font-medium">'+esc(x.matkul)+'</div><div class="text-xs text-gray-500">'+esc(x.ruang)+'</div></div></div>';cnt++;}}}}
h+='</div>';}}
document.getElementById('jw').innerHTML=h||'<p class="px-6 py-4 text-sm text-gray-400 italic">Data tidak tersedia</p>';
document.getElementById('jadwalCount').textContent=cnt;
renderDeadline(s.deadline?.items||[]);
var lw=document.getElementById('lw');
lw.innerHTML=(!s.log_errors||s.log_errors.length===0)?'<p class="text-sm text-gray-400 italic">Tidak ada error</p>'
:s.log_errors.map(function(l){{return'<div class="flex items-start gap-3 py-2 border-b border-gray-100"><div class="w-2 h-2 bg-red-500 rounded-full mt-1.5 shrink-0"></div><div><p class="text-xs text-gray-500">baru</p><p class="text-sm">'+l+'</p></div></div>';}}).join('');
renderJadwalFull();
renderDeadlineFull();
renderLogFull();
renderSettings();
renderPresensiChart();
}}catch(e){{console.log(e);}}
}}

function renderJadwalFull(){{
ap('/jadwal').then(function(j){{
if(!j)return;
var hariOrder=['senin','selasa','rabu','kamis','jumat','sabtu','minggu'];
var el=document.getElementById('jadwalFull');
var countEl=document.getElementById('jadwalFullCount');
var total=0;
var h='';
for(var n in j){{
total++;
h+='<div class="bg-white border border-gray-200 rounded-xl overflow-hidden"><div class="px-5 py-3 border-b border-gray-100 flex items-center gap-2"><div class="w-8 h-8 rounded-lg bg-gray-900 text-white flex items-center justify-center font-bold text-sm">'+esc(n[0]||'')+'</div><div><h3 class="text-sm font-bold">'+esc(n)+'</h3></div></div><div class="divide-y divide-gray-100">';
for(var i=0;i<hariOrder.length;i++){{
var hari=hariOrder[i];
var slots=j[n][hari]||[];
h+='<div class="px-5 py-3"><div class="text-xs font-semibold text-gray-500 uppercase mb-2">'+hari.charAt(0).toUpperCase()+hari.slice(1)+'</div>';
if(!slots.length)h+='<p class="text-xs text-gray-400 italic py-1">Libur</p>';
else h+=slots.map(function(s){{return'<div class="flex items-center gap-3 py-1.5 text-sm"><div class="w-20 text-gray-500 font-mono text-xs shrink-0">'+esc(s[0])+'</div><div class="flex-1 font-medium">'+esc(s[1])+'</div><div class="text-gray-500 text-xs shrink-0">'+esc(s[2])+'</div></div>';}}).join('');
h+='</div>';
}}
h+='</div></div>';
}}
el.innerHTML=h||'<p class="text-sm text-gray-400 italic">Data tidak tersedia</p>';
if(countEl)countEl.textContent=total+' orang · '+hariOrder.length+' hari';
}});}}

function renderDeadlineFull(){{
if(currentData){{var items=currentData?.deadline?.items||[];doSearchWith(items);}}
else {{ap('/deadline').then(function(d){{doSearchWith(d?.items||[]);}});}}
}}
function doSearchWith(items){{
var q=(document.getElementById('searchDeadline')?.value||'').toLowerCase();
var u=document.getElementById('filterDeadline')?.value||'all';
var f=items.filter(function(i){{return i.name.toLowerCase().indexOf(q)>=0&&(u==='all'||i.account===u);}});
var el=document.getElementById('deadlineFull');
if(!el)return;
document.getElementById('deadlineCount').textContent=f.length;
if(!f.length){{el.innerHTML='<p class="text-sm text-gray-400 italic">Tidak ada deadline</p>';return;}}
el.innerHTML=f.map(function(i){{var u2=i.deadline&&i.deadline.includes('j)');return'<div class="flex items-center justify-between p-3 mb-2 rounded-lg border '+(u2?'bg-red-50 border-red-200':'bg-white border-gray-200')+'"><div><div class="text-sm font-medium">'+esc(i.name)+'</div><div class="text-xs text-gray-500 mt-0.5">'+esc(i.account)+'</div></div><div class="text-sm font-semibold">'+esc((i.deadline||'').split('(')[0])+'</div></div>';}}).join('');
}}
function renderDeadline(items){{
var el=document.getElementById('dw');
if(!el)return;
if(!items||!items.length){{el.innerHTML='<p class="text-sm text-gray-400 italic">Tidak ada deadline aktif</p>';return;}}
el.innerHTML=items.map(function(i){{var u2=i.deadline&&i.deadline.includes('j)');return'<div class="flex items-center justify-between p-3 mb-2 rounded-lg border '+(u2?'bg-red-50 border-red-200':'bg-white border-gray-200')+'"><div><div class="text-sm font-medium">'+esc(i.name)+'</div><div class="text-xs text-gray-500 mt-0.5">'+esc(i.account)+'</div></div><div class="text-sm font-semibold">'+esc((i.deadline||'').split('(')[0])+'</div></div>';}}).join('');
}}
function doSearch(){{
if(currentData?.deadline?.items){{doSearchWith(currentData.deadline.items);}}
else {{ap('/deadline').then(function(d){{doSearchWith(d?.items||[]);}});}}
}}

function renderLogFull(){{
var s=currentData;
var el=document.getElementById('logFull');
if(!s||!s.log_errors||!s.log_errors.length){{el.innerHTML='<p class="text-sm text-gray-400 italic">Tidak ada error</p>';return;}}
el.innerHTML=s.log_errors.map(function(l){{return'<div class="flex items-start gap-3 py-2 border-b border-gray-100"><div class="w-2 h-2 bg-red-500 rounded-full mt-1.5 shrink-0"></div><div><p class="text-xs text-gray-500">'+new Date().toLocaleTimeString('id-ID')+'</p><p class="text-sm">'+esc(l)+'</p></div></div>';}}).join('');
}}

function renderSettings(){{
if(!currentData)return;
var s=currentData;
document.getElementById('settAuto').textContent=s.autopilot;
document.getElementById('settAuto').className='mt-1 px-2 py-0.5 text-xs font-semibold rounded-full '+(s.autopilot==='Aktif'?'bg-green-100 text-green-700':'bg-gray-100 text-gray-500');
}}

function renderPresensiChart(){{
var svg=document.getElementById('presensiChart');
if(!svg)return;
ap('/history/data').then(function(h){{
h=h||[];
var days=['Min','Sen','Sel','Rab','Kam','Jum','Sab'];
var today=new Date().getDay();
var counts=[0,0,0,0,0,0,0];
h.forEach(function(x){{var d=new Date(x.tanggal).getDay();counts[d]++;}});
var max=Math.max.apply(null,counts)||1;
var w=700,h=200,pad=20,bw=(w-2*pad)/7;
var bars=[];
for(var i=0;i<7;i++){{
var c=counts[i];
var bh=(c/max)*(h-2*pad);
var x=pad+i*bw+bw*0.15;
var bw2=bw*0.7;
var y=h-pad-bh;
var color=i===today?'#059669':'#10b981';
bars.push('<rect x="'+x+'" y="'+y+'" width="'+bw2+'" height="'+bh+'" rx="4" fill="'+color+'"><title>'+days[i]+': '+c+'</title></rect>');
bars.push('<text x="'+(x+bw2/2)+'" y="'+(h-5)+'" text-anchor="middle" font-size="12" fill="#6b7280">'+days[i]+'</text>');
if(c>0)bars.push('<text x="'+(x+bw2/2)+'" y="'+(y-5)+'" text-anchor="middle" font-size="11" fill="#111">'+c+'</text>');
}}
svg.innerHTML=bars.join('');
}});
}}

async function renderLogbook(){{
var acct=document.getElementById('logbookFilter').value;
var r=await ap('/logbook?account='+acct);
if(!r){{document.getElementById('logbookList').innerHTML='<p class="text-sm text-gray-400 italic">Gagal memuat logbook</p>';return;}}
document.getElementById('lbTotal').textContent=r.stats.total;
document.getElementById('lbHadir').textContent=r.stats.hadir;
document.getElementById('lbPct').textContent=r.stats.pct+'%';
if(!r.days.length){{document.getElementById('logbookList').innerHTML='<p class="text-sm text-gray-400 italic">Logbook kosong</p>';return;}}
var h='';
r.days.forEach(function(d){{
h+='<div class="bg-white border border-gray-200 rounded-xl p-4"><div class="text-sm font-semibold text-gray-700 mb-3">📅 '+esc(d.date)+'</div>';
d.entries.forEach(function(e){{
var icon=e.status==='hadir'?'✅':'❌';
var color=e.status==='hadir'?'text-green-700':'text-red-700';
h+='<div class="flex items-center justify-between py-2 border-b border-gray-100 last:border-0"><div class="flex items-center gap-3"><span class="text-lg">'+icon+'</span><div><div class="text-sm font-medium text-gray-900">'+esc(e.matkul)+'</div><div class="text-xs text-gray-500">Ruang '+esc(e.ruang)+' • '+(e.account==="saya"?"Hafizh":"Azfa")+'</div></div></div><div class="text-sm font-mono '+color+'">'+esc(e.jam)+'</div></div>';
}});
h+='</div>';
}});
document.getElementById('logbookList').innerHTML=h;
}}

async function renderHistory(){{
var el=document.getElementById('historyList');
var h=await ap('/history/data');
if(!h||!h.length){{el.innerHTML='<p class="text-sm text-gray-400 italic">Belum ada presensi tercatat</p>';return;}}
el.innerHTML=h.slice().reverse().map(function(x){{return'<div class="flex items-center justify-between p-3 mb-2 border border-gray-200 rounded-lg"><div><div class="text-sm font-medium">'+esc(x.matkul)+'</div><div class="text-xs text-gray-500 mt-0.5">'+esc(x.account)+' • '+esc(x.ruang)+'</div></div><div class="text-right"><div class="text-sm font-semibold">'+esc(x.tanggal)+'</div><div class="text-xs text-gray-500">'+esc(x.jam)+'</div></div></div>';}}).join('');
}}

async function renderCalendar(){{
var j=await ap('/jadwal');
if(!j)return;
var now=new Date();
var weekStart=new Date(now);
weekStart.setDate(now.getDate()-now.getDay()+calOffset*7);
var hariOrder=['Minggu','Senin','Selasa','Rabu','Kamis','Jumat','Sabtu'];
var h='<div class="grid grid-cols-7 gap-2">';
for(var i=0;i<7;i++){{
var day=new Date(weekStart);
day.setDate(weekStart.getDate()+i);
var ds=day.toISOString().split('T')[0];
var hariId=hariOrder[i].toLowerCase();
var slots=[];
for(var n in j){{if(j[n][hariId])slots=slots.concat(j[n][hariId].map(function(s){{return{{nama:n,slot:s}};}}));}}
var isToday=ds===now.toISOString().split('T')[0];
h+='<div class="border '+(isToday?'border-green-500 bg-green-50':'border-gray-200')+' rounded-lg p-2 min-h-32"><div class="text-xs font-semibold text-gray-500 mb-2">'+hariOrder[i]+'<br><span class="text-sm text-gray-900 font-bold">'+day.getDate()+'</span></div>';
slots.forEach(function(s){{
h+='<div class="text-[10px] bg-blue-100 text-blue-800 rounded px-1 py-0.5 mb-1 truncate" title="'+esc(s.nama)+': '+esc(s.slot[1])+' ('+esc(s.slot[2])+')">'+esc(s.slot[0].slice(0,5))+' '+esc(s.slot[1].slice(0,12))+'</div>';
}});
h+='</div>';
}}
h+='</div>';
document.getElementById('calendarView').innerHTML=h;
document.getElementById('calWeek').textContent=weekStart.toLocaleDateString('id-ID',{{day:'numeric',month:'short'}})+' - '+new Date(weekStart.getTime()+6*86400000).toLocaleDateString('id-ID',{{day:'numeric',month:'short'}});
}}

async function doCleanup(){{if(!confirm('Hapus semua deadline yang sudah lewat?'))return;var r=await ap('/cleanup','POST');notify('Dihapus: '+r.removed+' deadline','success');await ld();}}
async function ta(){{var r=await ap('/control/toggle-autopilot','POST');notify('Autopilot: '+(r.autopilot?'Aktif':'Nonaktif'),'success');ld();}}
async function tt(){{await ap('/control/trigger-tugas','POST');notify('Tugas check dikirim!','info');}}
async function triggerPresensi(){{var who=document.getElementById('presensiWho').value;var r=await ap('/control/trigger-presensi?who='+who,'POST');notify('Presensi '+(who==='saya'?'Hafizh':'Azfa')+' dikirim!','info');}}
function rf(){{ld();}}
function filterCards(q){{q=q.toLowerCase();var d=currentData?.deadline?.items||[];var f=d.filter(i=>i.name.toLowerCase().includes(q));renderDeadline(f);}}
function filterByUser(u){{var d=currentData?.deadline?.items||[];var f=u==='all'?d:d.filter(i=>i.account===u);renderDeadline(f);}}

// Dark mode auto: manual toggle by user, atau auto berdasarkan jam
var savedTheme=localStorage.getItem('theme');
if(savedTheme==='dark')document.documentElement.classList.add('dark');
else if(!savedTheme){{var h=new Date().getHours();if(h>=18||h<6)document.documentElement.classList.add('dark');}}
ld();
showPageFromHash();
window.addEventListener('hashchange',showPageFromHash);
function tickClock(){{
var d=new Date();
var days=['Min','Sen','Sel','Rab','Kam','Jum','Sab'];
var months=['Jan','Feb','Mar','Apr','Mei','Jun','Jul','Agu','Sep','Okt','Nov','Des'];
var t=days[d.getDay()]+', '+pad(d.getDate())+' '+months[d.getMonth()]+' '+d.getFullYear()+' '+pad(d.getHours())+':'+pad(d.getMinutes())+':'+pad(d.getSeconds())+' WIB';
var el=document.getElementById('realtimeClock');
if(el)el.textContent=t;
}}
if('serviceWorker' in navigator){{navigator.serviceWorker.register('/sw.js');}}
let deferredPrompt=null;
window.addEventListener('beforeinstallprompt',function(e){{e.preventDefault();deferredPrompt=e;var b2=document.getElementById('installBtn');if(b2)b2.classList.remove('hidden');}});
function doInstall(){{if(deferredPrompt){{deferredPrompt.prompt();deferredPrompt.userChoice.then(function(){{deferredPrompt=null;var b2=document.getElementById('installBtn');if(b2)b2.classList.add('hidden');}});}}}}
tickClock();
setInterval(tickClock,1000);
setInterval(ld,30000);
</script>
</body>
</html>
"""


# ============ JSON API endpoints ============
@app.route("/status")
@_require_token
def status():
    now = datetime.now()
    esok = now + timedelta(days=1)
    hari_id = {"monday":"senin","tuesday":"selasa","wednesday":"rabu",
               "thursday":"kamis","friday":"jumat","saturday":"sabtu","sunday":"minggu"}.get(now.strftime("%A").lower(), "")

    schedules = read_json(SCHEDULES_FILE)
    today_saya = schedules.get("saya", {}).get(hari_id, [])
    today_pacar = schedules.get("pacar", {}).get(hari_id, [])

    deadlines = read_json(TASKS_DEADLINE_FILE)
    active_deadline = sum(1 for k in deadlines if k != "notified")

    log_errors = [l for l in read_file_lines(LOG_FILE, 200)
                  if re.search(r"\b(ERROR|CRITICAL)\b", l)][-5:]

    from config import get_stats_snapshot
    return jsonify({
        "uptime": get_uptime(),
        "waktu": now.strftime("%A, %d %B %Y %H:%M WIB"),
        "besok": esok.strftime("%A, %d %B %Y"),
        "autopilot": "Aktif" if get_control("autopilot") else "Nonaktif",
        "stat": get_stats_snapshot(),
        "jadwal_hari_ini": {
            KULINO_ACCOUNTS["saya"]["name"]: [{"jam": j, "matkul": m, "ruang": r} for j, m, r in today_saya],
            KULINO_ACCOUNTS["pacar"]["name"]: [{"jam": j, "matkul": m, "ruang": r} for j, m, r in today_pacar],
        },
        "deadline": {
            "aktif": active_deadline,
            "items": [{"name": v.get("name",k), "deadline": format_tanggal(v.get("deadline_iso","")),
                       "deadline_raw": v.get("deadline_raw",""), "account": v.get("account","")}
                      for k, v in deadlines.items() if k != "notified"],
        },
        "log_errors": log_errors,
        "has_screenshot_tugas": os.path.exists(SCREENSHOT_TUGAS),
        "has_screenshot_presensi": os.path.exists(SCREENSHOT_PRESENSI),
        "control": dict(CONTROL),
    })


@app.route("/jadwal")
@_require_token
def jadwal():
    schedules = read_json(SCHEDULES_FILE)
    data = {}
    for who in ("saya", "pacar"):
        nama = KULINO_ACCOUNTS[who]["name"]
        data[nama] = schedules.get(who, {})
    return jsonify(data)


@app.route("/deadline")
@_require_token
def deadline():
    deadlines = read_json(TASKS_DEADLINE_FILE)
    items = [{"id": k, "name": v.get("name",""), "deadline": v.get("deadline_raw",""),
              "iso": v.get("deadline_iso",""), "course": v.get("course",""),
              "account": v.get("account","")}
             for k, v in deadlines.items() if k != "notified"]
    return jsonify({"total": len(items), "items": items})


@app.route("/log")
@_require_token
def log():
    lines = read_file_lines(LOG_FILE, 500)
    errors = [l for l in lines if re.search(r"\b(ERROR|CRITICAL)\b", l)]
    n = min(int(request.args.get("n", 50)), 500)
    return jsonify({"total": len(lines), "errors": len(errors), "lines": lines[-n:]})


@app.route("/logbook")
@_require_token
def logbook_viewer():
    """Logbook viewer - list of days, filter by user/course."""
    account_filter = request.args.get("account", "all")
    days = []
    if os.path.exists(LOG_DIR):
        for fn in sorted(os.listdir(LOG_DIR), reverse=True):
            if not fn.endswith(".md"):
                continue
            date_str = fn[:-3]
            content = (ROOT / LOG_DIR / fn).read_text(encoding="utf-8")
            entries = []
            for line in content.split("\n"):
                line = line.strip()
                if not line.startswith("- "):
                    continue
                # Parse: "- 14:10-15:50 - BASIS DATA ✅ (saya, Ruang D.2.J)"
                m = re.match(r"- (\S+) - (.+?) ([✅❌]) \(([^,]+), Ruang ([^)]+)\)", line)
                if m:
                    jam, matkul, status, account, ruang = m.groups()
                    status_ok = status == "✅"
                    if account_filter != "all" and account != account_filter:
                        continue
                    entries.append({
                        "jam": jam,
                        "matkul": matkul,
                        "status": "hadir" if status_ok else "absen",
                        "account": account,
                        "ruang": ruang,
                    })
            if entries:
                days.append({"date": date_str, "entries": entries})
    # Stats
    total = sum(len(d["entries"]) for d in days)
    hadir = sum(1 for d in days for e in d["entries"] if e["status"] == "hadir")
    pct = round((hadir / total) * 100, 1) if total else 0
    return jsonify({
        "days": days,
        "stats": {
            "total": total,
            "hadir": hadir,
            "pct": pct,
        }
    })


@app.route("/screenshot/tugas")
@_require_token
def screenshot_tugas():
    if os.path.exists(SCREENSHOT_TUGAS):
        return send_file(SCREENSHOT_TUGAS, mimetype="image/png")
    return jsonify({"error": "Screenshot tidak ditemukan"}), 404


@app.route("/screenshot/presensi")
@_require_token
def screenshot_presensi():
    if os.path.exists(SCREENSHOT_PRESENSI):
        return send_file(SCREENSHOT_PRESENSI, mimetype="image/png")
    return jsonify({"error": "Screenshot tidak ditemukan"}), 404


@app.route("/control")
@_require_token
def control():
    with CONTROL_LOCK:
        return jsonify(dict(CONTROL))


@app.route("/control/toggle-autopilot", methods=["POST"])
@_require_token
def toggle_autopilot():
    with CONTROL_LOCK:
        CONTROL["autopilot"] = not CONTROL["autopilot"]
        new_val = CONTROL["autopilot"]
    logger.info(f"Autopilot toggled via dashboard: {new_val}")
    return jsonify({"autopilot": new_val})


@app.route("/control/trigger-tugas", methods=["POST"])
@_require_token
def trigger_tugas():
    """Trigger cek tugas. Rate-limited 60 detik."""
    with CONTROL_LOCK:
        last_ts = CONTROL.get("last_trigger_tugas_ts", 0)
        now_ts = datetime.now().timestamp()
        if now_ts - last_ts < 60:
            wait = int(60 - (now_ts - last_ts))
            return jsonify({"error": f"Rate limit. Tunggu {wait}s."}), 429
        CONTROL["last_trigger_tugas_ts"] = now_ts
        CONTROL["trigger_tugas"] = (CONTROL.get("trigger_tugas", 0) or 0) + 1
    logger.info("Trigger cek tugas via dashboard")
    return jsonify({"triggered": True})


@app.route("/control/trigger-presensi", methods=["POST"])
@_require_token
def trigger_presensi():
    """Trigger presensi manual untuk akun tertentu. Body: {"who": "saya"} or ?who=pacar.
    Rate-limited 30 detik per akun."""
    data = request.get_json(silent=True) or {}
    who = data.get("who") or request.args.get("who", "saya")
    if who not in ("saya", "pacar"):
        return jsonify({"error": "Invalid who (saya/pacar)"}), 400
    with CONTROL_LOCK:
        last_ts = CONTROL.get(f"last_trigger_presensi_{who}", 0)
        now_ts = datetime.now().timestamp()
        if now_ts - last_ts < 30:
            wait = int(30 - (now_ts - last_ts))
            return jsonify({"error": f"Rate limit. Tunggu {wait}s."}), 429
        CONTROL[f"last_trigger_presensi_{who}"] = now_ts
        CONTROL["trigger_presensi"] = who
    logger.info(f"Trigger presensi via dashboard: {who}")
    return jsonify({"triggered": True, "who": who})


@app.route("/cleanup", methods=["POST"])
@_require_token
def cleanup():
    from storage import cleanup_expired_deadlines
    removed = cleanup_expired_deadlines()
    return jsonify({"removed": removed})


# === PWA support ===
@app.route("/manifest.json")
def pwa_manifest():
    return jsonify({
        "name": "Presensi Udinus",
        "short_name": "Presensi",
        "description": "Dashboard monitoring presensi & tugas Udinus",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#f8f9ff",
        "theme_color": "#0b1c30",
        "icons": [
            {
                "src": "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAxMDAgMTAwIj48dGV4dCB5PSI4MCIgZm9udC1zaXplPSI4MCI+8J+QiTwvdGV4dD48L3N2Zz4=",
                "sizes": "192x192",
                "type": "image/svg+xml"
            }
        ]
    })


@app.route("/sw.js")
def pwa_sw():
    return app.response_class("""
const CACHE='presensi-v1';
self.addEventListener('install',e=>self.skipWaiting());
self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));
self.addEventListener('fetch',e=>{
    e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)));
});
""", mimetype="application/javascript")


# === Health check (no auth — untuk monitoring eksternal) ===
@app.route("/health")
def health():
    """Public health endpoint — untuk monitoring / load balancer."""
    now = datetime.now()
    try:
        delta = now - BOT_START_TIME
        d, r = delta.days, delta.seconds
        uptime = f"{d}h {r//3600}j {(r%3600)//60}m"
    except Exception:
        uptime = "?"
    try:
        stat = get_stats_snapshot()
    except Exception:
        stat = {}
    return jsonify({
        "status": "ok",
        "uptime": uptime,
        "autopilot": bool(get_control("autopilot")),
        "stat": stat,
        "timestamp": now.isoformat(),
    })


# === History presensi ===
@app.route("/history/data")
@_require_token
def history_data():
    return jsonify(load_history())


@app.route("/history/clear", methods=["POST"])
@_require_token
def history_clear():
    save_history([])
    return jsonify({"cleared": True})


# === Export CSV ===
@app.route("/export/csv")
@_require_token
def export_csv():
    schedules = read_json(SCHEDULES_FILE)
    deadlines = read_json(TASKS_DEADLINE_FILE)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Section", "Owner", "Day", "Time", "Course", "Room"])
    for who in ("saya", "pacar"):
        nama = KULINO_ACCOUNTS[who]["name"]
        for hari, slots in schedules.get(who, {}).items():
            for jam, mk, ruang in slots:
                writer.writerow(["Jadwal", nama, hari, jam, mk, ruang])
    for k, v in deadlines.items():
        if k == "notified": continue
        writer.writerow(["Deadline", v.get("account",""), "-", v.get("deadline_raw",""), v.get("name",""), "-"])
    writer.writerow([])
    writer.writerow(["History Presensi"])
    for h in load_history():
        writer.writerow([h.get("tanggal",""), h.get("account",""), h.get("jam",""), h.get("matkul",""), h.get("ruang","")])
    return Response(output.getvalue(), mimetype="text/csv",
                   headers={"Content-Disposition": "attachment;filename=presensi-export.csv"})


# ============ Start Server ============
def start_server(port=8787, debug=False):
    print(f"Dashboard: http://127.0.0.1:{port}?token={DASH_TOKEN}")
    app.run(host="127.0.0.1", port=port, debug=debug, use_reloader=False, threaded=True)


def run_in_thread(port=8787):
    t = threading.Thread(target=start_server, args=(port,), daemon=True)
    t.start()
    return t


if __name__ == "__main__":
    # debug=True HANYA untuk development (Werkzeug debugger bisa execute code arbitrary)
    debug_mode = os.environ.get("FLASK_DEBUG", "0") == "1"
    start_server(debug=debug_mode)
