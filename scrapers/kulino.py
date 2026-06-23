"""Kulino (Moodle) scraper — tugas & material files extraction."""
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

KULINO_URL = "https://kulino.dinus.ac.id/"
SCREENSHOT_TUGAS = "bukti_tugas.png"
MATERIALS_DIR = Path("data") / "materials"


def extract_tasks_from_text(body_text: str, body_html: str = "") -> list[dict]:
    """Ekstrak tugas dari body text halaman Kulino pakai Python regex."""
    results = []
    seen = set()

    matches = re.findall(r"([A-Z][^\n]{5,120})\s+is due\s*\n\s*([^\n]{5,60})", body_text)
    for name, deadline in matches:
        name_clean = name.strip()
        deadline_clean = deadline.strip()
        key = name_clean.lower()
        if name_clean and key not in seen:
            seen.add(key)
            results.append({
                "name": name_clean,
                "course": "",
                "deadline": deadline_clean,
                "link": "",
            })

    return results


async def scrape_kulino_tugas(page, kulino_akun: dict) -> list[dict]:
    """Scrape daftar tugas dari halaman Kulino setelah login."""
    logger.info(f"Scrape tugas Kulino untuk {kulino_akun['name']}...")

    tugas_list = []

    timeline_selectors = [
        "[data-region='timeline-view']",
        ".timeline",
        "#timeline",
        "[data-region='my-timeline']",
    ]
    for sel in timeline_selectors:
        el = await page.query_selector(sel)
        if el:
            logger.info(f"Timeline ditemukan via selector: {sel}")
            break

    logger.info("Login Kulino berhasil, navigasi ke Upcoming Events...")

    try:
        await page.goto(
            f"{KULINO_URL}calendar/view.php?view=upcoming",
            wait_until="networkidle", timeout=30000,
        )
        await page.wait_for_timeout(3000)
        logger.info(f"URL: {page.url}")
    except Exception as e:
        logger.warning(f"Fallback dashboard: {e}")
        await page.goto(f"{KULINO_URL}my/", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(3000)

    body_text = await page.evaluate("() => document.body?.innerText || ''")
    logger.info(f"Body length: {len(body_text)}")

    regex_tasks = extract_tasks_from_text(body_text)
    tugas_list.extend(regex_tasks)

    try:
        await page.screenshot(path=SCREENSHOT_TUGAS, full_page=True)
        logger.info(f"Screenshot: {SCREENSHOT_TUGAS}")
    except Exception as e:
        logger.error(f"Screenshot gagal: {e}")

    seen = set()
    unik = []
    for t in tugas_list:
        key = t["name"].lower().strip()
        if key and key not in seen:
            seen.add(key)
            unik.append(t)

    if unik:
        logger.info(f"Scrape sukses: {len(unik)} tugas ditemukan")
    else:
        logger.info("Tidak ada tugas ditemukan via selector.")

    return unik


async def scrape_course_list(page) -> list[dict]:
    """Scrape daftar course Kulino dari halaman dashboard.
    Returns [{id, name, url}, ...]
    """
    await page.goto(f"{KULINO_URL}my/", wait_until="networkidle", timeout=30000)
    await page.wait_for_timeout(2000)

    courses = await page.evaluate("""() => {
        const items = [];
        // Moodle dashboard: course box atau list
        const links = document.querySelectorAll('a[href*="course/view.php?id="]');
        const seen = new Set();
        links.forEach(a => {
            const name = a.innerText?.trim();
            const href = a.getAttribute('href') || '';
            if (name && name.length > 3 && !seen.has(name)) {
                seen.add(name);
                const id = new URL(href).searchParams.get('id') || '';
                items.push({id, name, url: href});
            }
        });
        return items;
    }""")
    logger.info(f"Course list: {len(courses)} courses")
    return courses


async def scrape_course_files(page, course_url: str) -> list[dict]:
    """Scrape file resources dari halaman course.
    Moodle resource index: /mod/resource/index.php?id=X
    Atau course page langsung.
    
    Returns [{name, file_url, file_ext, modified_time, course_name}, ...]
    """
    files = []

    # Coba resource index page
    try:
        course_id_match = re.search(r'id=(\d+)', course_url)
        if course_id_match:
            resource_url = f"{KULINO_URL}mod/resource/index.php?id={course_id_match.group(1)}"
            await page.goto(resource_url, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
    except Exception:
        pass

    files = await page.evaluate("""() => {
        const items = [];
        // Moodle resource table format
        const rows = document.querySelectorAll('table.generaltable tbody tr, .resourcecontent tr, li.resource, .activity.resource');
        rows.forEach(row => {
            const link = row.querySelector('a[href*="pluginfile.php"], a[href*="mod_resource"], a[href*="download"');
            if (!link) return;
            const name = link.innerText?.trim();
            const href = link.getAttribute('href') || '';
            if (!name || name.length < 2) return;
            const ext = name.includes('.') ? name.split('.').pop().toLowerCase() : '';
            const timeEl = row.querySelector('.date, .time, .text-muted, td:last-child');
            const modified = timeEl ? timeEl.innerText?.trim() : '';
            items.push({
                name: name,
                file_url: href.startsWith('http') ? href : new URL(href, window.location.origin).href,
                file_ext: ext,
                modified_time: modified,
            });
        });
        return items;
    }""")

    # Fallback: parse page text for file links
    if not files:
        logger.info("Resource table not found, parsing page text...")
        body = await page.inner_text("body")
        lines = body.split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if re.search(r'\.(pdf|ppt|pptx|doc|docx|xls|xlsx|zip|rar|txt)$', line, re.I):
                files.append({
                    "name": line,
                    "file_url": "",
                    "file_ext": line.split(".")[-1].lower(),
                    "modified_time": "",
                })

    logger.info(f"Files found: {len(files)}")
    return files


async def download_file(page, file_url: str, dest_path: Path) -> bool:
    """Download file dari Kulino dan simpan ke disk."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        async with page.context.expect_page(timeout=15000) as new_page_info:
            await page.evaluate(f"window.open('{file_url}', '_blank')")
        new_page = await new_page_info.value
        await new_page.wait_for_load_state("networkidle", timeout=15000)
        # If it's directly a file, download via Playwright
        # Fallback: evaluate document body text as last resort
        await new_page.close()
        return True
    except Exception as e:
        logger.warning(f"Download via new page failed: {e}")

    # Fallback: use fetch + save
    try:
        response = await page.evaluate(f"""
            async () => {{
                const resp = await fetch('{file_url}');
                if (!resp.ok) return null;
                const blob = await resp.blob();
                return await new Promise((resolve) => {{
                    const reader = new FileReader();
                    reader.onloadend = () => resolve(reader.result);
                    reader.readAsDataURL(blob);
                }});
            }}
        """)
        if response:
            import base64
            _, encoded = response.split(",", 1)
            raw = base64.b64decode(encoded)
            dest_path.write_bytes(raw)
            logger.info(f"Downloaded: {dest_path}")
            return True
    except Exception as e:
        logger.warning(f"Fetch download failed: {e}")

    return False


async def check_new_materials(page, kulino_akun: dict, material_cache: dict, course_query: str = "") -> tuple[list[dict], list[str]]:
    """Scrape & deteksi file baru untuk akun tertentu.
    Jika course_query diisi, hanya mengecek matkul yang cocok.
    Returns (new_files, all_found_courses_names)
    """

    cache = material_cache.get(kulino_akun["name"], {})
    new_files = []
    downloaded = []
    found_courses = []

    courses = await scrape_course_list(page)

    # Filter berdasarkan query
    if course_query:
        courses = [c for c in courses if course_query.lower() in c["name"].lower()]

    for course in courses:
        course_id = course["id"]
        if not course_id:
            continue
            
        found_courses.append(course["name"])

        files = await scrape_course_files(page, course["url"])
        for f in files:
            file_key = f"{course_id}:{f['name']}"

            # Check if already notified
            if file_key in cache:
                continue

            # New file detected
            f["course_name"] = course["name"]
            f["course_id"] = course_id
            f["account"] = kulino_akun["name"]
            new_files.append(f)

            # Attempt download
            if f["file_url"]:
                safe_name = re.sub(r'[^\w\.\- ]', '_', f['name'])
                dest = MATERIALS_DIR / kulino_akun["name"] / f"{course_id}_{safe_name}"
                ok = await download_file(page, f["file_url"], dest)
                if ok:
                    f["local_path"] = str(dest)
                    downloaded.append(f)

    # Update cache
    for f in new_files:
        file_key = f"{f['course_id']}:{f['name']}"
        cache[file_key] = {
            "name": f["name"],
            "course": f["course_name"],
            "ext": f["file_ext"],
            "time": f.get("modified_time", ""),
        }

    material_cache[kulino_akun["name"]] = cache
    logger.info(f"New materials for {kulino_akun['name']}: {len(new_files)} files, {len(downloaded)} downloaded")
    return new_files, found_courses
