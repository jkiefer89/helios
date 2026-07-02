"""Unit tests for the shared engine helpers in engine/_common.py and engine/pdf_layout.py."""
import pandas as pd
import pytest

from engine import _common, pdf_layout


def test_dedupe_preserves_order_and_drops_empty_entries():
    assert _common.dedupe(["a", "", "b", "a", None, "c", "b"]) == ["a", "b", "c"]
    assert _common.dedupe([]) == []


def test_finite_parses_numbers_and_rejects_garbage():
    assert _common.finite(2) == 2.0
    assert _common.finite("3.5") == 3.5
    assert _common.finite(None) is None
    assert _common.finite("n/a") is None
    assert _common.finite(float("nan")) is None
    assert _common.finite(float("inf")) is None


def test_avg_and_pct_round_and_handle_empty_inputs():
    assert _common.avg([1.0, 2.0, None, "bad"]) == 1.5
    assert _common.avg([1, 2, 4]) == round(7 / 3, 4)
    assert _common.avg([]) is None
    assert _common.avg([None]) is None
    assert _common.pct(1, 3) == 33.33
    assert _common.pct(0, 0) is None
    assert _common.pct(2, -1) is None


def test_clean_close_normalizes_index_and_values():
    raw = pd.Series(
        ["101", None, "100"],
        index=pd.to_datetime([
            "2024-01-03 15:30:00+00:00",
            "2024-01-02 00:00:00+00:00",
            "2024-01-01 10:00:00+00:00",
        ]),
    )
    clean = _common.clean_close(raw)

    assert clean.tolist() == [100.0, 101.0]
    assert list(clean.index) == [pd.Timestamp("2024-01-01"), pd.Timestamp("2024-01-03")]
    assert clean.index.tz is None
    assert _common.clean_close(None).empty


def test_paper_hit_direction_rules():
    assert _common.paper_hit("BUY", 2.0, None) is True
    assert _common.paper_hit("BUY", 2.0, -0.5) is False  # alpha overrides forward
    assert _common.paper_hit("SELL", -1.0, None) is True
    assert _common.paper_hit("REDUCE", 1.0, None) is False
    assert _common.paper_hit("HOLD", -0.5, None) is True
    assert _common.paper_hit("HOLD", -2.0, None) is False
    assert _common.paper_hit("BUY", None, None) is None


def test_journal_paper_hit_requires_measured_entries():
    measured = {"forward_status": "measured", "action_label": "BUY", "forward_result_pct": 1.2, "alpha_pct": None}
    pending = {**measured, "forward_status": "pending"}

    assert _common.journal_paper_hit(measured) is True
    assert _common.journal_paper_hit(pending) is None
    assert _common.journal_paper_hit({"forward_status": "measured", "action_label": "BUY"}) is None


def test_pdf_layout_formatting_helpers():
    assert pdf_layout.clean_text("a—b–c‑d") == "a-b-c-d"
    assert pdf_layout.clean_text(None) == ""
    assert pdf_layout.fmt_value(12.34, suffix="%") == "12.3%"
    assert pdf_layout.fmt_value(None) == "Research locked"
    assert pdf_layout.fmt_value("mixed") == "mixed"
    assert pdf_layout.fmt_optional_pct(1.25) == "1.2%"
    assert pdf_layout.fmt_optional_pct(None) == "Pending"


class _CanvasStub:
    """Records drawString calls; ignores styling calls."""

    def __init__(self):
        self.strings = []

    def setFillColor(self, *args, **kwargs):
        pass

    def setFont(self, *args, **kwargs):
        pass

    def drawString(self, x, y, text):
        self.strings.append((x, y, text))


class _ColorsStub:
    @staticmethod
    def HexColor(value):
        return value


def test_pdf_layout_draw_wrapped_caps_lines_and_returns_new_baseline():
    pdf = _CanvasStub()
    body = "word " * 200
    y = pdf_layout.draw_wrapped(pdf, body, 0, 100, 100, 10, "#fff", max_lines=3)

    assert len(pdf.strings) == 3
    assert y == pytest.approx(100 - 3 * (10 * 1.38))
    assert pdf.strings[0][1] == 100  # first line drawn at the starting baseline


def test_pdf_layout_header_and_footer_render_expected_strings():
    pdf = _CanvasStub()
    pdf.setStrokeColor = lambda *a, **k: None
    pdf.line = lambda *a, **k: None
    pdf_layout.header(pdf, 612, 792, "SECTION TITLE", _ColorsStub, subtitle="MODEL APPROVAL PACKET")
    pdf_layout.footer(pdf, 612, 2, 5, _ColorsStub, version_label="v3")

    texts = [text for _, _, text in pdf.strings]
    assert "HELIOS PRO" in texts
    assert "MODEL APPROVAL PACKET" in texts
    assert "SECTION TITLE" in texts
    assert "Page 2 of 5" in texts
    assert "v3" in texts
    assert pdf_layout.DEFAULT_FOOTER_NOTE in texts
