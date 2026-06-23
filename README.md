# Presensi Bot

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-121%20passing-brightgreen.svg)](./test)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

Telegram bot untuk mengotomasi presensi & monitoring tugas Kulino / MHS SiAdin
Universitas Dian Nuswantoro. Bot ini menjalankan autopilot presensi untuk dua
akun (Hafizh dan Azfa), mengirim reminder deadline H-12 / H-6, dan menyediakan
dashboard web untuk monitoring & kontrol manual.

---

## Daftar Isi

- [Fitur](#fitur)
- [Arsitektur](#arsitektur)
- [Instalasi](#instalasi)
- [Konfigurasi](#konfigurasi)
- [Penggunaan](#penggunaan)
- [Perintah Telegram](#perintah-telegram)
- [Dashboard Web](#dashboard-web)
- [Pengembangan](#pengembangan)
- [Deployment](#deployment)
- [Pre-Deploy Checklist](#pre-deploy-checklist)
- [Struktur Proyek](#struktur-proyek)
- [Keamanan](#keamanan)
- [Lisensi](#lisensi)

---

## Fitur

| Fitur | Deskripsi |
|-------|-----------|
| **Presensi otomatis** | Klik tombol presensi sesuai jadwal kuliah (autopilot) |
| **Cek tugas Kulino** | Parse halaman Kulino, format tabel, kirim screenshot |
| **Reminder deadline** | Notifikasi H-12 & H-6, plus TTS voice (id-ID-ArdiNeural) |
| **Sinkron jadwal** | Auto-fetch jadwal dari MHS SiAdin (Minggu 22:00) |
| **Multi-akun** | Dukungan 2 akun Kulino/MHS (Hafizh + Azfa) |
| **Dashboard web** | Status, jadwal, deadline, history, log, charts |
| **Kontrol manual** | Trigger cek tugas / presensi via dashboard |
| **PWA** | Install sebagai app, dark mode auto, notifikasi browser |
| **Hari libur** | Skip presensi di hari libur nasional (API + cache) |
| **NLP fallback** | Pertanyaan natural language (e.g. "jadwal besok", "kapan basis data") |
| **Logbook** | Catatan presensi harian di `logbook/<date>.md` |
| **Custom alias** | Tambah command custom via Telegram |
| **Single instance** | Mutex mencegah duplikat proses |

---

## Arsitektur

```
Telegram API  ──┐
                ├──►  bot.py (asyncio)  ──►  Playwright  ──►  Kulino / SiAdin
Telegram User  ─┘        │                                │
                         ├──►  proactive_check() loop    (presensi otomatis)
                         ├──►  polling getUpdates()       (commands)
                         └──►  http://*:8787  ──►  Flask  (dashboard)
                                                       │
                                            ┌──────────┴──────────┐
                                            │                     │
                                       File persistence      Background tasks
                                       (JSON, .md)           (deadline, sync)
```

**Stack:** Python 3.11 · asyncio · Playwright · httpx · Flask · pytest

**Thread-safety:** `STATS` & `CONTROL` pakai `threading.Lock` (shared antara
asyncio loop & Flask thread). Single-instance via Windows Named Mutex.

---

## Instalasi

### Prasyarat

- Python 3.11 atau lebih baru
- Akses internet ke `kulino.dinus.ac.id` & `mhs.dinus.ac.id`
- Token Telegram dari [@BotFather](https://t.me/BotFather)
- (Linux/Mac) Browser Chromium terinstall untuk Playwright

### Langkah

```bash
# 1. Clone repository
git clone https://github.com/hafizmuzani011-collab/presensi-udinus-bot.git
cd presensi-udinus-bot

# 2. Install dependencies
pip install -r requirements.txt
playwright install chromium

# 3. Salin dan isi konfigurasi
cp .env.example .env
# Edit .env dengan credentials Anda
```

---

## Konfigurasi

Semua konfigurasi melalui environment variables di file `.env`:

| Variable | Wajib | Deskripsi |
|----------|-------|-----------|
| `TELEGRAM_BOT_TOKEN` | ✅ | Token bot dari @BotFather |
| `KULINO_SAYA_NIM` / `KULINO_SAYA_PASS` | ✅ | Akun Kulino Hafizh |
| `KULINO_PACAR_NIM` / `KULINO_PACAR_PASS` | ✅ | Akun Kulino Azfa |
| `MHS_SAYA_NIM` / `MHS_SAYA_PASS` | ✅ | Akun MHS Hafizh |
| `MHS_PACAR_NIM` / `MHS_PACAR_PASS` | ✅ | Akun MHS Azfa |
| `DASH_TOKEN` | ✅ | Token akses dashboard (generate pakai `python -c "import secrets; print(secrets.token_urlsafe(32))"`) |
| `CLAUDEFIRE_API_KEY` | — | API key LLM (opsional, untuk smart reply) |
| `LLM_MODEL` | — | Model LLM (default: `deepseek-v4-flash-free`) |

Generate `DASH_TOKEN` baru:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Penggunaan

### Lokal (development)

```bash
python bot.py
```

### Linux (production)

```bash
# Edit presensi-bot.service, set DASH_TOKEN env var
sudo cp presensi-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now presensi-bot
sudo systemctl status presensi-bot
```

Lihat `bot.log` (rotating 10 MB × 5 files) untuk log aplikasi.

---

## Perintah Telegram

| Perintah | Alias | Fungsi |
|----------|-------|--------|
| `/start` | `halo`, `hai`, `hi` | Mulai bot |
| `/help` | `bantuan` | Daftar perintah |
| `/status` | `status` | Status bot & statistik |
| `/tanggal` | `tanggal` | Tanggal & hari ini |
| `cek tugas` | — | Cek tugas Kulino (Hafizh) |
| `cek tugas pacar` | — | Cek tugas Kulino (Azfa) |
| `jadwal` | — | Jadwal hari ini (kedua akun) |
| `jadwal senin` | `jadwal rabu`, dst. | Jadwal hari spesifik |
| `jadwal update` | `jadwal refresh`, `jadwal sinkron` | Sinkron jadwal dari MHS |
| `deadline` | `tugas deadline`, `list deadline` | List deadline tersimpan |
| `statustugas <nama>` | `done <nama>` | Tandai tugas selesai |
| `cleanup` | `bersihkan` | Hapus deadline lewat |
| `presensi` | `hadir` | Presensi manual |
| `presensi pacar` | — | Presensi Azfa |
| `autopilot on` | — | Nyalakan autopilot |
| `autopilot off` | `autopilot nonaktif` | Matikan autopilot |
| `ujian` | — | Jadwal UTS / UAS Hafizh |
| `ujian pacar` | — | Jadwal UTS / UAS Azfa |
| `libur` | `tanggal merah`, `hari libur` | Daftar hari libur nasional |
| `logbook` | `catatan` | Riwayat presensi |
| `addalias <nama> <cmd>` | — | Tambah command alias |
| `delalias <nama>` | — | Hapus alias |

Bot juga mendukung **inline keyboard** (tombol interaktif) untuk quick action
presensi dari notifikasi reminder.

---

## Dashboard Web

Buka `http://localhost:8787/?token=<DASH_TOKEN>` di browser.

Halaman yang tersedia:

- **Dashboard** — Overview status, chart presensi mingguan, deadline
- **Jadwal** — Jadwal lengkap kedua akun, filter hari
- **Deadline** — List deadline, filter akun, search
- **Presensi** — History presensi
- **Logbook** — Catatan harian dengan statistik hadir/persentase
- **Calendar** — Kalender mingguan visual
- **Log** — Log error dari `bot.log`
- **Settings** — Toggle autopilot, presensi manual, test notifikasi

**Endpoints API** (perlu token):

- `GET /status` — Snapshot status bot
- `GET /jadwal` — Jadwal lengkap
- `GET /deadline` — List deadline
- `GET /logbook` — Logbook presensi
- `GET /history/data` — History presensi
- `GET /screenshot/tugas` — Screenshot bukti cek tugas
- `GET /screenshot/presensi` — Screenshot bukti presensi
- `POST /control/toggle-autopilot` — Toggle autopilot
- `POST /control/trigger-tugas` — Trigger cek tugas (rate limit 60s)
- `POST /control/trigger-presensi?who=saya|pacar` — Trigger presensi (rate limit 30s)
- `POST /cleanup` — Hapus deadline lewat
- `GET /health` — Health check (public, no auth)

---

## Pengembangan

### Setup development

```bash
git clone https://github.com/hafizmuzani011-collab/presensi-udinus-bot.git
cd presensi-udinus-bot
pip install -r requirements.txt
playwright install chromium

# Enable pre-commit hook (lint + test otomatis)
git config core.hooksPath .githooks
```

### Menjalankan test

```bash
# Semua test
python -m pytest test/ -v

# Test specific
python -m pytest test/test_nlp.py -v

# Dengan coverage
pip install pytest-cov
python -m pytest test/ --cov=. --cov-report=term-missing
```

**Test coverage: 121 tests, 8 files**

| File | Tests | Cakupan |
|------|-------|---------|
| `test_utils.py` | 20 | `parse_deadline` (ISO, EN/ID, AM/PM, relative) |
| `test_nlp.py` | 21 | `parse_question`, `answer_jadwal`, `answer_presensi` |
| `test_storage.py` | 20 | Chat IDs, offset, presensi done, tasks, schedules, backup |
| `test_process_remind.py` | 9 | Reminder H-6/H-12, dedup, priority |
| `test_schedule.py` | 11 | `get_schedule_for` formatter |
| `test_commands.py` | 19 | `/start`, `/help`, `/status`, `presensi`, dll. |
| `test_scrape.py` | 6 | `extract_tasks_from_text` |
| `test_dashboard_api.py` | 7 | Rate limit 429, health endpoint |

### Linting

```bash
# Auto-fix unused imports/vars
python -m ruff check --fix --select=F401,F841 *.py
```

### Commit convention

Format: `<type>: <subject>`

- `feat:` — fitur baru
- `fix:` — bug fix
- `chore:` — refactor tanpa behavior change
- `test:` — tambah/ubah test
- `docs:` — dokumentasi

---

## Deployment

### STB Armbian (production)

1. Setup Python 3.11:
   ```bash
   sudo apt update && sudo apt install python3-pip python3-venv
   ```

2. Copy project & install dependencies:
   ```bash
   cd /opt && git clone <repo-url> presensi-udinus
   cd presensi-udinus
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   .venv/bin/playwright install chromium
   ```

3. Salin `.env`:
   ```bash
   cp .env.example .env && nano .env
   ```

4. Setup systemd:
   ```bash
   sudo cp presensi-bot.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now presensi-bot
   ```

5. Verify:
   ```bash
   sudo systemctl status presensi-bot
   sudo journalctl -u presensi-bot -f
   ```

### Update deployment

```bash
cd /opt/presensi-udinus
git pull
.venv/bin/pip install -r requirements.txt
sudo systemctl restart presensi-bot
```

---

## Pre-Deploy Checklist

Sebelum restart/deploy bot, validasi:

- [ ] `python -m pytest test/ -q` lulus (121 tests)
- [ ] `.env` ada dan semua credentials valid
- [ ] `DASH_TOKEN` di-set: `python -c "import os; print(os.getenv('DASH_TOKEN'))"`
- [ ] `playwright install chromium` sudah jalan
- [ ] Test manual: `python bot.py` lalu `/start` → reply OK
- [ ] Dashboard bisa dibuka di `http://localhost:8787/?token=<DASH_TOKEN>`
- [ ] `GET /health` return `{"status": "ok", ...}`
- [ ] Cek log: `bot.log` terakhir tidak ada error fatal
- [ ] `schedules.json` & `tasks_deadlines.json` valid JSON
- [ ] STB: `systemctl status presensi-bot` active

---

## Struktur Proyek

```
presensi-udinus-bot/
├── bot.py               # Main entry point & command handler
├── config.py            # Konfigurasi, env loader, STATS (thread-safe)
├── storage.py           # Persistence layer (chat IDs, schedules, deadlines)
├── utils.py             # Schedule formatter & deadline parser
├── tg.py                # Telegram API client (httpx connection pool)
├── telegram_bot.py      # Kulino & SiAdin scraper
├── web_dashboard.py     # Flask dashboard + JSON API
├── browser.py           # Playwright browser singleton
├── instance_lock.py     # Windows Named Mutex (anti-duplikat)
├── tts.py               # TTS voice reminder (edge-tts)
├── aliases.py           # Custom command aliases
├── nlp.py               # Natural language parser
├── conftest.py          # pytest configuration
├── .githooks/           # Pre-commit lint + test runner
├── .github/workflows/   # CI (pytest + ruff)
├── test/                # 121 tests, 8 files
├── requirements.txt     # Python dependencies
├── .env.example         # Template konfigurasi
├── presensi-bot.service # systemd unit untuk STB
├── run.bat / run.sh     # Launcher scripts
└── README.md
```

---

## Keamanan

- **Credentials:** File `.env` di-`.gitignore`, **JANGAN** commit
- **Token Telegram:** WAJIB di-set, tidak ada fallback default
- **Dashboard token:** Generate random (`secrets.token_urlsafe(32)`), required untuk akses
- **XSS:** Semua data user di-escape via JS `esc()` di template dashboard
- **Single-instance:** Mutex mencegah duplikat proses (Named Mutex Windows, PID file Linux)
- **Rate limit:** Dashboard trigger 60s (cek tugas) / 30s (presensi per akun) — 429 kalau terlalu cepat
- **Log rotation:** `bot.log` max 10 MB × 5 files = 50 MB total
- **Auto-backup:** `schedules.json`, `tasks_deadlines.json`, `presensi_history.json` di-backup tiap jam ke `.bak`

### Jika credentials bocor

1. **Segera rotate:**
   - Token Telegram: @BotFather → `/mybots` → Revoke
   - Password Kulino/MHS: login website, ganti password
2. **Update `.env`** dengan credentials baru
3. **Restart bot:** `systemctl restart presensi-bot`
4. **Pantau `bot.log`** untuk akses tidak sah

---

## Lisensi

MIT — lihat [LICENSE](./LICENSE)

---

## Kontribusi

Pull request welcome. Untuk perubahan besar, buka issue dulu untuk diskusi.

Untuk development lokal, setup pre-commit hook (lihat [Pengembangan](#pengembangan))
supaya lint + test jalan otomatis sebelum commit.
