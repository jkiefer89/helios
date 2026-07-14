"""Backfill coverage for audited-but-untested branches.

Covers: provenance gate branches (universe real/invalid, instrument blocked,
portfolio mixed), the XLSX model-upload path plus its zip-bomb guard, the SELL
signal action, and the 405/413/415 JSON error-handler shapes. All offline.
"""
import io
import zipfile
from io import BytesIO
from types import SimpleNamespace

import openpyxl
import pytest
from werkzeug.exceptions import UnsupportedMediaType

import app as helios
from engine import data, portfolio, provenance, signals
from tests.conftest import price_series


def _client():
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    return helios.app.test_client()


# --------------------------------------------------------------------------- #
# (a) provenance gate branches
# --------------------------------------------------------------------------- #
def test_provenance_universe_pure_real_mode():
    frame = {"close": price_series(days=90)}
    instruments = [
        SimpleNamespace(source="upload", df=frame, price_provider=""),
        SimpleNamespace(source="live", df=frame, price_provider=""),
    ]

    result = provenance.universe(instruments)

    assert result["data_mode"] == "real"
    assert result["display_label"] == "Real Research Mode"
    assert result["eligible_for_real_research"] is True
    assert result["required_action"] == ""
    assert result["warnings"] == []
    assert result["source_counts"] == {"upload": 1, "live": 1}


def test_provenance_universe_invalid_for_research_without_eligible_sources():
    empty_frame = {"close": price_series(days=0)}
    for instruments in (
        [],
        [
            SimpleNamespace(source="missing", df=empty_frame),
            SimpleNamespace(source="unknown", df=empty_frame),
        ],
    ):
        result = provenance.universe(instruments)

        assert result["data_mode"] == "invalid_for_research"
        assert result["display_label"] == "Data Quality Blocked"
        assert result["eligible_for_real_research"] is False
        assert result["required_action"] == provenance.DEMO_ACTION
        assert result["warnings"] == [result["reason"]]


def test_provenance_instrument_blocked_for_unknown_source():
    result = provenance.instrument("missing", history_days=0)

    assert result["data_mode"] == "invalid_for_research"
    assert result["display_label"] == "Data Quality Blocked"
    assert result["eligible_for_real_research"] is False
    assert result["required_action"] == provenance.DEMO_ACTION
    assert result["warnings"] == [result["reason"]]

    # A real source with too little history remains blocked until quality passes.
    short = provenance.instrument("upload", history_days=10, min_history=60)
    assert short["data_mode"] == "invalid_for_research"
    assert short["eligible_for_real_research"] is False
    assert "60 sessions" in short["reason"]


def test_provenance_portfolio_mixed_with_partially_real_weight():
    prov = {
        "source_weight_pct": {"upload": 60.0, "simulated": 25.0, "missing": 15.0},
        "missing_symbols": ["NOHIST9"],
    }

    result = provenance.portfolio(prov)

    assert result["data_mode"] == "mixed"
    assert result["display_label"] == "Mixed Data Warning"
    assert result["eligible_for_real_research"] is False
    assert result["real_weight_pct"] == 60.0
    assert result["non_real_weight_pct"] == 40.0
    assert result["required_action"] == provenance.DEMO_ACTION
    assert result["missing_tickers"] == ["NOHIST9"]
    assert any("lacks retained market prices" in w for w in result["warnings"])
    assert any("NOHIST9" in w for w in result["warnings"])


# --------------------------------------------------------------------------- #
# (b) XLSX model upload path + zip-bomb guard
# --------------------------------------------------------------------------- #
def _xlsx_bytes(rows, header=("Ticker", "Weight (%)")) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(header))
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_model_upload_parses_holdings(monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    raw = _xlsx_bytes([("AAA", 60), ("BBB", 25), ("CCC", 15)])
    client = _client()

    resp = client.post(
        "/api/model/upload",
        data={"file": (BytesIO(raw), "book.xlsx"), "name": "Xlsx Model", "mandate": "balanced"},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["n_holdings"] == 3
    model = portfolio.get(body["id"])
    assert model is not None
    weights = {h.ticker: h.weight for h in model.holdings}
    assert weights == {"AAA": 0.6, "BBB": 0.25, "CCC": 0.15}
    assert model.holdings[0].ticker == "AAA"  # sorted by weight desc


def test_xlsx_zip_bomb_rejected(monkeypatch):
    monkeypatch.setattr(data, "HAS_YF", False)
    # ~8 MB of zeros deflates to a few KB: far past the 200x ratio guard.
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/worksheets/sheet1.xml", b"\x00" * (8 * 1024 * 1024))
    raw = bomb.getvalue()

    with pytest.raises(ValueError, match="unexpectedly large"):
        portfolio.parse_model_file(raw, "bomb.xlsx", "Bomb Model", "balanced", "")

    client = _client()
    resp = client.post(
        "/api/model/upload",
        data={"file": (BytesIO(raw), "bomb.xlsx"), "name": "Bomb Model", "mandate": "balanced"},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400
    assert "unexpectedly large" in resp.get_json()["error"]
    assert portfolio.get("BOMB-MODEL") is None


def test_xlsx_named_non_zip_bytes_rejected():
    with pytest.raises(ValueError, match="not a readable Excel/zip workbook"):
        portfolio.parse_model_file(b"this is not a zip archive", "fake.xlsx", "Fake", "balanced", "")


# --------------------------------------------------------------------------- #
# (c) SELL action branch
# --------------------------------------------------------------------------- #
def test_signals_evaluate_emits_sell_on_bearish_evidence():
    close = price_series(days=300, daily=-0.002)
    forecast_result = {
        "expected_return_pct": -4.0,
        "horizon_days": 21,
        "prob_up": 0.25,
        "quality": {"directional_accuracy": 0.70, "n_test": 100},
    }
    sentiment_result = {"aggregate_score": -0.6, "aggregate_label": "negative", "count": 4}

    sig = signals.evaluate(close, forecast_result, sentiment_result)

    assert sig["action"] == "SELL"
    assert sig["score"] < -0.25
    assert sig["conviction_pct"] > 25
    assert "Cautious" in sig["headline_rationale"]
    trend = next(c for c in sig["components"] if c["name"] == "trend")
    assert trend["raw"] < 0


# --------------------------------------------------------------------------- #
# (d) 405/413/415 JSON error-handler shapes
# --------------------------------------------------------------------------- #
def test_method_not_allowed_returns_json_error():
    client = _client()

    resp = client.post("/api/analyze")  # GET-only route

    assert resp.status_code == 405
    assert resp.content_type.startswith("application/json")
    body = resp.get_json()
    assert set(body) == {"error"}
    assert body["error"]


def test_oversized_body_returns_json_413():
    client = _client()
    big = BytesIO(b"x" * (17 * 1024 * 1024))  # over the 16 MB MAX_CONTENT_LENGTH cap

    resp = client.post(
        "/api/upload",
        data={"file": (big, "big.csv")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 413
    assert resp.content_type.startswith("application/json")
    assert resp.get_json() == {"error": "Uploaded file is too large."}


def test_unsupported_media_type_handler_returns_json_415():
    # No route in the app parses non-silent JSON, so drive Flask's registered
    # 415 handler through the app's own error dispatch to pin the JSON shape.
    with helios.app.test_request_context("/api/upload", method="POST"):
        rv = helios.app.handle_http_exception(UnsupportedMediaType())

    resp, code = rv
    assert code == 415
    assert resp.mimetype == "application/json"
    assert resp.get_json() == {"error": "Unsupported media type."}
