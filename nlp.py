"""Natural language parser untuk jadwal & deadline.

User bisa tanya dengan bahasa sehari-hari:
- "besok ada apa?"
- "apa jadwal selasa"
- "kapan basis data"
- "deadline berikutnya"
- "kapan presensi"
"""
import re
from datetime import datetime, timedelta

from constants import HARI_MAP, KATA_HARI
from config import MHS_ACCOUNTS

HARI = HARI_MAP  # full mapping: indo + eng + abbrev -> indo
_TZ_WIB = __import__('datetime').timezone(__import__('datetime').timedelta(hours=7))


def _now_wib():
    """Return datetime.now() in WIB timezone."""
    return datetime.now(tz=_TZ_WIB)


def parse_question(text: str) -> dict:
    """Parse pertanyaan user → struktur intent.

    Return dict dengan key:
    - intent: 'jadwal' | 'deadline' | 'presensi' | 'unknown'
    - hari: 'senin' | ... | None
    - date: ISO date string | None
    - keyword: kata kunci mata kuliah / topik | None
    - relative: 'besok' | 'hari ini' | 'lusa' | None
    """
    text = text.lower().strip()
    result = {
        "intent": "unknown",
        "hari": None,
        "date": None,
        "keyword": None,
        "relative": None,
    }

    # Detect intent
    if any(w in text for w in ["jadwal", "kelas", "kuliah", "matkul", "ngajar"]):
        result["intent"] = "jadwal"
    elif any(w in text for w in ["deadline", "tugas", "tenggat", "due", "kumpul"]):
        result["intent"] = "deadline"
    elif any(w in text for w in ["presensi", "hadir", "absen", "kelasnya", "kehadiran"]):
        result["intent"] = "presensi"
    else:
        # Default ke jadwal kalau menyebut hari atau relative time
        for h in KATA_HARI:
            if re.search(r"\b" + re.escape(h) + r"\b", text):
                result["intent"] = "jadwal"
                break
        if result["intent"] == "unknown" and any(w in text for w in ["besok", "lusa", "hari ini"]):
            result["intent"] = "jadwal"

    # Detect relative time (lusa first, before besok)
    if "lusa" in text:
        result["relative"] = "lusa"
        target = _now_wib() + timedelta(days=2)
        result["date"] = target.strftime("%Y-%m-%d")
        result["hari"] = HARI.get(target.strftime("%A").lower())
    elif "besok" in text or "tomorrow" in text or "besoknya" in text:
        result["relative"] = "besok"
        target = _now_wib() + timedelta(days=1)
        result["date"] = target.strftime("%Y-%m-%d")
        result["hari"] = HARI.get(target.strftime("%A").lower())
    elif "hari ini" in text or "sekarang" in text or "today" in text:
        result["relative"] = "hari ini"
        result["date"] = _now_wib().strftime("%Y-%m-%d")
        result["hari"] = HARI.get(_now_wib().strftime("%A").lower())

    # Detect specific day
    for h, h_id in HARI_MAP.items():
        if re.search(r"\b" + re.escape(h) + r"\b", text):
            result["hari"] = h_id
            break

    # Detect keyword (mata kuliah)
    stop_words = {"jadwal", "kelas", "kuliah", "apa", "kapan", "siang", "malam",
                  "hari", "ini", "besok", "lusa", "minggu", "aja", "dong",
                  "deh", "ya", "kok", "sih", "presensi", "deadline", "tugas",
                  "tenggat", "ada", "di", "ke", "dari", "untuk", "yang", "mau"}
    words = re.findall(r"\b[a-z]{4,}\b", text)
    keywords = [w for w in words if w not in stop_words and w not in KATA_HARI]
    if keywords:
        result["keyword"] = " ".join(keywords)

    return result


def answer_jadwal(intent_data: dict, schedules: dict, hari_key: str = "saya") -> str:
    """Buat jawaban natural language untuk pertanyaan jadwal."""
    hari = intent_data.get("hari")
    if not hari:
        return "Maaf, hari yang kamu maksud apa? Coba: 'jadwal selasa' atau 'jadwal besok'."

    slots = schedules.get(hari_key, {}).get(hari, [])
    if not slots:
        return f"📅 Hari {hari.title()} tidak ada jadwal kuliah. Libur!"

    if intent_data.get("keyword"):
        keyword = intent_data["keyword"].lower()
        filtered = [s for s in slots if keyword in s[1].lower()]
        slots = filtered  # set langsung, kosong = tidak cocok

    relative = intent_data.get("relative", "")
    header = f"📅 Jadwal {hari.title()}"
    if relative:
        header += f" ({relative})"

    if len(slots) == 0:
        return f"📅 Hari {hari.title()} ada jadwal, tapi tidak ada yang cocok dengan '{intent_data['keyword']}'."

    lines = [header + ":"]
    for jam, mk, ruang in slots:
        lines.append(f"  • 🕐 {jam} - {mk} (Ruang {ruang})")
    return "\n".join(lines)


def answer_presensi(intent_data: dict, schedules: dict) -> str:
    """Buat jawaban natural language untuk pertanyaan presensi."""
    hari = intent_data.get("hari")
    if not hari:
        return "Maaf, hari yang kamu maksud apa? Coba: 'kapan presensi selasa' atau 'besok ada kelas?'"

    slots = []
    for who in MHS_ACCOUNTS:
        s = schedules.get(who, {}).get(hari, [])
        for jam, mk, ruang in s:
            slots.append((who, jam, mk, ruang))

    if not slots:
        return f"📅 Hari {hari.title()} tidak ada presensi. Libur!"

    lines = [f"📅 Presensi hari {hari.title()}:"]
    for who, jam, mk, ruang in slots:
        nama = MHS_ACCOUNTS[who]["name"]
        lines.append(f"  • 🕐 {jam} - {mk} ({nama}, Ruang {ruang})")
    return "\n".join(lines)
