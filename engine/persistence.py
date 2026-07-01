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
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:  # pragma: no cover - exercised when dependency install is broken.
    Fernet = None
    InvalidToken = Exception

SCHEMA_VERSION = 4
REAL_SOURCES = {"live", "upload"}
DISABLED_VALUES = {"", "0", "false", "off", "disabled", "none"}
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / ".helios" / "helios.db"
ENCRYPTED_PREFIX = "enc:v1:"
ENCRYPTED_DB_MAGIC = b"HELIOSDB1\n"
SQLITE_HEADER = b"SQLite format 3\x00"

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


def redact_long_text(value: str | None, limit: int = 250_000) -> str:
    text = _CTRL_RE.sub("", (value or "").strip())[:limit]
    text = _SECRET_RE.sub(lambda m: f"{m.group(1)}=[redacted]", text)
    text = _OPENAI_KEY_RE.sub("[redacted-key]", text)
    return text


def safe_filename(filename: str | None) -> str:
    if not filename:
        return ""
    return redact_secrets(Path(filename).name)[:120]


class _FieldCipher:
    """Encrypt/decrypt the local SQLite snapshot and legacy encrypted fields."""

    def __init__(self, key: str, *, key_source: str, required: bool, mode: str):
        if Fernet is None:
            raise RuntimeError("database encryption requires the cryptography package")
        self._fernet = Fernet(key.encode("utf-8"))
        self.status = {
            "enabled": True,
            "required": bool(required),
            "mode": mode,
            "key_source": key_source,
            "algorithm": "fernet",
            "at_rest_format": "fernet-sqlite-snapshot",
            "plaintext_lookup_keys": [],
        }

    def encrypt(self, value: Any) -> Any:
        if value is None:
            return None
        text = str(value)
        if text.startswith(ENCRYPTED_PREFIX):
            return text
        token = self._fernet.encrypt(text.encode("utf-8")).decode("ascii")
        return ENCRYPTED_PREFIX + token

    def decrypt(self, value: Any) -> Any:
        if not isinstance(value, str) or not value.startswith(ENCRYPTED_PREFIX):
            return value
        token = value[len(ENCRYPTED_PREFIX):].encode("ascii")
        try:
            return self._fernet.decrypt(token).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError("encrypted database value could not be decrypted with the configured key") from exc

    def encrypt_bytes(self, value: bytes) -> bytes:
        return self._fernet.encrypt(value)

    def decrypt_bytes(self, value: bytes) -> bytes:
        try:
            return self._fernet.decrypt(value)
        except InvalidToken as exc:
            raise RuntimeError("encrypted SQLite persistence file could not be decrypted with the configured key") from exc


def _disabled_encryption_status(mode: str = "off", required: bool = False, warning: str = "") -> dict[str, Any]:
    return {
        "enabled": False,
        "required": bool(required),
        "mode": mode,
        "key_source": "",
        "algorithm": "",
        "at_rest_format": "sqlite",
        "plaintext_lookup_keys": [],
        "warning": warning,
    }


class _EncryptedConnectionLease:
    def __init__(self, store: "SQLiteStore"):
        self.store = store

    def __enter__(self) -> sqlite3.Connection:
        self.store._conn_lock.acquire()
        if self.store._memory_conn is None:
            self.store._conn_lock.release()
            raise RuntimeError("encrypted SQLite persistence is not initialized")
        return self.store._memory_conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if self.store._memory_conn is not None:
                if exc_type is None:
                    self.store._memory_conn.commit()
                    self.store._persist_encrypted_snapshot()
                else:
                    self.store._memory_conn.rollback()
        finally:
            self.store._conn_lock.release()
        return False


def _normalise_encryption_mode() -> tuple[str, bool, bool]:
    raw = (os.environ.get("HELIOS_DB_ENCRYPTION") or "auto").strip().lower()
    if raw in DISABLED_VALUES or raw in {"plain", "plaintext", "disabled"}:
        return "off", False, False
    if raw in {"required", "require", "fail-closed", "fail_closed"}:
        return "required", True, True
    return "auto", False, True


def _configured_cipher(path: Path | None) -> tuple[_FieldCipher | None, dict[str, Any], str]:
    mode, required, enabled = _normalise_encryption_mode()
    if not enabled:
        return None, _disabled_encryption_status(mode=mode, required=required), ""
    if Fernet is None:
        warning = "SQLite persistence encryption requires the cryptography package."
        return None, _disabled_encryption_status(mode=mode, required=required, warning=warning), warning

    raw_key = (os.environ.get("HELIOS_DB_ENCRYPTION_KEY") or "").strip()
    if raw_key:
        try:
            cipher = _FieldCipher(raw_key, key_source="environment", required=required, mode=mode)
            return cipher, cipher.status, ""
        except Exception as exc:
            warning = f"SQLite persistence encryption key is invalid: {exc}"
            return None, _disabled_encryption_status(mode=mode, required=required, warning=warning), warning

    key_path = Path(os.environ.get("HELIOS_DB_ENCRYPTION_KEY_PATH") or "").expanduser() if os.environ.get("HELIOS_DB_ENCRYPTION_KEY_PATH") else None
    if key_path is None and path is not None:
        key_path = path.with_suffix(".key")
    if key_path is None:
        warning = "SQLite persistence encryption is enabled but no key path is available."
        return None, _disabled_encryption_status(mode=mode, required=required, warning=warning), warning

    try:
        if key_path.is_file():
            key = key_path.read_text(encoding="utf-8").strip()
            key_source = "local_key_file"
        elif required:
            warning = "SQLite persistence encryption is required but no key is configured."
            return None, _disabled_encryption_status(mode=mode, required=True, warning=warning), warning
        else:
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key().decode("ascii")
            key_path.write_text(key + "\n", encoding="utf-8")
            try:
                key_path.chmod(0o600)
            except Exception:
                pass
            key_source = "generated_local_key_file"
        cipher = _FieldCipher(key, key_source=key_source, required=required, mode=mode)
        return cipher, cipher.status, ""
    except Exception as exc:
        warning = f"SQLite persistence encryption unavailable: {exc}"
        return None, _disabled_encryption_status(mode=mode, required=required, warning=warning), warning


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
        self._cipher, self.encryption, encryption_warning = _configured_cipher(path)
        self._conn_lock = threading.RLock()
        self._memory_conn: sqlite3.Connection | None = None
        if disabled:
            self.warning = "SQLite persistence is disabled by HELIOS_DB_PATH."
            return
        if encryption_warning and self.encryption.get("required"):
            self.warning = encryption_warning
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
            if self._cipher:
                self._ensure_encrypted_snapshot()
            else:
                if self.path.is_file() and self.path.read_bytes()[:len(ENCRYPTED_DB_MAGIC)] == ENCRYPTED_DB_MAGIC:
                    raise RuntimeError("encrypted SQLite persistence requires encryption to be enabled with the configured key")
                with self._connect() as conn:
                    _create_schema(conn)
            self.available = True
            self.warning = ""
        except Exception as exc:
            self.available = False
            self.warning = f"SQLite persistence unavailable: {exc}"

    def _ensure_encrypted_snapshot(self) -> None:
        if self.path is None or self._cipher is None:
            raise RuntimeError("encrypted SQLite persistence is not configured")
        conn = sqlite3.connect(":memory:", timeout=5, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        raw = self.path.read_bytes() if self.path.is_file() else b""
        if raw:
            if raw.startswith(ENCRYPTED_DB_MAGIC):
                conn.deserialize(self._cipher.decrypt_bytes(raw[len(ENCRYPTED_DB_MAGIC):]))
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA foreign_keys = ON")
            elif raw.startswith(SQLITE_HEADER):
                source = sqlite3.connect(str(self.path), timeout=5)
                try:
                    source.backup(conn)
                finally:
                    source.close()
            else:
                raise RuntimeError("encrypted SQLite persistence file has an unknown format")
        self._memory_conn = conn
        with self._connect() as active:
            _create_schema(active)
            self._normalise_existing_rows(active)

    def _persist_encrypted_snapshot(self) -> None:
        if self.path is None or self._cipher is None or self._memory_conn is None:
            return
        data = self._memory_conn.serialize()
        tmp = self.path.with_name(f"{self.path.name}.tmp")
        tmp.write_bytes(ENCRYPTED_DB_MAGIC + self._cipher.encrypt_bytes(data))
        try:
            tmp.chmod(0o600)
        except Exception:
            pass
        tmp.replace(self.path)
        self._remove_plaintext_sidecars()

    def _remove_plaintext_sidecars(self) -> None:
        if self.path is None:
            return
        for suffix in ("-wal", "-shm"):
            try:
                self.path.with_name(f"{self.path.name}{suffix}").unlink()
            except FileNotFoundError:
                pass

    def _connect(self):
        if self.path is None:
            raise RuntimeError("SQLite persistence is disabled.")
        if self._memory_conn is not None:
            return _EncryptedConnectionLease(self)
        conn = sqlite3.connect(str(self.path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _store_text(self, value: Any) -> Any:
        if self._cipher and isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX):
            value = self._cipher.decrypt(value)
        text = redact_secrets(str(value or ""))
        return text

    def _store_long_text(self, value: Any, limit: int = 250_000) -> Any:
        if self._cipher and isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX):
            value = self._cipher.decrypt(value)
        return redact_long_text(str(value or ""), limit=limit)

    def _load_text(self, value: Any) -> str:
        if self._cipher is None and isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX):
            raise RuntimeError("encrypted SQLite persistence requires the configured encryption key")
        decoded = self._cipher.decrypt(value) if self._cipher else value
        return str(decoded or "")

    def _store_number(self, value: Any) -> Any:
        if self._cipher and isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX):
            value = self._cipher.decrypt(value)
        number = _optional_float(value)
        return None if number is None else number

    def _load_number(self, value: Any) -> float | None:
        if self._cipher is None and isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX):
            raise RuntimeError("encrypted SQLite persistence requires the configured encryption key")
        decoded = self._cipher.decrypt(value) if self._cipher else value
        return _optional_float(decoded)

    def _store_json(self, value: dict[str, Any]) -> Any:
        if self._cipher and isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX):
            value = self._cipher.decrypt(value)
            return _json_dumps(_json_loads(str(value or "")))
        encoded = _json_dumps(value)
        return encoded

    def _load_json(self, value: Any) -> dict[str, Any]:
        if self._cipher is None and isinstance(value, str) and value.startswith(ENCRYPTED_PREFIX):
            raise RuntimeError("encrypted SQLite persistence requires the configured encryption key")
        decoded = self._cipher.decrypt(value) if self._cipher else value
        return _json_loads(str(decoded or ""))

    def _refresh_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "symbol": row["symbol"],
            "attempted_at": row["attempted_at"],
            "status": row["status"],
            "rows_added": int(row["rows_added"]),
            "message": self._load_text(row["message"]),
            "source": row["source"],
        }

    def _signal_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return _signal_row(row, self)

    def _normalise_existing_rows(self, conn: sqlite3.Connection) -> None:
        if not self._cipher:
            return
        for row in conn.execute("SELECT symbol, display_name, metadata_json FROM instruments").fetchall():
            conn.execute(
                "UPDATE instruments SET display_name = ?, metadata_json = ? WHERE symbol = ?",
                (self._store_text(self._load_text(row["display_name"])), self._store_json(self._load_json(row["metadata_json"])), row["symbol"]),
            )
        for row in conn.execute("SELECT symbol, date, open, high, low, close, volume FROM price_history").fetchall():
            conn.execute(
                """
                UPDATE price_history
                SET open = ?, high = ?, low = ?, close = ?, volume = ?
                WHERE symbol = ? AND date = ?
                """,
                (
                    self._store_number(self._load_number(row["open"])),
                    self._store_number(self._load_number(row["high"])),
                    self._store_number(self._load_number(row["low"])),
                    self._store_number(self._load_number(row["close"])),
                    self._store_number(self._load_number(row["volume"])),
                    row["symbol"],
                    row["date"],
                ),
            )
        for row in conn.execute("SELECT model_id, name, mandate_context, source_filename, metadata_json FROM models").fetchall():
            conn.execute(
                """
                UPDATE models
                SET name = ?, mandate_context = ?, source_filename = ?, metadata_json = ?
                WHERE model_id = ?
                """,
                (
                    self._store_text(self._load_text(row["name"])),
                    self._store_text(self._load_text(row["mandate_context"])),
                    self._store_text(self._load_text(row["source_filename"])),
                    self._store_json(self._load_json(row["metadata_json"])),
                    row["model_id"],
                ),
            )
        for row in conn.execute("SELECT rowid, ticker, weight, source_state, metadata_json FROM holdings").fetchall():
            conn.execute(
                "UPDATE holdings SET ticker = ?, weight = ?, source_state = ?, metadata_json = ? WHERE rowid = ?",
                (
                    self._store_text(self._load_text(row["ticker"])),
                    self._store_number(self._load_number(row["weight"])),
                    self._store_text(self._load_text(row["source_state"])),
                    self._store_json(self._load_json(row["metadata_json"])),
                    row["rowid"],
                ),
            )
        for row in conn.execute("SELECT id, message FROM refresh_log").fetchall():
            conn.execute("UPDATE refresh_log SET message = ? WHERE id = ?", (self._store_text(self._load_text(row["message"])), row["id"]))
        for row in conn.execute("SELECT * FROM signal_journal").fetchall():
            conn.execute(
                """
                UPDATE signal_journal
                SET target_name = ?, benchmark = ?, score = ?, action_label = ?,
                    data_mode = ?, source_counts_json = ?, forward_status = ?,
                    forward_start_date = ?, forward_end_date = ?,
                    forward_result_pct = ?, benchmark_result_pct = ?, alpha_pct = ?,
                    evaluated_at = ?, metadata_json = ?
                WHERE id = ?
                """,
                (
                    self._store_text(self._load_text(row["target_name"])),
                    self._store_text(self._load_text(row["benchmark"])),
                    self._store_number(self._load_number(row["score"])),
                    self._store_text(self._load_text(row["action_label"])),
                    self._store_text(self._load_text(row["data_mode"])),
                    self._store_json(self._load_json(row["source_counts_json"])),
                    self._store_text(self._load_text(row["forward_status"])),
                    self._store_text(self._load_text(row["forward_start_date"])),
                    self._store_text(self._load_text(row["forward_end_date"])),
                    self._store_number(self._load_number(row["forward_result_pct"])),
                    self._store_number(self._load_number(row["benchmark_result_pct"])),
                    self._store_number(self._load_number(row["alpha_pct"])),
                    self._store_text(self._load_text(row["evaluated_at"])),
                    self._store_json(self._load_json(row["metadata_json"])),
                    row["id"],
                ),
            )
        for row in conn.execute("SELECT * FROM report_snapshots").fetchall():
            conn.execute(
                """
                UPDATE report_snapshots
                SET target_name = ?, title = ?, data_mode = ?, display_label = ?,
                    source = ?, first_date = ?, last_date = ?, source_counts_json = ?,
                    model_metadata_json = ?, warnings_json = ?, report_json = ?,
                    ai_narrative = ?, html = ?, metadata_json = ?
                WHERE snapshot_id = ?
                """,
                (
                    self._store_text(self._load_text(row["target_name"])),
                    self._store_text(self._load_text(row["title"])),
                    self._store_text(self._load_text(row["data_mode"])),
                    self._store_text(self._load_text(row["display_label"])),
                    self._store_text(self._load_text(row["source"])),
                    self._store_text(self._load_text(row["first_date"])),
                    self._store_text(self._load_text(row["last_date"])),
                    self._store_json(self._load_json(row["source_counts_json"])),
                    self._store_json(self._load_json(row["model_metadata_json"])),
                    self._store_json(self._load_json(row["warnings_json"])),
                    self._store_json(self._load_json(row["report_json"])),
                    self._store_long_text(self._load_text(row["ai_narrative"]), limit=6000),
                    self._store_long_text(self._load_text(row["html"])),
                    self._store_json(self._load_json(row["metadata_json"])),
                    row["snapshot_id"],
                ),
            )

    def status(self) -> dict[str, Any]:
        status = {
            "configured": self.configured and not self.disabled,
            "available": self.available,
            "path": str(self.path) if self.path else "",
            "warning": self.warning,
            "schema_version": SCHEMA_VERSION if self.available else None,
            "encryption": dict(self.encryption),
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
                    status["last_refresh"] = self._refresh_row(last)
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
            meta_json = self._store_json(metadata or {})
            rows = []
            for idx, row in clean.iterrows():
                rows.append((
                    symbol,
                    str(idx.date()),
                    self._store_number(row.get("open")),
                    self._store_number(row.get("high")),
                    self._store_number(row.get("low")),
                    self._store_number(row.get("close")),
                    self._store_number(row.get("volume")),
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
                    (symbol, self._store_text(name), source, first_date, last_date, len(clean), created_at, now, meta_json),
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
                    df = pd.DataFrame([
                        {
                            "date": p["date"],
                            "open": self._load_number(p["open"]),
                            "high": self._load_number(p["high"]),
                            "low": self._load_number(p["low"]),
                            "close": self._load_number(p["close"]),
                            "volume": self._load_number(p["volume"]),
                        }
                        for p in prices
                    ])
                    df.index = pd.to_datetime(df.pop("date"), errors="coerce")
                    df = df[~df.index.isna()]
                    if "close" not in df or df["close"].dropna().empty:
                        continue
                    out.append(PersistedInstrument(
                        symbol=row["symbol"],
                        name=self._load_text(row["display_name"]),
                        source=row["source"],
                        df=df,
                        metadata=self._load_json(row["metadata_json"]),
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
            meta_json = self._store_json(metadata or {})
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
                    (
                        model_id,
                        self._store_text(name),
                        mandate_key,
                        self._store_text(clean_context),
                        self._store_text(safe_source),
                        created_at,
                        now,
                        meta_json,
                    ),
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
                            self._store_text(str(h["ticker"])),
                            self._store_number(h["weight"]),
                            self._store_text(str(h.get("source_state") or h.get("source") or "pending")),
                            self._store_json(h.get("metadata") or {}),
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
                        ORDER BY rowid
                        """,
                        (row["model_id"],),
                    ).fetchall()
                    holding_rows = [
                        {
                            "ticker": self._load_text(h["ticker"]),
                            "weight": float(self._load_number(h["weight"]) or 0.0),
                            "source_state": self._load_text(h["source_state"]),
                            "metadata": self._load_json(h["metadata_json"]),
                        }
                        for h in holdings
                    ]
                    holding_rows.sort(key=lambda h: (-float(h["weight"]), h["ticker"]))
                    out.append(PersistedModel(
                        id=row["model_id"],
                        name=self._load_text(row["name"]),
                        mandate_key=row["mandate_key"],
                        mandate_context=self._load_text(row["mandate_context"]),
                        source_filename=self._load_text(row["source_filename"]),
                        holdings=holding_rows,
                        metadata=self._load_json(row["metadata_json"]),
                    ))
                return out
        except Exception as exc:
            self.available = False
            self.warning = f"Could not load persisted models: {exc}"
            return []

    def next_model_governance_version(self, model_id: str) -> int:
        if not self.available:
            return 1
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT MAX(version) AS version FROM model_governance_events WHERE model_id = ?",
                    (model_id,),
                ).fetchone()
                current = int(row["version"] or 1) if row else 1
                return current + 1
        except Exception:
            return 1

    def record_model_governance_event(
        self,
        *,
        model_id: str,
        version: int,
        actor: str,
        action: str,
        note: str = "",
        approval_status: str = "",
        snapshot: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if not self.available:
            return None
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO model_governance_events
                      (model_id, created_at, version, actor, action, note,
                       approval_status, snapshot_json, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        redact_secrets(str(model_id))[:64],
                        utc_now(),
                        int(version),
                        self._store_text(redact_secrets(str(actor or "Advisor Console"))[:120]),
                        self._store_text(redact_secrets(str(action or "change_note"))[:48]),
                        self._store_text(redact_secrets(str(note or ""))[:800]),
                        self._store_text(redact_secrets(str(approval_status or ""))[:32]),
                        self._store_json(snapshot or {}),
                        self._store_json(metadata or {}),
                    ),
                )
                row = conn.execute(
                    """
                    SELECT *
                    FROM model_governance_events
                    WHERE id = last_insert_rowid()
                    """
                ).fetchone()
                return _model_governance_event_row(row, self) if row else None
        except Exception as exc:
            self.warning = f"Could not record model governance event: {exc}"
            return None

    def model_governance_events(self, model_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                if model_id:
                    rows = conn.execute(
                        """
                        SELECT *
                        FROM model_governance_events
                        WHERE model_id = ?
                        ORDER BY created_at DESC, id DESC
                        LIMIT ?
                        """,
                        (model_id, max(1, min(int(limit), 2000))),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT *
                        FROM model_governance_events
                        ORDER BY created_at DESC, id DESC
                        LIMIT ?
                        """,
                        (max(1, min(int(limit), 2000)),),
                    ).fetchall()
                return [_model_governance_event_row(row, self) for row in rows]
        except Exception:
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
                    (symbol, utc_now(), status, int(rows_added), self._store_text(redact_secrets(message)[:500]), source),
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
                return [self._refresh_row(row) for row in rows]
        except Exception:
            return []

    def record_signal_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            return {"recorded": False, "warning": self.warning}
        try:
            now = utc_now()
            source_counts = event.get("source_counts") or {}
            metadata = event.get("metadata") or {}
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO signal_journal
                      (dedupe_key, created_at, target_kind, target_id, target_name, benchmark,
                       input_start_date, input_end_date, input_rows, horizon_days,
                       score, action_label, data_mode, eligible_for_real_research,
                       source_counts_json, forward_status, forward_start_date,
                       forward_end_date, forward_result_pct, benchmark_result_pct,
                       alpha_pct, evaluated_at, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dedupe_key) DO UPDATE SET
                      target_name = excluded.target_name,
                      benchmark = excluded.benchmark,
                      score = excluded.score,
                      action_label = excluded.action_label,
                      data_mode = excluded.data_mode,
                      eligible_for_real_research = excluded.eligible_for_real_research,
                      source_counts_json = excluded.source_counts_json,
                      forward_status = excluded.forward_status,
                      forward_start_date = excluded.forward_start_date,
                      forward_end_date = excluded.forward_end_date,
                      forward_result_pct = excluded.forward_result_pct,
                      benchmark_result_pct = excluded.benchmark_result_pct,
                      alpha_pct = excluded.alpha_pct,
                      evaluated_at = excluded.evaluated_at,
                      metadata_json = excluded.metadata_json
                    """,
                    (
                        redact_secrets(str(event["dedupe_key"]))[:96],
                        now,
                        redact_secrets(str(event["target_kind"]))[:24],
                        redact_secrets(str(event["target_id"]))[:64],
                        self._store_text(redact_secrets(str(event["target_name"]))[:120]),
                        self._store_text(redact_secrets(str(event.get("benchmark") or ""))[:32]),
                        str(event["input_start_date"]),
                        str(event["input_end_date"]),
                        int(event["input_rows"]),
                        int(event["horizon_days"]),
                        self._store_number(event["score"]),
                        self._store_text(redact_secrets(str(event["action_label"]))[:16]),
                        self._store_text(redact_secrets(str(event.get("data_mode") or ""))[:32]),
                        int(bool(event.get("eligible_for_real_research"))),
                        self._store_json(source_counts),
                        self._store_text(redact_secrets(str(event.get("forward_status") or "pending"))[:16]),
                        self._store_text(str(event.get("forward_start_date") or "")),
                        self._store_text(str(event.get("forward_end_date") or "")),
                        self._store_number(event.get("forward_result_pct")),
                        self._store_number(event.get("benchmark_result_pct")),
                        self._store_number(event.get("alpha_pct")),
                        self._store_text(str(event.get("evaluated_at") or "")),
                        self._store_json(metadata),
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM signal_journal WHERE dedupe_key = ?",
                    (redact_secrets(str(event["dedupe_key"]))[:96],),
                ).fetchone()
                return {"recorded": True, "entry": self._signal_row(row) if row else None}
        except Exception as exc:
            self.warning = f"Could not record signal journal entry: {exc}"
            return {"recorded": False, "warning": self.warning}

    def update_signal_forward_result(self, dedupe_key: str, result: dict[str, Any]) -> None:
        if not self.available:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE signal_journal
                    SET forward_status = ?,
                        forward_start_date = ?,
                        forward_end_date = ?,
                        forward_result_pct = ?,
                        benchmark_result_pct = ?,
                        alpha_pct = ?,
                        evaluated_at = ?
                    WHERE dedupe_key = ?
                    """,
                    (
                        self._store_text(redact_secrets(str(result.get("forward_status") or "pending"))[:16]),
                        self._store_text(str(result.get("forward_start_date") or "")),
                        self._store_text(str(result.get("forward_end_date") or "")),
                        self._store_number(result.get("forward_result_pct")),
                        self._store_number(result.get("benchmark_result_pct")),
                        self._store_number(result.get("alpha_pct")),
                        self._store_text(str(result.get("evaluated_at") or "")),
                        redact_secrets(str(dedupe_key))[:96],
                    ),
                )
        except Exception as exc:
            self.warning = f"Could not update signal journal result: {exc}"

    def signal_journal(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM signal_journal
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (max(1, min(int(limit), 500)),),
                ).fetchall()
                return [self._signal_row(row) for row in rows]
        except Exception:
            return []

    def save_report_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            return {"saved": False, "warning": self.warning}
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO report_snapshots
                      (snapshot_id, created_at, target_kind, target_id, target_name,
                       title, data_mode, display_label, eligible_for_real_research,
                       source, row_count, first_date, last_date, source_counts_json,
                       model_metadata_json, warnings_json, report_json, ai_narrative,
                       html, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        redact_secrets(str(snapshot["id"]))[:64],
                        str(snapshot["created_at"]),
                        redact_secrets(str(snapshot["target_kind"]))[:24],
                        redact_secrets(str(snapshot["target_id"]))[:96],
                        self._store_text(redact_secrets(str(snapshot.get("target_name") or ""))[:160]),
                        self._store_text(redact_secrets(str(snapshot.get("title") or ""))[:220]),
                        self._store_text(redact_secrets(str(snapshot.get("data_mode") or ""))[:32]),
                        self._store_text(redact_secrets(str(snapshot.get("display_label") or ""))[:120]),
                        int(bool(snapshot.get("eligible_for_real_research"))),
                        self._store_text(redact_secrets(str(snapshot.get("source") or ""))[:32]),
                        int(snapshot.get("row_count") or 0),
                        self._store_text(str(snapshot.get("first_date") or "")),
                        self._store_text(str(snapshot.get("last_date") or "")),
                        self._store_json(snapshot.get("source_counts") or {}),
                        self._store_json(snapshot.get("model_metadata") or {}),
                        self._store_json({"warnings": snapshot.get("warnings") or []}),
                        self._store_json(snapshot.get("report") or {}),
                        self._store_long_text(redact_long_text(str(snapshot.get("ai_narrative") or ""), limit=6000), limit=6000),
                        self._store_long_text(str(snapshot.get("html") or "")),
                        self._store_json(snapshot.get("metadata") or {}),
                    ),
                )
            return {"saved": True, "snapshot": snapshot}
        except Exception as exc:
            self.warning = f"Could not save report snapshot: {exc}"
            return {"saved": False, "warning": self.warning}

    def report_snapshots(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM report_snapshots
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (max(1, min(int(limit), 200)),),
                ).fetchall()
                return [_report_snapshot_row(row, self, include_report=False) for row in rows]
        except Exception:
            return []

    def get_report_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM report_snapshots WHERE snapshot_id = ?",
                    (redact_secrets(str(snapshot_id))[:64],),
                ).fetchone()
                return _report_snapshot_row(row, self, include_report=True) if row else None
        except Exception:
            return None


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
        """
        CREATE TABLE IF NOT EXISTS signal_journal (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          dedupe_key TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL,
          target_kind TEXT NOT NULL,
          target_id TEXT NOT NULL,
          target_name TEXT NOT NULL,
          benchmark TEXT NOT NULL DEFAULT '',
          input_start_date TEXT NOT NULL,
          input_end_date TEXT NOT NULL,
          input_rows INTEGER NOT NULL,
          horizon_days INTEGER NOT NULL,
          score REAL NOT NULL,
          action_label TEXT NOT NULL,
          data_mode TEXT NOT NULL DEFAULT '',
          eligible_for_real_research INTEGER NOT NULL DEFAULT 0,
          source_counts_json TEXT NOT NULL DEFAULT '{}',
          forward_status TEXT NOT NULL DEFAULT 'pending',
          forward_start_date TEXT NOT NULL DEFAULT '',
          forward_end_date TEXT NOT NULL DEFAULT '',
          forward_result_pct REAL,
          benchmark_result_pct REAL,
          alpha_pct REAL,
          evaluated_at TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS report_snapshots (
          snapshot_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          target_kind TEXT NOT NULL,
          target_id TEXT NOT NULL,
          target_name TEXT NOT NULL DEFAULT '',
          title TEXT NOT NULL,
          data_mode TEXT NOT NULL DEFAULT '',
          display_label TEXT NOT NULL DEFAULT '',
          eligible_for_real_research INTEGER NOT NULL DEFAULT 0,
          source TEXT NOT NULL DEFAULT '',
          row_count INTEGER NOT NULL DEFAULT 0,
          first_date TEXT NOT NULL DEFAULT '',
          last_date TEXT NOT NULL DEFAULT '',
          source_counts_json TEXT NOT NULL DEFAULT '{}',
          model_metadata_json TEXT NOT NULL DEFAULT '{}',
          warnings_json TEXT NOT NULL DEFAULT '{}',
          report_json TEXT NOT NULL DEFAULT '{}',
          ai_narrative TEXT NOT NULL DEFAULT '',
          html TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_governance_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          model_id TEXT NOT NULL,
          created_at TEXT NOT NULL,
          version INTEGER NOT NULL,
          actor TEXT NOT NULL DEFAULT '',
          action TEXT NOT NULL DEFAULT 'change_note',
          note TEXT NOT NULL DEFAULT '',
          approval_status TEXT NOT NULL DEFAULT '',
          snapshot_json TEXT NOT NULL DEFAULT '{}',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          FOREIGN KEY(model_id) REFERENCES models(model_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, utc_now()),
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_price_history_symbol_date ON price_history(symbol, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_refresh_log_symbol_time ON refresh_log(symbol, attempted_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_journal_target ON signal_journal(target_kind, target_id, input_end_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_report_snapshots_target ON report_snapshots(target_kind, target_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_governance_model ON model_governance_events(model_id, created_at)")


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


def _signal_row(row: sqlite3.Row, store: SQLiteStore | None = None) -> dict[str, Any]:
    def text(name: str) -> str:
        return store._load_text(row[name]) if store else str(row[name] or "")

    def number(name: str) -> float | None:
        return store._load_number(row[name]) if store else _optional_float(row[name])

    def object_json(name: str) -> dict[str, Any]:
        return store._load_json(row[name]) if store else _json_loads(row[name])

    return {
        "id": int(row["id"]),
        "dedupe_key": row["dedupe_key"],
        "created_at": row["created_at"],
        "target_kind": row["target_kind"],
        "target_id": row["target_id"],
        "target_name": text("target_name"),
        "benchmark": text("benchmark"),
        "input_start_date": row["input_start_date"],
        "input_end_date": row["input_end_date"],
        "input_rows": int(row["input_rows"]),
        "horizon_days": int(row["horizon_days"]),
        "score": float(number("score") or 0.0),
        "action_label": text("action_label"),
        "data_mode": text("data_mode"),
        "eligible_for_real_research": bool(row["eligible_for_real_research"]),
        "source_counts": object_json("source_counts_json"),
        "forward_status": text("forward_status"),
        "forward_start_date": text("forward_start_date") or None,
        "forward_end_date": text("forward_end_date") or None,
        "forward_result_pct": number("forward_result_pct"),
        "benchmark_result_pct": number("benchmark_result_pct"),
        "alpha_pct": number("alpha_pct"),
        "evaluated_at": text("evaluated_at") or None,
        "metadata": object_json("metadata_json"),
    }


def _model_governance_event_row(row: sqlite3.Row, store: SQLiteStore | None = None) -> dict[str, Any]:
    def text(name: str) -> str:
        return store._load_text(row[name]) if store else str(row[name] or "")

    def object_json(name: str) -> dict[str, Any]:
        return store._load_json(row[name]) if store else _json_loads(row[name])

    return {
        "id": int(row["id"]),
        "model_id": row["model_id"],
        "created_at": row["created_at"],
        "version": int(row["version"]),
        "actor": text("actor"),
        "action": text("action"),
        "note": text("note"),
        "approval_status": text("approval_status"),
        "snapshot": object_json("snapshot_json"),
        "metadata": object_json("metadata_json"),
    }


def _report_snapshot_row(row: sqlite3.Row, store: SQLiteStore, *, include_report: bool) -> dict[str, Any]:
    warnings = store._load_json(row["warnings_json"]).get("warnings") or []
    metadata = store._load_json(row["metadata_json"])
    ai_narrative = store._load_text(row["ai_narrative"])
    snapshot = {
        "id": row["snapshot_id"],
        "created_at": row["created_at"],
        "target_kind": row["target_kind"],
        "target_id": row["target_id"],
        "target_name": store._load_text(row["target_name"]),
        "title": store._load_text(row["title"]),
        "data_mode": store._load_text(row["data_mode"]),
        "display_label": store._load_text(row["display_label"]),
        "eligible_for_real_research": bool(row["eligible_for_real_research"]),
        "source": store._load_text(row["source"]),
        "row_count": int(row["row_count"] or 0),
        "first_date": store._load_text(row["first_date"]) or None,
        "last_date": store._load_text(row["last_date"]) or None,
        "source_counts": store._load_json(row["source_counts_json"]),
        "model_metadata": store._load_json(row["model_metadata_json"]),
        "warnings": warnings if isinstance(warnings, list) else [],
        "ai_narrative": ai_narrative,
        "ai_narrative_included": bool(ai_narrative),
        "ai_narrative_status": metadata.get("ai_narrative_status") or ("included" if ai_narrative else "not_included"),
        "ai_provider": metadata.get("ai_provider") or {},
        "metadata": metadata,
    }
    if include_report:
        snapshot["report"] = store._load_json(row["report_json"])
        snapshot["html"] = store._load_text(row["html"])
    return snapshot


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
