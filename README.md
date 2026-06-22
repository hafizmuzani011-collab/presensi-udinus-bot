# 🤖 Presensi Udinus Bot

Telegram bot untuk otomatisasi presensi & cek tugas Kulino/MHS Udinus.

## Fitur

| Fitur | Status |
|-------|--------|
| ✅ Cek tugas Kulino (table + screenshot) | ✅ |
| ✅ Jadwal kuliah dari MHS (sinkron otomatis) | ✅ |
| ✅ Reminder deadline H-12 / H-6 | ✅ |
| ✅ Autopilot presensi (Hafizh + Azfa) | ✅ |
| ✅ Dashboard web monitoring | ✅ |
| ✅ Jadwal update via MHS | ✅ |
| ✅ Autopilot toggle | ✅ |

## Cara Pakai

### 1. Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. Konfigurasi credentials

Copy `.env.example` ke `.env` lalu isi:

```bash
cp .env.example .env
# Edit .env dengan credentials Anda
```

| Variable | Fungsi |
|----------|--------|
| `TELEGRAM_BOT_TOKEN` | Token bot Telegram dari @BotFather |
| `KULINO_SAYA_NIM` / `KULINO_SAYA_PASS` | Akun Kulino Hafizh |
| `KULINO_PACAR_NIM` / `KULINO_PACAR_PASS` | Akun Kulino Azfa |
| `MHS_SAYA_NIM` / `MHS_SAYA_PASS` | Akun MHS Hafizh |
| `MHS_PACAR_NIM` / `MHS_PACAR_PASS` | Akun MHS Azfa |
| `DASH_TOKEN` | Token akses dashboard (WAJIB) |

### 3. Jalankan

```bash
python bot.py
```

### 4. Buka Dashboard

```
http://127.0.0.1:8787/?token=<DASH_TOKEN_dari_env>
```

## Perintah Telegram

```
/start       - Mulai bot
/help        - Bantuan
cek tugas    - Cek tugas Kulino (Hafizh)
cek tugas pacar - Cek tugas Azfa
jadwal       - Jadwal hari ini
jadwal senin - Jadwal spesifik
jadwal update - Sinkron dari MHS
dewadline    - Lihat deadline
statustugas <nama> - Tandai selesai
cleanup      - Hapus deadline lewat
autopilot on/off - Toggle presensi otomatis
presensi     - Presensi manual
/status      - Status bot
/tanggal     - Info hari ini
```

## Pre-Deploy Checklist

Sebelum restart/deploy bot, validasi:

- [ ] `python -m pytest test/ -q` lulus (semua 110+ tests passed)
- [ ] `.env` ada dan semua credentials valid (NIM, password, token)
- [ ] `DASH_TOKEN` di-set (cek: `python -c "import os; print(os.getenv('DASH_TOKEN'))"`)
- [ ] `playwright install chromium` sudah jalan
- [ ] Test manual: `python bot.py` — `/start` reply, `/status` reply
- [ ] Dashboard bisa dibuka di `http://localhost:8787/?token=<DASH_TOKEN>`
- [ ] Cek log: `bot.log` terakhir tidak ada error fatal
- [ ] `schedules.json` & `tasks_deadlines.json` ada dan valid JSON
- [ ] Kalau pakai STB: `presensi-bot.service` jalan, `systemctl status presensi-bot`

## Struktur Project

```
├── bot.py               # Main entry
├── config.py            # Konfigurasi & STATS lock
├── storage.py           # File I/O persistence
├── utils.py             # Schedule formatter, deadline parser
├── tg.py                # Telegram API (httpx pool)
├── telegram_bot.py      # Kulino & SiAdin scraper
├── web_dashboard.py     # Dashboard Flask
├── browser.py           # Playwright singleton
├── instance_lock.py     # Single-instance mutex
├── tts.py               # TTS reminder
├── aliases.py           # Custom commands
├── nlp.py               # Natural language parser
├── conftest.py          # pytest config
├── .githooks/           # Pre-commit test runner
├── test/                # 110+ tests
└── requirements.txt     # Dependencies
```

## Development

### Run tests
```bash
python -m pytest test/ -v
```

### Install pre-commit hook (auto run tests)
```bash
git config core.hooksPath .githooks
```

Hook ini akan abort commit kalau test gagal. Skip dengan `git commit --no-verify` kalau urgent.

## STB (Armbian)

1. Copy project ke STB
2. Install Python + Playwright
3. `chmod +x run.sh && ./run.sh`
4. Atau `sudo systemctl enable` via `presensi-bot.service`

## Security Notes

- File `.env` di-gitignore, JANGAN commit
- `DASH_TOKEN` wajib (gak ada fallback default)
- XSS escape di semua dashboard renders
- Mutex anti-duplikat instance (Named Mutex di Windows)
- Rate limit 60s/30s di dashboard triggers
- RotatingFileHandler: log max 10MB × 5 files

## License

MIT
