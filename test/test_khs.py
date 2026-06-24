"""Test KHS/Nilai scraper + formatter + diff detection."""

from scrapers import format_khs_message, scrape_khs


SAMPLE_KHS = {
    "matkul": [
        {"kdmk": "A22.63206", "matkul": "Basis Data", "sks": 3, "huruf": "A", "bobot": 4.0},
        {"kdmk": "A22.63207", "matkul": "Jaringan Komputer", "sks": 3, "huruf": "B+", "bobot": 3.5},
        {"kdmk": "A22.63208", "matkul": "Sistem Informasi", "sks": 2, "huruf": "A-", "bobot": 3.75},
    ],
    "ip_semester": 3.78,
    "ipk": 3.65,
    "total_sks": 8,
}


class TestFormatKhsMessage:
    def test_includes_name(self):
        msg = format_khs_message(SAMPLE_KHS, "Hafizh")
        assert "Hafizh" in msg
        assert "KHS" in msg

    def test_includes_courses(self):
        msg = format_khs_message(SAMPLE_KHS, "Hafizh")
        assert "Basis Data" in msg
        assert "Jaringan" in msg
        assert "Sistem Informasi" in msg

    def test_includes_ip(self):
        msg = format_khs_message(SAMPLE_KHS, "Hafizh")
        assert "3.78" in msg
        assert "3.65" in msg

    def test_includes_sks(self):
        msg = format_khs_message(SAMPLE_KHS, "Hafizh")
        assert "8" in msg

    def test_huruf_grades(self):
        msg = format_khs_message(SAMPLE_KHS, "Hafizh")
        assert "A" in msg
        assert "B+" in msg
        assert "A-" in msg

    def test_empty(self):
        msg = format_khs_message({"matkul": []}, "Hafizh")
        assert "Belum ada nilai" in msg

    def test_no_ip(self):
        data = {**SAMPLE_KHS, "ip_semester": None, "ipk": None}
        msg = format_khs_message(data, "Hafizh")
        assert "3.78" not in msg


class TestDiffNilai:
    def test_new_grade_detected(self):
        from storage import diff_nilai
        old = {"saya": {"A22.001": {"huruf": "B"}}}
        new = {"saya": {"A22.001": {"huruf": "A"}}}
        assert len(diff_nilai(old, new)) == 1

    def test_no_change(self):
        from storage import diff_nilai
        old = {"saya": {"A22.001": {"huruf": "A"}}}
        new = {"saya": {"A22.001": {"huruf": "A"}}}
        assert diff_nilai(old, new) == []

    def test_new_course(self):
        from storage import diff_nilai
        old = {"saya": {}}
        new = {"saya": {"A22.001": {"huruf": "A", "matkul": "BD"}}}
        diffs = diff_nilai(old, new)
        assert len(diffs) == 1
        assert diffs[0]["new"] == "A"
        assert diffs[0]["old"] == ""

    def test_multiple_changes(self):
        from storage import diff_nilai
        old = {"saya": {"A": {"huruf": "B"}, "B": {"huruf": "A"}}}
        new = {"saya": {"A": {"huruf": "A"}, "B": {"huruf": "A"}}}
        assert len(diff_nilai(old, new)) == 1  # Only A changed

    def test_per_akun(self):
        from storage import diff_nilai
        old = {"saya": {"X": {"huruf": "B"}}}
        new = {"saya": {"X": {"huruf": "B"}}, "pacar": {"Y": {"huruf": "A"}}}
        diffs = diff_nilai(old, new)
        assert len(diffs) == 1  # pacar:Y is new
        assert diffs[0]["akun"] == "pacar"
        assert diffs[0]["kdmk"] == "Y"


class TestScrapeKhs:
    def test_imports(self):
        """scrape_khs function compiles and imports."""
        import inspect
        assert inspect.iscoroutinefunction(scrape_khs)
