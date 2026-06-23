"""KHS scraper — daftar nilai, IPK, komponen."""
import logging
import re

logger = logging.getLogger(__name__)

MHS_URL = "https://mhs.dinus.ac.id/"

BOBOT = {"A": 4.0, "A-": 3.75, "AB": 3.5, "B+": 3.5, "B": 3.0,
         "B-": 2.75, "BC": 2.5, "C+": 2.5, "C": 2.0, "D": 1.0,
         "E": 0.0, "K": 0.0}


async def scrape_khs(page, mhs_akun: dict) -> dict:
    logger.info(f"Scrape Daftar Nilai untuk {mhs_akun['name']}...")
    try:
        await page.goto(f"{MHS_URL}akademik/daftarNilai", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
    except Exception as e:
        logger.warning(f"daftarNilai fallback: {e}")
        return {"matkul": [], "ip_semester": None, "ipk": None, "total_sks": 0}

    body = (await page.inner_text("body")).strip()
    lines = [line.strip() for line in body.split("\n") if line.strip()]

    result: dict = {"matkul": [], "ip_semester": None, "ipk": None, "total_sks": 0}

    for idx, line in enumerate(lines):
        m = re.search(r"IP\s*Kumulatif", line, re.I)
        if m:
            ip_match = re.search(r"([0-9]+\.[0-9]+)", line)
            if ip_match:
                try:
                    result["ipk"] = float(ip_match.group(1))
                except ValueError:
                    pass
            elif idx + 1 < len(lines):
                try:
                    result["ipk"] = float(lines[idx + 1])
                except ValueError:
                    pass
            break

    i = 0
    while i < len(lines):
        line = lines[i]
        if i + 3 < len(lines):
            sks_match = re.match(r"^(\d+)\s+SKS$", lines[i + 1], re.I)
            kdmk_match = re.match(r"^KDMK:\s*(\S+)", lines[i + 2], re.I)
            grade_line = lines[i + 3].strip().upper()
            is_grade = any(h in grade_line for h in ["A", "B", "C", "D", "E"])

            if sks_match and kdmk_match and is_grade and len(grade_line) <= 4:
                matkul = line
                sks = int(sks_match.group(1))
                kdmk = kdmk_match.group(1)
                huruf = grade_line
                bobot = BOBOT.get(huruf, 0.0)
                result["matkul"].append({
                    "kdmk": kdmk, "matkul": matkul, "sks": sks,
                    "huruf": huruf, "bobot": bobot,
                })
                result["total_sks"] += sks
                i += 4
                continue
        i += 1

    if result["matkul"]:
        total_bobot = sum(m["sks"] * m["bobot"] for m in result["matkul"])
        if result["total_sks"] > 0:
            result["ip_semester"] = round(total_bobot / result["total_sks"], 2)

    logger.info(f"DaftarNilai: {len(result['matkul'])} matkul, IPK={result['ipk']}")
    return result


async def scrape_khs_komponen(page, mhs_akun: dict) -> dict:
    logger.info(f"Scrape KHS komponen untuk {mhs_akun['name']}...")
    try:
        await page.goto(f"{MHS_URL}akademik/khs", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
    except Exception:
        return {"matkul": [], "ips": None, "max_sks": None}

    body = (await page.inner_text("body")).strip()
    lines = [line.strip() for line in body.split("\n") if line.strip()]

    result: dict = {"matkul": [], "ips": None, "max_sks": None}
    i = 0
    while i < len(lines):
        line = lines[i]
        if (line.isupper() and len(line) > 4
            and line not in ("SiAdin", "UNDUH KHS", "DASHBOARD", "AKADEMIK",
                            "BIODATA", "KEUANGAN", "DOKUMEN", "TUGAS AKHIR",
                            "LAINNYA", "KELUAR", "KRS", "KHS", "JADWAL UJIAN",
                            "PRESENSI ONLINE", "DAFTAR NILAI", "MATRIKULASI",
                            "SEMESTER ANTARA", "GRAFIK IP SEMESTER ANTARA")):
            if i + 1 < len(lines) and re.match(r"^\d+\s+SKS$", lines[i+1], re.I):
                matkul = line
                sks_match = re.match(r"^(\d+)", lines[i+1])
                sks = int(sks_match.group(1)) if sks_match else 0

                uts = None
                uas = None
                n_akhir = None
                j = i + 2
                while j < len(lines) and j < i + 15:
                    lj = lines[j]
                    if lj == "N.UTS":
                        if j + 1 < len(lines) and lines[j+1].replace(".", "").replace("-", "").isdigit():
                            uts_str = lines[j+1]
                            uts = float(uts_str) if uts_str.replace(".", "").replace("-", "").isdigit() else None
                    elif lj == "N.UAS":
                        if j + 1 < len(lines):
                            uas_str = lines[j+1]
                            uas = float(uas_str) if uas_str.replace(".", "").replace("-", "").isdigit() else None
                    elif lj == "N.Akhir":
                        if j + 1 < len(lines):
                            n_akhir_raw = lines[j+1]
                            parts = re.split(r"\s*\|\s*", n_akhir_raw)
                            try:
                                n_akhir = float(parts[0]) if parts[0].replace(".","").replace("-","").isdigit() else None
                            except ValueError:
                                n_akhir = None
                    elif lj.startswith("IPS:"):
                        try:
                            result["ips"] = float(lj.split(":")[1].strip())
                        except (ValueError, IndexError):
                            pass
                    elif lj.startswith("Max. SKS:"):
                        try:
                            result["max_sks"] = int(lj.split(":")[1].strip())
                        except (ValueError, IndexError):
                            pass
                    j += 1

                entry = {"matkul": matkul, "sks": sks}
                if uts is not None:
                    entry["uts"] = uts
                if uas is not None:
                    entry["uas"] = uas
                if n_akhir is not None:
                    entry["n_akhir"] = n_akhir
                result["matkul"].append(entry)
                i = j
                continue
        i += 1

    logger.info(f"KHS komponen: {len(result['matkul'])} matkul, IPS={result['ips']}")
    return result
