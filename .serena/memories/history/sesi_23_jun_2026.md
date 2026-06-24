## Perbaikan (Sesi 23 Juni 2026)

*   **Pembersihan Linter**: Memperbaiki semua warning Ruff (F841, E402, E701, E741) di `bot.py`, `scrapers/kulino.py`, `scrapers/siadin.py`, dan `web_dashboard.py`.
*   **Auto-Retry Presensi**: Menambahkan logic retry 3x dengan backoff 5 detik dan pelaporan progress ke Telegram pada fungsi `do_presensi_siadin`.
*   **Decouple UI Dashboard**: Template HTML inline di `web_dashboard.py` dipindah ke `templates/dashboard.html`.
*   **Perbaikan Script Launcher**: Menambahkan `cd /d "%~dp0\.."` pada `scripts/run.bat` supaya command prompt tidak close sendiri (crash) saat launch bot dari folder `scripts/`.
*   **Fitur Rekap IPK & KHS**: 
    *   Tiap cek nilai (`/khs`), IP semester (IPS) dan IPK kumulatif akan otomatis disimpan berdasar pendeteksian kalender ganjil/genap (`KHS_HISTORY_FILE`).
    *   Ada menu baru **Nilai & KHS** di web dashboard dengan _bar chart_ interaktif (SVG) untuk melacak progress IP.
*   **Rombak UI Calendar**: Jadwal Hafizh dan Azfa di-color code (Indigo vs Emerald), desain card lebih rapi menampilkan jam, matkul, dan ruang tanpa _tooltip_ hover.

**Semua perubahan sudah di-commit, push ke repository GitHub `presensi-udinus-bot`, dan 180 unit test tetap berstatus Passed.**