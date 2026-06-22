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
pip install playwright httpx openai flask
playwright install chromium
```

### 2. Konfigurasi credentials

Edit `config.py` atau set environment variable:

| Variable | Fungsi |
|----------|--------|
| `TELEGRAM_BOT_TOKEN` | Token bot Telegram |
| `KULINO_SAYA_NIM` / `KULINO_SAYA_PASS` | Akun Kulino Hafizh |
| `KULINO_PACAR_NIM` / `KULINO_PACAR_PASS` | Akun Kulino Azfa |
| `MHS_SAYA_NIM` / `MHS_SAYA_PASS` | Akun MHS Hafizh |
| `MHS_PACAR_NIM` / `MHS_PACAR_PASS` | Akun MHS Azfa |

### 3. Jalankan

```bash
python bot.py
```

### 4. Buka Dashboard

```
http://127.0.0.1:8787/?token=presensi123
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
autopilot    - Toggle presensi otomatis
presensi     - Presensi manual
/status      - Status bot
/tanggal     - Info hari ini
```

## Struktur Project

```
├── bot.py               # Main entry
├── config.py            # Konfigurasi
├── storage.py           # File I/O
├── utils.py             # Helper functions
├── tg.py                # Telegram API
├── telegram_bot.py      # Scraper (Kulino + MHS)
├── web_dashboard.py     # Dashboard web (Flask)
├── browser.py           # Shared Playwright
├── instance_lock.py     # Anti duplikat
├── run.bat / run.sh     # Launcher
├── schedules.json       # Data jadwal
└── tasks_deadlines.json # Deadline cache
```

## STB (Armbian)

1. Copy project ke STB
2. Install Python + Playwright
3. `chmod +x run.sh && ./run.sh`
4. Atau `sudo systemctl enable` via `presensi-bot.service`

## License

MIT
