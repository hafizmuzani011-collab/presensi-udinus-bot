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
from flask import Flask, jsonify, request, send_file, abort, Response, make_response
from config import (
    LOG_FILE, TASKS_DEADLINE_FILE, SCHEDULES_FILE,
    SCREENSHOT_TUGAS, SCREENSHOT_PRESENSI,
    KULINO_ACCOUNTS, BOT_START_TIME, LOG_DIR,
    get_stats_snapshot,
    CONTROL, CONTROL_LOCK, get_control, PRESENSI_HISTORY_FILE, KHS_HISTORY_FILE,
)
from constants import HARI_ID

# === Path ke file2 bot ===
ROOT = Path(__file__).parent

# === Logger ===
logger = logging.getLogger(__name__)

# === Auth token (WAJIB dari env) ===
DASH_TOKEN = os.environ.get("DASH_TOKEN")
if not DASH_TOKEN:
    raise RuntimeError("DASH_TOKEN tidak ditemukan! Set environment variable DASH_TOKEN, atau isi di .env")
logger.info("Dashboard token loaded from DASH_TOKEN env")

app = Flask(__name__)

# === Control & History (in-memory, share dengan bot.py) ===
# CONTROL, CONTROL_LOCK, get_control, set_control, consume_control
# di-import dari config.py agar tidak circular.
HISTORY_PATH = Path(str(PRESENSI_HISTORY_FILE))  # convert str -> Path
def load_history():
    if not HISTORY_PATH.exists():
        return []
    try:
        return json.loads(HISTORY_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return []

def save_history(items):
    HISTORY_PATH.write_text(json.dumps(items, indent=2, ensure_ascii=False))


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
    return [ln.strip() for ln in _log_cache["lines"][-n:] if ln.strip()]

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

def _get_token() -> str | None:
    """Extract token from: cookie > header > URL param."""
    return (request.cookies.get("dash_token")
            or request.headers.get("X-Dash-Token")
            or request.args.get("token"))


def _require_token_decorator(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        tok = _get_token()
        if tok != DASH_TOKEN:
            abort(401)
        return f(*args, **kwargs)
    return wrapper

_require_token = _require_token_decorator


def _set_token_cookie(response, token: str | None) -> None:
    """Set HttpOnly secure cookie if token matches."""
    if token == DASH_TOKEN:
        response.set_cookie("dash_token", token, httponly=True, samesite="Lax",
                           max_age=86400 * 30)  # 30 days


# ============ API Routes ============
@app.route("/")
def index():
    token = _get_token()
    resp = make_response(dashboard_page(token))
    _set_token_cookie(resp, token or DASH_TOKEN)
    return resp

@app.route("/<page>")
def pages(page):
    if page in ("dashboard","jadwal","deadline","history","khs","calendar","log","settings","logbook"):
        token = _get_token()
        resp = make_response(dashboard_page(token))
        _set_token_cookie(resp, token or DASH_TOKEN)
        return resp
    return index()

def dashboard_page(token, page="dashboard"):
    nav_items = [
        ("dashboard", "Dashboard", "M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"),
        ("jadwal", "Jadwal", "M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"),
        ("deadline", "Deadline", "M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"),
        ("history", "Presensi", "M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"),
        ("khs", "Nilai & KHS", "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"),
        ("logbook", "Logbook", "M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z"),
        ("calendar", "Calendar", "M8 7V3m0 2.586l5.293-5.293a1 1 0 011.414 0L20 5.414V17a2 2 0 01-2 2H6a2 2 0 01-2-2V5a2 2 0 012-2h2z"),
        ("log", "Log", "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"),
        ("settings", "Settings", "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"),
    ]
    nav_html = ""
    for key, label, path_svg in nav_items:
        nav_html += f'<a onclick="navigate(\x27{key}\x27)" class="nav-link flex items-center gap-3 px-3 py-2.5 text-sm rounded-lg cursor-pointer" data-page="{key}"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="{path_svg}"/></svg>{label}</a>'

    template_path = ROOT / "templates" / "dashboard.html"
    html = template_path.read_text(encoding="utf-8")
    
    html = html.replace("{{nav_html}}", nav_html)
    html = html.replace("{{token_json}}", json.dumps(token))
    
    return "<!DOCTYPE html>\n" + html

# ============ JSON API endpoints ============
@app.route("/status")
@_require_token
def status():
    now = datetime.now()
    esok = now + timedelta(days=1)
    hari_id = HARI_ID.get(now.strftime("%A").lower(), "")

    schedules = read_json(SCHEDULES_FILE)
    today_saya = schedules.get("saya", {}).get(hari_id, [])
    today_pacar = schedules.get("pacar", {}).get(hari_id, [])

    deadlines = read_json(TASKS_DEADLINE_FILE)
    active_deadline = sum(1 for k in deadlines if k != "notified")

    log_errors = [ln for ln in read_file_lines(LOG_FILE, 200)
                  if re.search(r"\b(ERROR|CRITICAL)\b", ln)][-5:]

    from config import get_stats_snapshot  # already imported top-level
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
    errors = [ln for ln in lines if re.search(r"\b(ERROR|CRITICAL)\b", ln)]
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


# === KHS History ===
@app.route("/khs/history")
@_require_token
def khs_history():
    if not os.path.exists(KHS_HISTORY_FILE):
        return jsonify({})
    try:
        with open(KHS_HISTORY_FILE, encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({})


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
        if k == "notified":
            continue
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
