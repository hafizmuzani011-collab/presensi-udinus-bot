"""Test render.py — jadwal HTML/PNG generation."""

from render import _build_html, esc


SAMPLE_SCHEDULES = {
    "saya": {
        "senin": [["07:00-08:40", "Basis Data", "D.2.J"]],
        "selasa": [],
    },
    "pacar": {
        "senin": [["10:00-11:40", "Matematika", "D.2.K"]],
        "selasa": [],
    },
}


class TestEsc:
    def test_ampersand(self):
        assert esc("a & b") == "a &amp; b"

    def test_html_tags(self):
        assert esc("<script>") == "&lt;script&gt;"

    def test_quotes(self):
        assert esc('"hello"') == "&quot;hello&quot;"

    def test_unicode_preserved(self):
        assert esc("Basis Data 📊") == "Basis Data 📊"

    def test_non_string(self):
        assert esc(str(123)) == "123"


class TestBuildHtml:
    def test_contains_hari_title(self):
        html = _build_html(SAMPLE_SCHEDULES, "senin", "22-06-2026")
        assert "Senin" in html
        assert "22-06-2026" in html

    def test_contains_all_classes(self):
        html = _build_html(SAMPLE_SCHEDULES, "senin", "22-06-2026")
        assert "Basis Data" in html
        assert "Matematika" in html
        assert "D.2.J" in html
        assert "D.2.K" in html

    def test_count_badge(self):
        html = _build_html(SAMPLE_SCHEDULES, "senin", "22-06-2026")
        assert "2 kelas hari ini" in html

    def test_empty_day(self):
        html = _build_html(SAMPLE_SCHEDULES, "selasa", "23-06-2026")
        assert "Tidak ada kelas" in html
        assert "0 kelas hari ini" in html

    def test_html_escaping_in_data(self):
        schedules = {
            "saya": {"senin": [["07:00-08:40", "<script>alert(1)</script>", "A&B"]]},
            "pacar": {"senin": []},
        }
        html = _build_html(schedules, "senin", "22-06-2026")
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html
        assert "A&amp;B" in html

    def test_valid_html_structure(self):
        html = _build_html(SAMPLE_SCHEDULES, "senin", "22-06-2026")
        assert html.startswith("<!DOCTYPE html>")
        assert html.endswith("</html>")
        assert "<style>" in html
        assert 'class="card"' in html
        assert 'class="row"' in html
