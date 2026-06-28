"""SQLite persistence for local real-data workflows.

The store intentionally persists parsed market/model facts only. Raw uploads,
sample/simulated series, API keys, secrets, and browser/runtime artifacts do not
belong here.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_VERSION = 1
REAL_SOURCES = {"live", "upload"}
DISABLED_VALUES = {"", "0", "false", "off", "disabled", "none"}
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / ".helios" / "helios.db"

_STORE: "SQLiteStore | None" = None
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|authorization)\s*[:=]\s*[^,\s;]+"
)
_OPENAI_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{12,}")


@dataclass
class PersistedInstrument:
    symbol: str
    name: str
    source: str
    df: pd.DataFrame
    metadata: dict[str, Any]


@dataclass
class PersistedModel:
    id: str
    name: str
    mandate_key: str
    mandate_context: str
    source_filename: str
    holdings: list[dict[str, Any]]
    metadata: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact_secrets(value: str | None) -> str:
    text = _CTRL_RE.sub("", (value or "").strip())[:800]
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}=[redacted]", text)
    text = _OPENAI_KEY_RE.sub("[redacted-key]", text)
    return text


def safe_filename(filename: str | None) -> str:
    if not filename:
        return ""
    return redact_secrets(Path(filename).name)[:120]


def get_store() -> "SQLiteStore":
    global _STORE
    if _STORE is None:
        _STORE = SQLiteStore.from_env()
    return _STORE


def reset_store_for_tests() -> None:
    global _STORE
    _STORE = None


class SQLiteStore:
    def __init__(self, path: Path | None, configured: bool, disabled: bool = False):
        self.path = path
        self.configured = configured
        self.disabled = disabled
        self.available = False
        self.warning = ""
        if disabled:
            self.warning = "SQLite persistence is disabled by HELIOS_DB_PATH."
            return
        self._ensure()

    @classmethod
    def from_env(cls) -> "SQLiteStore":
        raw = os.environ.get("HELIOS_DB_PATH")
        if raw is not None and raw.strip().lower() in DISABLED_VALUES:
            return cls(None, configured=False, disabled=True)
        path = Path(raw).expanduser() if raw else DEFAULT_DB_PATH
        return cls(path, configured=True, disabled=False)

    def _ensure(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                _create_schema(conn)
            self.available = True
            self.warning = ""
        except Exception as exc:
            self.available = False
            self.warning = f"SQLite persistence unavailable: {exc}"

    def _connect(self) -> sqlite3.Connection:
        if self.path is None:
            raise RuntimeError("SQLite persistence is disabled.")
        conn = sqlite3.connect(str(self.path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def status(self) -> dict[str, Any]:
        status = {
            "configured": self.configured and not self.disabled,
            "available": self.available,
            "path": str(self.path) if self.path else "",
            "warning": self.warning,
            "schema_version": SCHEMA_VERSION if self.available else None,
            "real_instrument_count": 0,
            "persisted_model_count": 0,
            "last_refresh": None,
        }
        if not self.available:
            return status
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM instruments WHERE source IN ('live', 'upload')"
                ).fetchone()
                status["real_instrument_count"] = int(row["n"] if row else 0)
                row = conn.execute("SELECT COUNT(*) AS n FROM models").fetchone()
                status["persisted_model_count"] = int(row["n"] if row else 0)
                last = conn.execute(
                    "SELECT symbol, attempted_at, status, rows_added, message, source "
                    "FROM refresh_log ORDER BY attempted_at DESC, id DESC LIMIT 1"
                ).fetchone()
                if last:
                    status["last_refresh"] = dict(last)
        except Exception as exc:
            self.available = False
            self.warning = f"SQLite persistence unavailable: {exc}"
            status["available"] = False
            status["warning"] = self.warning
        return status

    def persist_instrument(
        self,
        *,
        symbol: str,
        name: str,
        source: str,
        frame: pd.DataFrame,
        adjusted: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if source not in REAL_SOURCES:
            return {"persisted": False, "reason": f"source {source!r} is not persisted"}
        if not self.available:
            return {"persisted": False, "warning": self.warning}
        try:
            clean = _normalise_price_frame(frame)
            if clean.empty:
                raise ValueError("No valid price rows to persist.")
            now = utc_now()
            first_date = str(clean.index.min().date())
            last_date = str(clean.index.max().date())
            meta_json = _json_dumps(metadata or {})
            rows = []
            for idx, row in clean.iterrows():
                rows.append((
                    symbol,
                    str(idx.date()),
                    _optional_float(row.get("open")),
                    _optional_float(row.get("high")),
                    _optional_float(row.get("low")),
                    _optional_float(row.get("close")),
                    _optional_float(row.get("volume")),
                    source,
                    None if adjusted is None else int(bool(adjusted)),
                ))
            with self._connect() as conn:
                existing = conn.execute(
                    "SELECT created_at FROM instruments WHERE symbol = ?", (symbol,)
                ).fetchone()
                created_at = existing["created_at"] if existing else now
                conn.execute(
                    """
                    INSERT INTO instruments
                      (symbol, display_name, source, first_date, last_date, row_count,
                       created_at, updated_at, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                      display_name = excluded.display_name,
                      source = excluded.source,
                      first_date = excluded.first_date,
                      last_date = excluded.last_date,
                      row_count = excluded.row_count,
                      updated_at = excluded.updated_at,
                      metadata_json = excluded.metadata_json
                    """,
                    (symbol, name, source, first_date, last_date, len(clean), created_at, now, meta_json),
                )
                conn.executemany(
                    """
                    INSERT INTO price_history
                      (symbol, date, open, high, low, close, volume, source, adjusted)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, date) DO UPDATE SET
                      open = excluded.open,
                      high = excluded.high,
                      low = excluded.low,
                      close = excluded.close,
                      volume = excluded.volume,
                      source = excluded.source,
                      adjusted = excluded.adjusted
                    """,
                    rows,
                )
            return {"persisted": True, "rows": len(clean), "first_date": first_date, "last_date": last_date}
        except Exception as exc:
            self.warning = f"Could not persist {symbol}: {exc}"
            return {"persisted": False, "warning": self.warning}

    def load_instruments(self) -> list[PersistedInstrument]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                instruments = conn.execute(
                    "SELECT * FROM instruments WHERE source IN ('live', 'upload') ORDER BY symbol"
                ).fetchall()
                out: list[PersistedInstrument] = []
                for row in instruments:
                    prices = conn.execute(
                        """
                        SELECT date, open, high, low, close, volume
                        FROM price_history
                        WHERE symbol = ?
                        ORDER BY date
                        """,
                        (row["symbol"],),
                    ).fetchall()
                    if not prices:
                        continue
                    df = pd.DataFrame([dict(p) for p in prices])
                    df.index = pd.to_datetime(df.pop("date"), errors="coerce")
                    df = df[~df.index.isna()]
                    if "close" not in df or df["close"].dropna().empty:
                        continue
                    out.append(PersistedInstrument(
                        symbol=row["symbol"],
                        name=row["display_name"],
                        source=row["source"],
                        df=df,
                        metadata=_json_loads(row["metadata_json"]),
                    ))
                return out
        except Exception as exc:
            self.available = False
            self.warning = f"Could not load persisted instruments: {exc}"
            return []

    def persist_model(
        self,
        *,
        model_id: str,
        name: str,
        mandate_key: str,
        mandate_context: str,
        holdings: list[dict[str, Any]],
        source_filename: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            return {"persisted": False, "warning": self.warning}
        try:
            now = utc_now()
            clean_context = redact_secrets(mandate_context)[:400]
            safe_source = safe_filename(source_filename)
            meta_json = _json_dumps(metadata or {})
            with self._connect() as conn:
                existing = conn.execute(
                    "SELECT created_at FROM models WHERE model_id = ?", (model_id,)
                ).fetchone()
                created_at = existing["created_at"] if existing else now
                conn.execute(
                    """
                    INSERT INTO models
                      (model_id, name, mandate_key, mandate_context, source_filename,
                       created_at, updated_at, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(model_id) DO UPDATE SET
                      name = excluded.name,
                      mandate_key = excluded.mandate_key,
                      mandate_context = excluded.mandate_context,
                      source_filename = excluded.source_filename,
                      updated_at = excluded.updated_at,
                      metadata_json = excluded.metadata_json
                    """,
                    (model_id, name, mandate_key, clean_context, safe_source, created_at, now, meta_json),
                )
                conn.execute("DELETE FROM holdings WHERE model_id = ?", (model_id,))
                conn.executemany(
                    """
                    INSERT INTO holdings
                      (model_id, ticker, weight, source_state, metadata_json)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            model_id,
                            str(h["ticker"]),
                            float(h["weight"]),
                            str(h.get("source_state") or h.get("source") or "pending"),
                            _json_dumps(h.get("metadata") or {}),
                        )
                        for h in holdings
                    ],
                )
            return {"persisted": True, "holdings": len(holdings)}
        except Exception as exc:
            self.warning = f"Could not persist model {model_id}: {exc}"
            return {"persisted": False, "warning": self.warning}

    def load_models(self) -> list[PersistedModel]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                models = conn.execute("SELECT * FROM models ORDER BY name").fetchall()
                out: list[PersistedModel] = []
                for row in models:
                    holdings = conn.execute(
                        """
                        SELECT ticker, weight, source_state, metadata_json
                        FROM holdings
                        WHERE model_id = ?
                        ORDER BY weight DESC, ticker
                        """,
                        (row["model_id"],),
                    ).fetchall()
                    out.append(PersistedModel(
                        id=row["model_id"],
                        name=row["name"],
                        mandate_key=row["mandate_key"],
                        mandate_context=row["mandate_context"] or "",
                        source_filename=row["source_filename"] or "",
                        holdings=[
                            {
                                "ticker": h["ticker"],
                                "weight": float(h["weight"]),
                                "source_state": h["source_state"],
                                "metadata": _json_loads(h["metadata_json"]),
                            }
                            for h in holdings
                        ],
                        metadata=_json_loads(row["metadata_json"]),
                    ))
                return out
        except Exception as exc:
            self.available = False
            self.warning = f"Could not load persisted models: {exc}"
            return []

    def record_refresh(
        self,
        *,
        symbol: str,
        status: str,
        rows_added: int,
        message: str,
        source: str = "live",
    ) -> None:
        if not self.available:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO refresh_log
                      (symbol, attempted_at, status, rows_added, message, source)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (symbol, utc_now(), status, int(rows_added), redact_secrets(message)[:500], source),
                )
        except Exception as exc:
            self.warning = f"Could not record refresh log: {exc}"

    def refresh_log(self, limit: int = 10) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT symbol, attempted_at, status, rows_added, message, source
                    FROM refresh_log
                    ORDER BY attempted_at DESC, id DESC
                    LIMIT ?
                    """,
                    (int(limit),),
                ).fetchall()
                return [dict(row) for row in rows]
        except Exception:
            return []


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
          version INTEGER PRIMARY KEY,
          applied_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS instruments (
          symbol TEXT PRIMARY KEY,
          display_name TEXT NOT NULL,
          source TEXT NOT NULL,
          first_date TEXT,
          last_date TEXT,
          row_count INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_history (
          symbol TEXT NOT NULL,
          date TEXT NOT NULL,
          open REAL,
          high REAL,
          low REAL,
          close REAL NOT NULL,
          volume REAL,
          source TEXT NOT NULL,
          adjusted INTEGER,
          PRIMARY KEY (symbol, date),
          FOREIGN KEY(symbol) REFERENCES instruments(symbol) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS models (
          model_id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          mandate_key TEXT NOT NULL,
          mandate_context TEXT NOT NULL DEFAULT '',
          source_filename TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS holdings (
          model_id TEXT NOT NULL,
          ticker TEXT NOT NULL,
          weight REAL NOT NULL,
          source_state TEXT NOT NULL DEFAULT 'pending',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          PRIMARY KEY (model_id, ticker),
          FOREIGN KEY(model_id) REFERENCES models(model_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS refresh_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          symbol TEXT NOT NULL,
          attempted_at TEXT NOT NULL,
          status TEXT NOT NULL,
          rows_added INTEGER NOT NULL DEFAULT 0,
          message TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT 'live'
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, utc_now()),
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_price_history_symbol_date ON price_history(symbol, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_refresh_log_symbol_time ON refresh_log(symbol, attempted_at)")


def _normalise_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    out.index = pd.to_datetime(out.index, errors="coerce")
    out = out[~out.index.isna()]
    if getattr(out.index, "tz", None) is not None:
        out.index = out.index.tz_localize(None)
    out.index = out.index.normalize()
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]
    for col in ("open", "high", "low", "close", "volume"):
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    if "close" not in out:
        return pd.DataFrame()
    keep = [col for col in ("open", "high", "low", "close", "volume") if col in out]
    out = out[keep]
    return out[~out["close"].isna()]


def _optional_float(value: Any) -> float | None:
    try:
        if pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _json_dumps(value: dict[str, Any]) -> str:
    clean = _scrub_json(value)
    return json.dumps(clean, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        decoded = json.loads(value)
        return decoded if isinstance(decoded, dict) else {}
    except Exception:
        return {}


def _scrub_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {redact_secrets(str(k))[:80]: _scrub_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_json(item) for item in value[:100]]
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_secrets(str(value))
