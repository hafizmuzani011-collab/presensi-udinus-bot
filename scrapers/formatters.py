"""Formatters — KHS, attendance, etc."""
import logging

logger = logging.getLogger(__name__)


def format_khs_message(khs: dict, name: str) -> str:
    if not khs.get("matkul"):
        return f"*KHS {name}*\n\nBelum ada nilai semester ini."
    lines = [f"*KHS {name}*", "",
             "`",
             f"{'SKS':>3} {'HURUF':<6} {'BOBOT':<6}  MATAKULIAH",
             "-" * 50]
    for m in khs["matkul"]:
        lines.append(f"{m['sks']:>3}  {m['huruf']:<6} {m['bobot']:<6.2f}  {m['matkul'][:35]}")
    lines.append("-" * 50)
    lines.append(f"{sum(m['sks'] for m in khs['matkul']):>3}  Total SKS")
    lines.append("`")
    lines.append("")
    if khs.get("ip_semester") is not None:
        ip = khs["ip_semester"]
        star = "\U0001F31F" if ip >= 3.5 else ("\U0001F44D" if ip >= 3.0 else "\U0001F4DA")
        lines.append(f"{star} *IP:* {ip:.2f}")
    if khs.get("ipk") is not None:
        lines.append(f"\U0001F393 *IPK:* {khs['ipk']}")
    return "\n".join(lines)


def format_attendance_message(results: list[dict], name: str, year: int, month: int) -> str:
    if not results:
        return f"*Presensi {name}*\n\nBelum ada data bulan {month}/{year}."

    lines = [f"*Presensi {name}* — {month}/{year}", ""]
    warning_count = 0
    for r in results:
        pct = r["pct"]
        if pct >= 90:
            icon = "\U0001F31F"
        elif pct >= 75:
            icon = "\U00002705"
        else:
            icon = "\U000026A0"
            warning_count += 1
        lines.append(
            f"{icon} {r['matkul'][:30]:30s} "
            f"{r['hadir']:>2}/{r['total']:<2} "
            f"({pct:.1f}%)"
        )

    lines.append("")
    if warning_count:
        lines.append(f"\U000026A0 *{warning_count} matkul di bawah 75%* \u2014 bahaya DO!")
        for r in results:
            if r["pct"] < 75:
                need = int(0.75 * r["total"]) - r["hadir"]
                lines.append(f"  \u2022 {r['matkul'][:25]}: butuh {need}x lagi")
    else:
        lines.append("\U00002705 Semua matkul aman (>= 75%)")
    return "\n".join(lines)
