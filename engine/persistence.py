"""SQLite persistence for local real-data workflows.

The store intentionally persists parsed market/model facts only. Raw uploads,
sample/simulated series, API keys, secrets, and browser/runtime artifacts do not
belong here.
"""
from __future__ import annotations

import atexit
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
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

SCHEMA_VERSION = 13  # 13: institutional provider, security, incident, and validation controls
REAL_SOURCES = {"live", "upload"}
DISABLED_VALUES = {"", "0", "false", "off", "disabled", "none"}
DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / ".helios" / "helios.db"
ENCRYPTED_PREFIX = "enc:v1:"
ENCRYPTED_DB_MAGIC = b"HELIOSDB1\n"
SQLITE_HEADER = b"SQLite format 3\x00"
SNAPSHOT_FLUSH_MAX_AGE_SECONDS = 3.0
BACKUP_DIR_NAME = "backups"
BACKUP_RETENTION = 5

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


_AUDIT_APPEND_LOCK = threading.Lock()


def _json_dumps_stable(payload: dict) -> str:
    """Deterministic serialization for audit hashing (sorted keys, no spaces)."""
    import json as _j
    try:
        return _j.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        return str(payload)


def _sha256_text(value: str) -> str:
    import hashlib
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


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
    def __init__(self, store: "SQLiteStore", *, durable: bool = False):
        self.store = store
        self.durable = durable
        self._changes_before = 0
        self._rollback_image: bytes | None = None

    def __enter__(self) -> sqlite3.Connection:
        self.store._conn_lock.acquire()
        if self.store._memory_conn is None:
            self.store._conn_lock.release()
            raise RuntimeError("encrypted SQLite persistence is not initialized")
        self._changes_before = self.store._memory_conn.total_changes
        if self.durable:
            self._rollback_image = self.store._memory_conn.serialize()
        return self.store._memory_conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if self.store._memory_conn is not None:
                if exc_type is None:
                    self.store._memory_conn.commit()
                    if self.store._memory_conn.total_changes != self._changes_before:
                        if self.durable:
                            try:
                                timer, self.store._flush_timer = self.store._flush_timer, None
                                if timer is not None:
                                    timer.cancel()
                                self.store._persist_encrypted_snapshot()
                                self.store._snapshot_dirty = False
                            except Exception as persist_exc:
                                if self._rollback_image is not None:
                                    self.store._memory_conn.deserialize(self._rollback_image)
                                    self.store._memory_conn.row_factory = sqlite3.Row
                                    self.store._memory_conn.execute("PRAGMA foreign_keys = ON")
                                self.store._snapshot_dirty = False
                                self.store.warning = f"Could not persist encrypted snapshot: {persist_exc}"
                                raise RuntimeError(self.store.warning) from persist_exc
                        else:
                            self.store._mark_snapshot_dirty()
                else:
                    self.store._memory_conn.rollback()
        finally:
            self.store._conn_lock.release()
        return False


class _PlaintextConnectionLease:
    """Commit/rollback like `with sqlite3.Connection`, then close the handle so
    plaintext-mode call sites don't leak connections (ResourceWarning)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        finally:
            self._conn.close()
        return False


class _NestedConnectionLease:
    """Reuse an explicit store transaction without committing it early."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _StoreTransaction:
    """One atomic transaction across persistence helpers and required audits."""

    def __init__(self, store: "SQLiteStore", *, durable: bool):
        self.store = store
        self.durable = durable
        self._nested = False
        self._lease: _EncryptedConnectionLease | _PlaintextConnectionLease | None = None
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        active = getattr(self.store._transaction_local, "connection", None)
        if active is not None:
            self._nested = True
            self.store._transaction_local.depth = int(
                getattr(self.store._transaction_local, "depth", 1)
            ) + 1
            self._conn = active
            return active
        self._lease = self.store._base_connect(durable=self.durable)
        self._conn = self._lease.__enter__()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if "within a transaction" not in str(exc).lower():
                self._lease.__exit__(type(exc), exc, exc.__traceback__)
                raise
        self.store._transaction_local.connection = self._conn
        self.store._transaction_local.depth = 1
        return self._conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._nested:
            self.store._transaction_local.depth = max(
                1, int(getattr(self.store._transaction_local, "depth", 2)) - 1
            )
            return False
        self.store._transaction_local.connection = None
        self.store._transaction_local.depth = 0
        if self._lease is None:
            return False
        return self._lease.__exit__(exc_type, exc, tb)


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
            # An explicitly configured key that cannot be used is fatal in every
            # mode: silently continuing would write a plaintext database.
            raise SystemExit(
                f"HELIOS_DB_ENCRYPTION_KEY is malformed ({exc}) — set it to a Fernet key, e.g. "
                "python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
            )

    key_path = Path(os.environ.get("HELIOS_DB_ENCRYPTION_KEY_PATH") or "").expanduser() if os.environ.get("HELIOS_DB_ENCRYPTION_KEY_PATH") else None
    if key_path is None and path is not None:
        key_path = path.with_suffix(".key")
    if key_path is None:
        warning = "SQLite persistence encryption is enabled but no key path is available."
        return None, _disabled_encryption_status(mode=mode, required=required, warning=warning), warning

    try:
        if key_path.is_file():
            try:
                key = key_path.read_text(encoding="utf-8").strip()
                cipher = _FieldCipher(key, key_source="local_key_file", required=required, mode=mode)
            except Exception as exc:
                # An existing key file that cannot be read or parsed is fatal in
                # every mode — never silently fall back to a plaintext database.
                raise SystemExit(
                    f"SQLite encryption key file {key_path} is unreadable or malformed ({exc}) — "
                    "restore the original Fernet key (or fix file permissions) before starting Helios."
                )
            return cipher, cipher.status, ""
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
    if _STORE is not None:
        _STORE.close()
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
        self._transaction_local = threading.local()
        self._memory_conn: sqlite3.Connection | None = None
        self._snapshot_dirty = False
        self._flush_timer: threading.Timer | None = None
        self.plaintext_artifacts: list[str] = []
        if disabled:
            self.warning = "SQLite persistence is disabled by HELIOS_DB_PATH."
            return
        if encryption_warning and self.encryption.get("required"):
            self.warning = encryption_warning
            return
        self._ensure()
        if self._memory_conn is not None:
            atexit.register(self.flush)
        self._warn_on_plaintext_artifacts()

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
                self._backup_existing_snapshot()
                self._ensure_encrypted_snapshot()
            else:
                if self.path.is_file() and self.path.read_bytes()[:len(ENCRYPTED_DB_MAGIC)] == ENCRYPTED_DB_MAGIC:
                    raise RuntimeError("encrypted SQLite persistence requires encryption to be enabled with the configured key")
                with self._connect() as conn:
                    _preflight_schema_version(conn)
                    _create_schema(conn)
                    _check_schema_version(conn)
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
                conn = _load_sqlite_payload_into_memory(
                    conn,
                    self._cipher.decrypt_bytes(raw[len(ENCRYPTED_DB_MAGIC):]),
                    self.path.parent,
                )
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
            _preflight_schema_version(active)
            _create_schema(active)
            # Fail closed on a newer-schema database before touching its rows
            # or rewriting the on-disk snapshot.
            _check_schema_version(active)
            self._normalise_existing_rows(active)
        # Always materialise the on-disk snapshot at startup so a fresh process
        # (and the startup backup on the next run) sees a valid encrypted file.
        # An unwritable path must raise here so the store reports unavailable.
        with self._conn_lock:
            timer, self._flush_timer = self._flush_timer, None
            if timer is not None:
                timer.cancel()
            self._persist_encrypted_snapshot()
            self._snapshot_dirty = False

    def _mark_snapshot_dirty(self) -> None:
        """Debounce snapshot writes: serializing + Fernet-encrypting the whole
        database after every transaction is too expensive for per-symbol live
        polling. Instead, writes mark the snapshot dirty and it is flushed at
        batch boundaries, after a short max-age, and at interpreter exit.
        Trade-off: a hard crash can lose the last few seconds of live polling
        writes, which the next refresh cycle re-fetches."""
        with self._conn_lock:
            self._snapshot_dirty = True
            if self._flush_timer is None:
                timer = threading.Timer(SNAPSHOT_FLUSH_MAX_AGE_SECONDS, self.flush)
                timer.daemon = True
                self._flush_timer = timer
                timer.start()

    def flush(self, force: bool = False) -> None:
        """Write pending in-memory changes to the encrypted on-disk snapshot."""
        with self._conn_lock:
            timer, self._flush_timer = self._flush_timer, None
            if timer is not None:
                timer.cancel()
            if self._memory_conn is None or not (self._snapshot_dirty or force):
                self._snapshot_dirty = False
                return
            try:
                self._persist_encrypted_snapshot()
                self._snapshot_dirty = False
            except Exception as exc:
                self.warning = f"Could not persist encrypted snapshot: {exc}"

    def close(self) -> None:
        """Flush pending writes and release the in-memory connection so a
        replaced store (tests, reconfiguration) doesn't leak the handle."""
        self.flush()
        with self._conn_lock:
            conn, self._memory_conn = self._memory_conn, None
            if conn is not None:
                conn.close()

    def _backup_existing_snapshot(self) -> Path | None:
        """Startup safety copy of the encrypted snapshot, keeping the newest few."""
        if self.path is None or not self.path.is_file():
            return None
        try:
            raw = self.path.read_bytes()
            if not raw.startswith(ENCRYPTED_DB_MAGIC):
                return None
            backups = self.path.parent / BACKUP_DIR_NAME
            backups.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            target = backups / f"{self.path.stem}-{stamp}{self.path.suffix}"
            target.write_bytes(raw)
            try:
                target.chmod(0o600)
            except Exception:
                pass
            for stale in sorted(backups.glob(f"{self.path.stem}-*{self.path.suffix}"))[:-BACKUP_RETENTION]:
                try:
                    stale.unlink()
                except OSError:
                    pass
            return target
        except Exception as exc:
            print(f"  ⚠  Could not write persistence backup: {exc}", file=sys.stderr, flush=True)
            return None

    def backup_status(self) -> dict[str, Any]:
        if self.path is None:
            return {"count": 0, "latest": None}
        try:
            files = sorted((self.path.parent / BACKUP_DIR_NAME).glob(f"{self.path.stem}-*{self.path.suffix}"))
        except OSError:
            files = []
        return {"count": len(files), "latest": files[-1].name if files else None}

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

    def _warn_on_plaintext_artifacts(self) -> None:
        if self.path is None:
            return
        expected: set[Path] = set()
        if self._cipher is None:
            expected = {self.path, self.path.with_name(f"{self.path.name}.tmp")}
            if self.available and self.path.is_file():
                print(
                    f"  ⚠  Unencrypted database files in {self.path.parent}: {self.path.name} — "
                    "enable HELIOS_DB_ENCRYPTION to protect data at rest.",
                    file=sys.stderr,
                    flush=True,
                )
        artifacts = _plaintext_sqlite_artifacts(self.path.parent, ignore=expected)
        self.plaintext_artifacts = [str(p) for p in artifacts]
        if not artifacts:
            return
        listed = ", ".join(self.plaintext_artifacts)
        print(
            f"[helios] WARNING: unencrypted SQLite file(s) found in {self.path.parent}: {listed}. "
            "Helios does not read these files, but they may expose client data at rest. "
            "Delete them or re-encrypt them with the configured database key.",
            file=sys.stderr,
            flush=True,
        )

    def _base_connect(self, *, durable: bool = False):
        if self.path is None:
            raise RuntimeError("SQLite persistence is disabled.")
        if self._memory_conn is not None:
            return _EncryptedConnectionLease(self, durable=durable)
        conn = sqlite3.connect(str(self.path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        if durable:
            conn.execute("PRAGMA synchronous = FULL")
        return _PlaintextConnectionLease(conn)

    def _connect(self, *, durable: bool = False):
        active = getattr(self._transaction_local, "connection", None)
        if active is not None:
            return _NestedConnectionLease(active)
        return self._base_connect(durable=durable)

    def transaction(self, *, durable: bool = False) -> _StoreTransaction:
        return _StoreTransaction(self, durable=durable)

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

    def _data_quality_alert_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return _data_quality_alert_row(row, self)

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
        for row in conn.execute("SELECT * FROM data_quality_alerts").fetchall():
            conn.execute(
                """
                UPDATE data_quality_alerts
                SET category = ?, severity = ?, target = ?, detail = ?,
                    next_step = ?, status = ?, resolved_at = ?,
                    last_fingerprint = ?, metadata_json = ?
                WHERE alert_id = ?
                """,
                (
                    self._store_text(self._load_text(row["category"])),
                    self._store_text(self._load_text(row["severity"])),
                    self._store_text(self._load_text(row["target"])),
                    self._store_text(self._load_text(row["detail"])),
                    self._store_text(self._load_text(row["next_step"])),
                    self._store_text(self._load_text(row["status"])),
                    self._store_text(self._load_text(row["resolved_at"])),
                    self._store_text(self._load_text(row["last_fingerprint"])),
                    self._store_json(self._load_json(row["metadata_json"])),
                    row["alert_id"],
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
            "backups": self.backup_status(),
            "plaintext_artifacts": list(self.plaintext_artifacts),
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
                # Replace, not union: the persisted history must be exactly the
                # frame the session analyzed (mirrors in-memory register()), so
                # reloads can't stitch windows with different adjustment bases.
                conn.execute("DELETE FROM price_history WHERE symbol = ?", (symbol,))
                conn.executemany(
                    """
                    INSERT INTO price_history
                      (symbol, date, open, high, low, close, volume, source, adjusted)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            with self.transaction(durable=True) as conn:
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
                self.audit_append(
                    "model_governance.record",
                    {
                        "model_id": str(model_id)[:64],
                        "version": int(version),
                        "action": str(action or "change_note")[:48],
                        "approval_status": str(approval_status or "")[:32],
                    },
                    actor=actor,
                    required=True,
                )
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

    def sync_data_quality_alerts(self, alerts: list[dict[str, Any]], generated_at: str | None = None) -> dict[str, Any]:
        if not self.available:
            return _data_quality_alert_result([], tracking_available=False, warning=self.warning)
        now = redact_secrets(str(generated_at or utc_now()))[:40]
        incoming = _normalise_alerts(alerts)
        incoming_ids = {alert["id"] for alert in incoming}
        notification_states: dict[str, str] = {}
        try:
            with self._connect() as conn:
                existing = {
                    row["alert_id"]: row
                    for row in conn.execute("SELECT * FROM data_quality_alerts").fetchall()
                }
                for alert in incoming:
                    alert_id = alert["id"]
                    fingerprint = _alert_fingerprint(alert)
                    row = existing.get(alert_id)
                    if row:
                        prior_status = self._load_text(row["status"])
                        prior_fingerprint = self._load_text(row["last_fingerprint"])
                        if prior_status == "resolved":
                            state = "reopened"
                        elif prior_fingerprint != fingerprint:
                            state = "changed"
                        else:
                            state = "active"
                        notification_states[alert_id] = state
                        if state == "active":
                            # Unchanged alert re-observed (typically a GET-driven
                            # sync): touch last_seen_at only. occurrence_count
                            # counts distinct raisings (new/reopened/changed),
                            # not page views — GET must not mutate audit state.
                            conn.execute(
                                "UPDATE data_quality_alerts SET last_seen_at = ? WHERE alert_id = ?",
                                (now, alert_id),
                            )
                            continue
                        occurrence_count = int(row["occurrence_count"] or 0) + 1
                        conn.execute(
                            """
                            UPDATE data_quality_alerts
                            SET category = ?, severity = ?, target = ?, detail = ?,
                                next_step = ?, status = 'active',
                                last_seen_at = ?, last_changed_at = ?,
                                resolved_at = '', occurrence_count = ?,
                                last_fingerprint = ?, metadata_json = ?
                            WHERE alert_id = ?
                            """,
                            (
                                self._store_text(alert["category"]),
                                self._store_text(alert["severity"]),
                                self._store_text(alert["target"]),
                                self._store_text(alert["detail"]),
                                self._store_text(alert["next_step"]),
                                now,
                                now if state in {"reopened", "changed"} else row["last_changed_at"],
                                occurrence_count,
                                self._store_text(fingerprint),
                                self._store_json(alert.get("metadata") or {}),
                                alert_id,
                            ),
                        )
                    else:
                        notification_states[alert_id] = "new"
                        conn.execute(
                            """
                            INSERT INTO data_quality_alerts
                              (alert_id, category, severity, target, detail, next_step,
                               status, first_seen_at, last_seen_at, last_changed_at,
                               resolved_at, occurrence_count, last_fingerprint, metadata_json)
                            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, '', 1, ?, ?)
                            """,
                            (
                                alert_id,
                                self._store_text(alert["category"]),
                                self._store_text(alert["severity"]),
                                self._store_text(alert["target"]),
                                self._store_text(alert["detail"]),
                                self._store_text(alert["next_step"]),
                                now,
                                now,
                                now,
                                self._store_text(fingerprint),
                                self._store_json(alert.get("metadata") or {}),
                            ),
                        )

                for row in existing.values():
                    alert_id = row["alert_id"]
                    if alert_id in incoming_ids or self._load_text(row["status"]) == "resolved":
                        continue
                    notification_states[alert_id] = "resolved"
                    conn.execute(
                        """
                        UPDATE data_quality_alerts
                        SET status = 'resolved',
                            last_changed_at = ?,
                            resolved_at = ?
                        WHERE alert_id = ?
                        """,
                        (now, now, alert_id),
                    )

                rows = conn.execute(
                    """
                    SELECT *
                    FROM data_quality_alerts
                    ORDER BY
                      CASE status WHEN 'active' THEN 0 ELSE 1 END,
                      CASE severity WHEN 'blocker' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                      last_changed_at DESC,
                      target ASC
                    """
                ).fetchall()
            out = []
            for row in rows:
                alert = self._data_quality_alert_row(row)
                alert["notification_state"] = notification_states.get(alert["id"], "active" if alert["status"] == "active" else "resolved")
                alert["should_notify"] = alert["notification_state"] in {"new", "reopened", "changed", "resolved"}
                out.append(alert)
            return _data_quality_alert_result(out, tracking_available=True)
        except Exception as exc:
            self.warning = f"Could not sync data quality alerts: {exc}"
            return _data_quality_alert_result(incoming, tracking_available=False, warning=self.warning)

    def data_quality_alerts(self, limit: int = 500) -> dict[str, Any]:
        """Return persisted alert state without mutating alert history."""
        if not self.available:
            return _data_quality_alert_result([], tracking_available=False, warning=self.warning)
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM data_quality_alerts
                    ORDER BY
                      CASE status WHEN 'active' THEN 0 ELSE 1 END,
                      CASE severity WHEN 'blocker' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                      last_changed_at DESC,
                      target ASC
                    LIMIT ?
                    """,
                    (max(1, min(int(limit), 5_000)),),
                ).fetchall()
            alerts = []
            for row in rows:
                alert = self._data_quality_alert_row(row)
                alert["notification_state"] = "active" if alert["status"] == "active" else "resolved"
                alert["should_notify"] = False
                alerts.append(alert)
            return _data_quality_alert_result(alerts, tracking_available=True)
        except Exception as exc:
            self.warning = f"Could not read data quality alerts: {exc}"
            return _data_quality_alert_result([], tracking_available=False, warning=self.warning)

    def record_signal_event(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            return {"recorded": False, "warning": self.warning}
        try:
            now = utc_now()
            source_counts = event.get("source_counts") or {}
            metadata = event.get("metadata") or {}
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO signal_journal
                      (dedupe_key, created_at, target_kind, target_id, target_name, benchmark,
                       input_start_date, input_end_date, input_rows, horizon_days,
                       score, action_label, data_mode, eligible_for_real_research,
                       source_counts_json, forward_status, forward_start_date,
                       forward_end_date, forward_result_pct, benchmark_result_pct,
                       alpha_pct, evaluated_at, metadata_json, trial_id, recording_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dedupe_key) DO NOTHING
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
                        redact_secrets(str(event.get("trial_id") or ""))[:64],
                        redact_secrets(str(event.get("recording_source") or "exploratory"))[:24],
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM signal_journal WHERE dedupe_key = ?",
                    (redact_secrets(str(event["dedupe_key"]))[:96],),
                ).fetchone()
                inserted = bool(cur.rowcount)
                return {"recorded": inserted, "duplicate": not inserted,
                        "entry": self._signal_row(row) if row else None}
        except Exception as exc:
            self.warning = f"Could not record signal journal entry: {exc}"
            return {"recorded": False, "warning": self.warning}

    def update_signal_forward_result(self, dedupe_key: str, result: dict[str, Any]) -> bool:
        if not self.available:
            return False
        try:
            with self._connect() as conn:
                cur = conn.execute(
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
                return bool(cur.rowcount)
        except Exception as exc:
            self.warning = f"Could not update signal journal result: {exc}"
            return False

    # ----------------------------------------------------------------- #
    # Decision journal — the OPERATOR's calls (vs the engine's), scored
    # over time so agree/override value is measurable. Append-only facts;
    # only outcome fields are ever updated.
    # ----------------------------------------------------------------- #
    def record_decision(self, entry: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            return {"recorded": False, "warning": self.warning}
        try:
            with self.transaction(durable=True) as conn:
                conn.execute(
                    """
                    INSERT INTO decision_journal
                      (decision_id, created_at, target_kind, target_id, target_name, mandate,
                       benchmark, engine_action, engine_score, tactical_action, strategic_action,
                       my_action, agreement, rationale, decision_date, decision_price, data_mode,
                       context_json, outcome_status, outcomes_json, evaluated_at, trial_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        redact_secrets(str(entry["decision_id"]))[:64],
                        utc_now(),
                        redact_secrets(str(entry["target_kind"]))[:24],
                        redact_secrets(str(entry["target_id"]))[:64],
                        self._store_text(redact_secrets(str(entry.get("target_name") or ""))[:120]),
                        redact_secrets(str(entry.get("mandate") or ""))[:32],
                        self._store_text(redact_secrets(str(entry.get("benchmark") or ""))[:32]),
                        self._store_text(redact_secrets(str(entry.get("engine_action") or ""))[:16]),
                        self._store_number(entry.get("engine_score")),
                        self._store_text(redact_secrets(str(entry.get("tactical_action") or ""))[:16]),
                        self._store_text(redact_secrets(str(entry.get("strategic_action") or ""))[:16]),
                        self._store_text(redact_secrets(str(entry["my_action"]))[:16]),
                        redact_secrets(str(entry.get("agreement") or ""))[:16],
                        # redact_long_text, not redact_secrets: the latter caps
                        # its INPUT at 800 chars, silently truncating rationales
                        # before the [:2000] limit applied (review finding).
                        self._store_text(redact_long_text(str(entry.get("rationale") or ""), limit=2000)),
                        str(entry.get("decision_date") or ""),
                        self._store_number(entry.get("decision_price")),
                        redact_secrets(str(entry.get("data_mode") or ""))[:32],
                        self._store_json(entry.get("context") or {}),
                        redact_secrets(str(entry.get("outcome_status") or "pending"))[:16],
                        self._store_json(entry.get("outcomes") or {}),
                        str(entry.get("evaluated_at") or ""),
                        redact_secrets(str(entry.get("trial_id") or ""))[:64],
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM decision_journal WHERE decision_id = ?",
                    (redact_secrets(str(entry["decision_id"]))[:64],),
                ).fetchone()
                self.audit_append("decision.record", {
                    "decision_id": entry["decision_id"], "target_id": entry.get("target_id"),
                    "my_action": entry.get("my_action"), "decision_date": entry.get("decision_date"),
                    "decision_price": entry.get("decision_price")}, required=True)
            return {"recorded": True, "entry": self._decision_row(row) if row else None}
        except Exception as exc:
            self.warning = f"Could not record decision: {exc}"
            return {"recorded": False, "warning": self.warning}

    def decision_journal(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM decision_journal ORDER BY created_at DESC, decision_id DESC LIMIT ?",
                    (max(1, min(int(limit), 1000)),),
                ).fetchall()
                return [self._decision_row(row) for row in rows]
        except Exception as exc:
            self.warning = f"Could not read decision journal: {exc}"
            return []

    def update_decision_outcomes(self, decision_id: str, outcomes: dict[str, Any],
                                 status: str, evaluated_at: str) -> bool:
        if not self.available:
            return False
        try:
            with self.transaction(durable=True) as conn:
                # Guarded MERGE, not a wholesale replace: concurrent GETs
                # under multithreaded waitress evaluate the same pending rows
                # from stale snapshots, and last-writer-wins dropped already-
                # measured horizons (review finding). Measured-once horizons
                # are immutable: existing keys win; not_measurable is sticky.
                clean_id = redact_secrets(str(decision_id))[:64]
                row = conn.execute(
                    "SELECT outcomes_json, outcome_status FROM decision_journal WHERE decision_id = ?",
                    (clean_id,),
                ).fetchone()
                existing = self._load_json(row["outcomes_json"]) if row else {}
                existing_status = str(row["outcome_status"] or "") if row else ""
                merged = {**(outcomes or {}), **existing}
                if existing_status == "not_measurable" or str(status) == "not_measurable":
                    merged_status = "not_measurable"
                elif len(merged) >= 3:      # all of 21/63/252 present
                    merged_status = "measured"
                elif merged:
                    merged_status = "partial"
                else:
                    merged_status = str(status or "pending")
                cur = conn.execute(
                    """
                    UPDATE decision_journal
                    SET outcomes_json = ?, outcome_status = ?, evaluated_at = ?
                    WHERE decision_id = ?
                    """,
                    (
                        self._store_json(merged),
                        redact_secrets(merged_status)[:16],
                        str(evaluated_at or ""),
                        clean_id,
                    ),
                )
                self.audit_append("decision.outcomes", {
                    "decision_id": clean_id, "status": merged_status,
                    "horizons": sorted(merged), "evaluated_at": str(evaluated_at or "")}, required=True)
                return bool(cur.rowcount)
        except Exception as exc:
            self.warning = f"Could not update decision outcomes: {exc}"
            return False

    def _decision_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "decision_id": row["decision_id"],
            "created_at": row["created_at"],
            "target_kind": row["target_kind"],
            "target_id": row["target_id"],
            "target_name": self._load_text(row["target_name"]),
            "mandate": row["mandate"],
            "benchmark": self._load_text(row["benchmark"]),
            "engine_action": self._load_text(row["engine_action"]),
            "engine_score": self._load_number(row["engine_score"]),
            "tactical_action": self._load_text(row["tactical_action"]),
            "strategic_action": self._load_text(row["strategic_action"]),
            "my_action": self._load_text(row["my_action"]),
            "agreement": row["agreement"],
            "rationale": self._load_text(row["rationale"]),
            "decision_date": row["decision_date"],
            "decision_price": self._load_number(row["decision_price"]),
            "data_mode": row["data_mode"],
            "context": self._load_json(row["context_json"]),
            "outcome_status": row["outcome_status"],
            "outcomes": self._load_json(row["outcomes_json"]),
            "evaluated_at": row["evaluated_at"],
            "trial_id": row["trial_id"] if "trial_id" in row.keys() else "",
        }

    # ----------------------------------------------------------------- #
    # Macro history — one reading per UTC day (upsert) so the regime layer
    # can reason about stance/GPR CHANGES, not just levels.
    # ----------------------------------------------------------------- #
    def record_macro_reading(self, reading: dict[str, Any]) -> None:
        if not self.available:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO macro_history
                      (reading_date, recorded_at, fed_stance, fed_n_documents,
                       gpr_index, fomc_days_until, policy_themes_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(reading_date) DO UPDATE SET
                      recorded_at = excluded.recorded_at,
                      fed_stance = excluded.fed_stance,
                      fed_n_documents = excluded.fed_n_documents,
                      gpr_index = excluded.gpr_index,
                      fomc_days_until = excluded.fomc_days_until,
                      policy_themes_json = excluded.policy_themes_json
                    """,
                    (
                        str(reading["reading_date"]),
                        utc_now(),
                        self._store_number(reading.get("fed_stance")),
                        int(reading["fed_n_documents"]) if reading.get("fed_n_documents") is not None else None,
                        self._store_number(reading.get("gpr_index")),
                        int(reading["fomc_days_until"]) if reading.get("fomc_days_until") is not None else None,
                        self._store_json(reading.get("policy_themes") or {}),
                    ),
                )
        except Exception as exc:
            self.warning = f"Could not record macro reading: {exc}"

    def macro_history(self, limit: int = 60) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM macro_history ORDER BY reading_date DESC LIMIT ?",
                    (max(1, min(int(limit), 400)),),
                ).fetchall()
                return [{
                    "reading_date": row["reading_date"],
                    "recorded_at": row["recorded_at"],
                    "fed_stance": self._load_number(row["fed_stance"]),
                    "fed_n_documents": row["fed_n_documents"],
                    "gpr_index": self._load_number(row["gpr_index"]),
                    "fomc_days_until": row["fomc_days_until"],
                    "policy_themes": self._load_json(row["policy_themes_json"]),
                } for row in rows]
        except Exception as exc:
            self.warning = f"Could not read macro history: {exc}"
            return []

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

    def pending_signal_entries(self, limit: int = 500) -> list[dict[str, Any]]:
        """Journal rows still awaiting a forward measurement, OLDEST first.

        The refresh loop used the newest-first ``signal_journal`` listing, so
        pending 63/252-day entries fell out of its window long before their
        forward horizon completed and were never measured again (review
        finding). Oldest-first over pending-only rows cannot starve.
        """
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM signal_journal
                    WHERE forward_status NOT IN ('measured', 'not_measurable')
                    ORDER BY created_at ASC, id ASC
                    LIMIT ?
                    """,
                    (max(1, min(int(limit), 2000)),),
                ).fetchall()
                return [self._signal_row(row) for row in rows]
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # Ledger: fills, account snapshots/positions, account->model mapping
    # ------------------------------------------------------------------ #
    def record_fills(self, fills: list[dict[str, Any]]) -> dict[str, Any]:
        """Idempotent fill import: the dedupe key makes CSV re-imports no-ops."""
        if not self.available:
            return {"inserted": 0, "duplicates": 0, "warning": self.warning}
        inserted = duplicates = 0
        try:
            with self.transaction(durable=True) as conn:
                for fill in fills:
                    cur = conn.execute(
                        """
                        INSERT OR IGNORE INTO trade_fills(
                          fill_id, account_id, trade_date, settle_date, ticker, side,
                          shares, price, fees, amount, source_file, decision_id,
                          dedupe_key, created_at, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(fill["fill_id"])[:64],
                            redact_secrets(str(fill["account_id"]))[:64],
                            str(fill["trade_date"])[:10],
                            str(fill.get("settle_date") or "")[:10],
                            str(fill.get("ticker") or "").upper()[:16],
                            str(fill["side"])[:12],
                            float(fill.get("shares") or 0.0),
                            float(fill.get("price") or 0.0),
                            float(fill.get("fees") or 0.0),
                            (float(fill["amount"]) if fill.get("amount") is not None else None),
                            self._store_text(str(fill.get("source_file") or "")[:120]),
                            str(fill.get("decision_id") or "")[:64],
                            str(fill["dedupe_key"])[:64],
                            utc_now(),
                            self._store_json(fill.get("metadata") or {}),
                        ),
                    )
                    if cur.rowcount:
                        inserted += 1
                    else:
                        duplicates += 1
                if inserted:
                    self.audit_append("ledger.fills", {
                        "inserted": inserted, "duplicates": duplicates,
                        "accounts": sorted({str(f.get("account_id") or "") for f in fills}),
                        "dedupe_keys": sorted(str(f.get("dedupe_key") or "") for f in fills)[:50]}, required=True)
            return {"inserted": inserted, "duplicates": duplicates}
        except Exception as exc:
            self.warning = f"Could not record fills: {exc}"
            return {"inserted": inserted, "duplicates": duplicates, "warning": str(exc)}

    def fills(self, account_id: str = "", limit: int = 2000) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                if account_id:
                    rows = conn.execute(
                        "SELECT * FROM trade_fills WHERE account_id = ? "
                        "ORDER BY trade_date ASC, fill_id ASC LIMIT ?",
                        (account_id, max(1, min(int(limit), 20000))),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM trade_fills ORDER BY trade_date ASC, fill_id ASC LIMIT ?",
                        (max(1, min(int(limit), 20000)),),
                    ).fetchall()
                return [self._fill_row(row) for row in rows]
        except Exception:
            return []

    def _fill_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "fill_id": row["fill_id"],
            "account_id": row["account_id"],
            "trade_date": row["trade_date"],
            "settle_date": row["settle_date"],
            "ticker": row["ticker"],
            "side": row["side"],
            "shares": row["shares"],
            "price": row["price"],
            "fees": row["fees"],
            "amount": row["amount"],
            "source_file": self._load_text(row["source_file"]),
            "decision_id": row["decision_id"],
            "created_at": row["created_at"],
            "metadata": self._load_json(row["metadata_json"]),
        }

    def record_account_snapshot(self, snapshot: dict[str, Any],
                                positions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if not self.available:
            return {"recorded": False, "warning": self.warning}
        try:
            account_id = redact_secrets(str(snapshot["account_id"]))[:64]
            as_of = str(snapshot["as_of"])[:10]
            clean_positions = sorted([
                {
                    "ticker": str(pos.get("ticker") or "").upper()[:16],
                    "shares": float(pos.get("shares") or 0.0),
                    "price": float(pos["price"]) if pos.get("price") is not None else None,
                    "market_value": float(pos["market_value"]) if pos.get("market_value") is not None else None,
                    "cost_basis": float(pos["cost_basis"]) if pos.get("cost_basis") is not None else None,
                }
                for pos in positions or []
            ], key=lambda row: row["ticker"])
            evidence_payload = {
                "account_id": account_id,
                "as_of": as_of,
                "cash": float(snapshot["cash"]) if snapshot.get("cash") is not None else None,
                "total_value": float(snapshot["total_value"]),
                "source": str(snapshot.get("source") or "")[:64],
                "positions": clean_positions,
            }
            content_hash = __import__("hashlib").sha256(
                _json_dumps_stable(evidence_payload).encode("utf-8")
            ).hexdigest()
            with self.transaction(durable=True) as conn:
                prior = conn.execute(
                    "SELECT * FROM account_snapshot_revisions "
                    "WHERE account_id = ? AND as_of = ? ORDER BY revision DESC LIMIT 1",
                    (account_id, as_of),
                ).fetchone()
                if prior and prior["content_hash"] == content_hash:
                    return {
                        "recorded": False,
                        "duplicate": True,
                        "snapshot_id": prior["snapshot_id"],
                        "revision": int(prior["revision"]),
                    }
                revision = int(prior["revision"] or 0) + 1 if prior else 1
                snapshot_id = f"acct-{__import__('uuid').uuid4().hex[:24]}"
                reason = str(
                    snapshot.get("correction_reason")
                    or (snapshot.get("metadata") or {}).get("correction_reason")
                    or ("Corrected source re-import." if prior else "Initial snapshot.")
                )[:240]
                created_at = utc_now()
                conn.execute(
                    """
                    INSERT INTO account_snapshot_revisions(
                      snapshot_id, account_id, as_of, revision, supersedes_snapshot_id,
                      correction_reason, cash, total_value, source, content_hash,
                      created_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id, account_id, as_of, revision,
                        prior["snapshot_id"] if prior else "", reason,
                        evidence_payload["cash"], evidence_payload["total_value"],
                        evidence_payload["source"], content_hash, created_at,
                        self._store_json(snapshot.get("metadata") or {}),
                    ),
                )
                for pos in clean_positions:
                    conn.execute(
                        """
                        INSERT INTO account_position_revisions(
                          snapshot_id, ticker, shares, price, market_value, cost_basis
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (snapshot_id, pos["ticker"], pos["shares"], pos["price"],
                         pos["market_value"], pos["cost_basis"]),
                    )
                # Compatibility projection: existing analytics read the current
                # revision through these tables; immutable history lives above.
                conn.execute(
                    """
                    INSERT OR REPLACE INTO account_snapshots(
                      account_id, as_of, cash, total_value, source, created_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        as_of,
                        evidence_payload["cash"],
                        evidence_payload["total_value"],
                        evidence_payload["source"],
                        created_at,
                        self._store_json(snapshot.get("metadata") or {}),
                    ),
                )
                conn.execute(
                    "DELETE FROM account_positions WHERE account_id = ? AND as_of = ?",
                    (str(snapshot["account_id"])[:64], str(snapshot["as_of"])[:10]),
                )
                for pos in clean_positions:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO account_positions(
                          account_id, as_of, ticker, shares, price, market_value, cost_basis
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            account_id,
                            as_of,
                            pos["ticker"], pos["shares"], pos["price"],
                            pos["market_value"], pos["cost_basis"],
                        ),
                    )
                self.audit_append("account.snapshot", {
                    "snapshot_id": snapshot_id, "account_id": account_id,
                    "as_of": as_of, "revision": revision, "content_hash": content_hash,
                }, required=True)
            return {"recorded": True, "snapshot_id": snapshot_id, "revision": revision}
        except Exception as exc:
            self.warning = f"Could not record account snapshot: {exc}"
            return {"recorded": False, "warning": self.warning}

    def account_snapshot_revisions(self, account_id: str, as_of: str | None = None) -> list[dict[str, Any]]:
        """Append-only correction history, newest revision first per date."""
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                if as_of:
                    rows = conn.execute(
                        "SELECT * FROM account_snapshot_revisions WHERE account_id = ? AND as_of = ? "
                        "ORDER BY revision DESC", (account_id, str(as_of)[:10])).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM account_snapshot_revisions WHERE account_id = ? "
                        "ORDER BY as_of DESC, revision DESC", (account_id,)).fetchall()
                return [{
                    **dict(row),
                    "metadata": self._load_json(row["metadata_json"]),
                    "positions": self._account_position_revision_rows(conn, row["snapshot_id"]),
                } for row in rows]
        except Exception:
            return []

    def _account_position_revision_rows(self, conn: sqlite3.Connection, snapshot_id: str) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT ticker, shares, price, market_value, cost_basis "
            "FROM account_position_revisions WHERE snapshot_id = ? ORDER BY market_value DESC",
            (snapshot_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def record_evidence_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        """Insert an immutable calculation envelope; an ID can never be overwritten."""
        if not self.available:
            return {"recorded": False, "warning": self.warning}
        try:
            with self.transaction(durable=True) as conn:
                existing = conn.execute(
                    "SELECT input_hash, output_hash, envelope_hash FROM evidence_snapshots WHERE evidence_id = ?",
                    (str(snapshot["evidence_id"]),),
                ).fetchone()
                if existing:
                    same = (
                        existing["input_hash"] == snapshot["input_hash"]
                        and existing["output_hash"] == snapshot["output_hash"]
                        and existing["envelope_hash"] == snapshot["envelope_hash"]
                    )
                    return {"recorded": False, "duplicate": same,
                            "warning": "Evidence ID collision." if not same else ""}
                conn.execute(
                    """
                    INSERT INTO evidence_snapshots(
                      evidence_id, created_at, artifact_kind, target_kind, target_id,
                      calculation_version, input_hash, output_hash, envelope_hash, manifest_json,
                      input_json, output_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(snapshot["evidence_id"])[:64], str(snapshot["created_at"]),
                        str(snapshot["artifact_kind"])[:32], str(snapshot.get("target_kind") or "")[:24],
                        str(snapshot.get("target_id") or "")[:96],
                        str(snapshot["calculation_version"])[:64], str(snapshot["input_hash"])[:64],
                        str(snapshot["output_hash"])[:64], str(snapshot["envelope_hash"])[:64],
                        _evidence_json_dumps(snapshot.get("manifest") or {}),
                        _evidence_json_dumps(snapshot.get("input") or {}),
                        _evidence_json_dumps(snapshot.get("output") or {}),
                    ),
                )
                self.audit_append("evidence.snapshot", {
                    "evidence_id": snapshot["evidence_id"], "artifact_kind": snapshot["artifact_kind"],
                    "input_hash": snapshot["input_hash"], "output_hash": snapshot["output_hash"],
                }, required=True)
            return {"recorded": True, "evidence_id": snapshot["evidence_id"]}
        except Exception as exc:
            self.warning = f"Could not record evidence snapshot: {exc}"
            return {"recorded": False, "warning": self.warning}

    def create_prospective_trial(self, trial: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            return {"created": False, "warning": self.warning}
        try:
            with self.transaction(durable=True) as conn:
                open_row = conn.execute(
                    "SELECT trial_id FROM prospective_trials WHERE target_kind = ? AND target_id = ? "
                    "AND status = 'open' LIMIT 1",
                    (trial["target_kind"], trial["target_id"]),
                ).fetchone()
                if open_row:
                    return {
                        "created": False,
                        "status_code": 409,
                        "warning": f"Open trial {open_row['trial_id']} already exists for this target.",
                    }
                conn.execute(
                    """
                    INSERT INTO prospective_trials(
                      trial_id, created_at, actor, target_kind, target_id,
                      model_version, target_snapshot_hash, status, starts_on,
                      closed_at, supersedes_trial_id, protocol_json, protocol_hash,
                      registration_snapshot_json, registration_snapshot_hash, close_note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, '', ?, ?, ?, ?, ?, '')
                    """,
                    (
                        trial["trial_id"], trial["created_at"], trial["actor"],
                        trial["target_kind"], trial["target_id"], int(trial.get("model_version") or 0),
                        trial["target_snapshot_hash"], trial["starts_on"],
                        trial.get("supersedes_trial_id") or "",
                        _evidence_json_dumps(trial["protocol"]), trial["protocol_hash"],
                        _evidence_json_dumps(trial["registration_snapshot"]),
                        trial["registration_snapshot_hash"],
                    ),
                )
                row = conn.execute(
                    "SELECT * FROM prospective_trials WHERE trial_id = ?", (trial["trial_id"],)
                ).fetchone()
                self.audit_append("trial.register", {
                    "trial_id": trial["trial_id"], "target_kind": trial["target_kind"],
                    "target_id": trial["target_id"], "protocol_hash": trial["protocol_hash"],
                }, actor=trial["actor"], required=True)
            return {"created": True, "trial": _prospective_trial_row(row) if row else None}
        except Exception as exc:
            self.warning = f"Could not register prospective trial: {exc}"
            return {"created": False, "warning": self.warning}

    def prospective_trials(self, limit: int = 200, target_kind: str = "", target_id: str = "") -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            query = "SELECT * FROM prospective_trials"
            params: list[Any] = []
            clauses = []
            if target_kind:
                clauses.append("target_kind = ?")
                params.append(target_kind)
            if target_id:
                clauses.append("target_id = ?")
                params.append(target_id)
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY created_at DESC, trial_id DESC LIMIT ?"
            params.append(max(1, min(int(limit), 2000)))
            with self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
                return [_prospective_trial_row(row) for row in rows]
        except Exception:
            return []

    def prospective_trial(self, trial_id: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM prospective_trials WHERE trial_id = ?", (str(trial_id)[:64],)
                ).fetchone()
                return _prospective_trial_row(row) if row else None
        except Exception:
            return None

    def close_prospective_trial(self, trial_id: str, status: str, note: str, actor: str) -> dict[str, Any]:
        if not self.available:
            return {"closed": False, "warning": self.warning}
        try:
            with self.transaction(durable=True) as conn:
                row = conn.execute(
                    "SELECT * FROM prospective_trials WHERE trial_id = ?", (str(trial_id)[:64],)
                ).fetchone()
                if not row:
                    return {"closed": False, "status_code": 404, "warning": "Unknown trial."}
                if row["status"] != "open":
                    return {"closed": False, "status_code": 409, "warning": "Trial is already closed."}
                conn.execute(
                    "UPDATE prospective_trials SET status = ?, closed_at = ?, close_note = ? WHERE trial_id = ?",
                    (status, utc_now(), redact_long_text(note, limit=1000), str(trial_id)[:64]),
                )
                updated = conn.execute(
                    "SELECT * FROM prospective_trials WHERE trial_id = ?", (str(trial_id)[:64],)
                ).fetchone()
                self.audit_append("trial.close", {
                    "trial_id": trial_id, "status": status, "note_hash": _sha256_text(note),
                }, actor=actor, required=True)
            return {"closed": True, "trial": _prospective_trial_row(updated)}
        except Exception as exc:
            return {"closed": False, "warning": f"Could not close trial: {exc}"}

    def get_evidence_snapshot(self, evidence_id: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM evidence_snapshots WHERE evidence_id = ?", (str(evidence_id)[:64],)
                ).fetchone()
                if not row:
                    return None
                return {
                    "evidence_id": row["evidence_id"], "created_at": row["created_at"],
                    "artifact_kind": row["artifact_kind"], "target_kind": row["target_kind"],
                    "target_id": row["target_id"], "calculation_version": row["calculation_version"],
                    "input_hash": row["input_hash"], "output_hash": row["output_hash"],
                    "envelope_hash": row["envelope_hash"],
                    "manifest": self._load_json(row["manifest_json"]),
                    "input": self._load_json(row["input_json"]),
                    "output": self._load_json(row["output_json"]),
                }
        except Exception:
            return None

    def evidence_snapshots(
        self,
        *,
        artifact_kind: str = "",
        target_kind: str = "",
        target_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not self.available:
            return []
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("artifact_kind", artifact_kind),
            ("target_kind", target_kind),
            ("target_id", target_id),
        ):
            if value:
                clauses.append(f"{column} = ?")
                params.append(str(value))
        query = "SELECT evidence_id FROM evidence_snapshots"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, evidence_id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 2000)))
        try:
            with self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
            return [
                snapshot for row in rows
                for snapshot in [self.get_evidence_snapshot(row["evidence_id"])]
                if snapshot is not None
            ]
        except Exception:
            return []

    def account_snapshots(self, account_id: str) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM account_snapshots WHERE account_id = ? ORDER BY as_of ASC",
                    (account_id,),
                ).fetchall()
                return [{
                    "account_id": row["account_id"], "as_of": row["as_of"],
                    "cash": row["cash"], "total_value": row["total_value"],
                    "source": row["source"], "created_at": row["created_at"],
                    "metadata": self._load_json(row["metadata_json"]),
                } for row in rows]
        except Exception:
            return []

    def account_positions(self, account_id: str, as_of: str | None = None) -> list[dict[str, Any]]:
        """Positions for one snapshot date; latest snapshot when as_of is None."""
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                target = as_of
                if target is None:
                    row = conn.execute(
                        "SELECT MAX(as_of) AS latest FROM account_positions WHERE account_id = ?",
                        (account_id,),
                    ).fetchone()
                    target = row["latest"] if row else None
                if not target:
                    return []
                rows = conn.execute(
                    "SELECT * FROM account_positions WHERE account_id = ? AND as_of = ? "
                    "ORDER BY market_value DESC",
                    (account_id, str(target)[:10]),
                ).fetchall()
                return [{
                    "account_id": row["account_id"], "as_of": row["as_of"],
                    "ticker": row["ticker"], "shares": row["shares"],
                    "price": row["price"], "market_value": row["market_value"],
                    "cost_basis": row["cost_basis"],
                } for row in rows]
        except Exception:
            return []

    # ------------------------------------------------------------------ #
    # v10 data-architecture spine (all best-effort: never block a fetch)
    # ------------------------------------------------------------------ #
    def record_security(self, symbol: str, *, name: str = "", figi: str = "",
                        exchange: str = "", security_type: str = "",
                        price_provider: str = "") -> None:
        """Upsert identity facts, keeping any already-known non-empty field."""
        if not self.available:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO security_master(symbol, name, figi, exchange, security_type,
                                                price_provider, first_seen, last_verified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                      name = CASE WHEN excluded.name != '' THEN excluded.name ELSE name END,
                      figi = CASE WHEN excluded.figi != '' THEN excluded.figi ELSE figi END,
                      exchange = CASE WHEN excluded.exchange != '' THEN excluded.exchange ELSE exchange END,
                      security_type = CASE WHEN excluded.security_type != '' THEN excluded.security_type ELSE security_type END,
                      price_provider = CASE WHEN excluded.price_provider != '' THEN excluded.price_provider ELSE price_provider END,
                      last_verified = excluded.last_verified
                    """,
                    (str(symbol).upper()[:16], redact_secrets(str(name))[:120],
                     str(figi)[:16], str(exchange)[:24], str(security_type)[:24],
                     str(price_provider)[:32], utc_now(), utc_now()),
                )
        except Exception:
            pass

    def securities(self) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute("SELECT * FROM security_master ORDER BY symbol").fetchall()
                return [dict(row) for row in rows]
        except Exception:
            return []

    def vault_payload(self, provider: str, endpoint: str, symbol: str,
                      payload_text: str) -> bool:
        """Raw vendor response, hash-deduped: identical payloads store once."""
        if not self.available or not payload_text:
            return False
        import hashlib
        # Store the FULL payload via the long-text path (_store_text routes
        # through redact_secrets, which caps input at 800 chars — the exact
        # trap that once truncated decision rationales). Hash the text as
        # actually stored so payload_hash always verifies the row.
        stored = redact_long_text(str(payload_text), limit=400_000)
        digest = hashlib.sha256(stored.encode("utf-8", errors="replace")).hexdigest()
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO vendor_vault"
                    "(provider, endpoint, symbol, retrieved_at, payload_hash, payload_json) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (str(provider)[:32], str(endpoint)[:120], str(symbol).upper()[:16],
                     utc_now(), digest, self._store_long_text(stored, limit=400_000)),
                )
                return bool(cur.rowcount)
        except Exception:
            return False

    def vault_entries(self, symbol: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                if symbol:
                    rows = conn.execute(
                        "SELECT id, provider, endpoint, symbol, retrieved_at, payload_hash "
                        "FROM vendor_vault WHERE symbol = ? ORDER BY id DESC LIMIT ?",
                        (str(symbol).upper(), max(1, min(int(limit), 2000))),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, provider, endpoint, symbol, retrieved_at, payload_hash "
                        "FROM vendor_vault ORDER BY id DESC LIMIT ?",
                        (max(1, min(int(limit), 2000)),),
                    ).fetchall()
                return [dict(row) for row in rows]
        except Exception:
            return []

    def vault_payload_text(self, vault_id: int) -> str:
        if not self.available:
            return ""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT payload_json FROM vendor_vault WHERE id = ?", (int(vault_id),)
                ).fetchone()
                return self._load_text(row["payload_json"]) if row else ""
        except Exception:
            return ""

    def record_price_revisions(self, symbol: str, revisions: list[dict[str, Any]],
                               provider: str = "") -> int:
        """Silent vendor restatements of already-stored bars — evidence, kept."""
        if not self.available or not revisions:
            return 0
        recorded = 0
        try:
            with self._connect() as conn:
                for rev in revisions[:500]:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO price_revisions"
                        "(symbol, bar_date, old_close, new_close, change_pct, provider, observed_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (str(symbol).upper()[:16], str(rev["bar_date"])[:10],
                         float(rev["old_close"]), float(rev["new_close"]),
                         float(rev["change_pct"]), str(provider)[:32], utc_now()),
                    )
                    recorded += int(bool(cur.rowcount))
        except Exception:
            return recorded
        return recorded

    def price_revisions(self, symbol: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                if symbol:
                    rows = conn.execute(
                        "SELECT * FROM price_revisions WHERE symbol = ? "
                        "ORDER BY observed_at DESC LIMIT ?",
                        (str(symbol).upper(), max(1, min(int(limit), 2000))),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM price_revisions ORDER BY observed_at DESC LIMIT ?",
                        (max(1, min(int(limit), 2000)),),
                    ).fetchall()
                return [dict(row) for row in rows]
        except Exception:
            return []

    def record_corporate_actions(self, symbol: str, actions: list[dict[str, Any]]) -> int:
        if not self.available or not actions:
            return 0
        recorded = 0
        try:
            with self._connect() as conn:
                for action in actions[:500]:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO corporate_actions"
                        "(symbol, action_type, ex_date, value, source, retrieved_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (str(symbol).upper()[:16], str(action["action_type"])[:24],
                         str(action["ex_date"])[:10], float(action["value"]),
                         str(action.get("source") or "")[:32], utc_now()),
                    )
                    recorded += int(bool(cur.rowcount))
        except Exception:
            return recorded
        return recorded

    def corporate_actions(self, symbol: str, limit: int = 400) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM corporate_actions WHERE symbol = ? "
                    "ORDER BY ex_date DESC LIMIT ?",
                    (str(symbol).upper(), max(1, min(int(limit), 2000))),
                ).fetchall()
                return [dict(row) for row in rows]
        except Exception:
            return []

    def corporate_actions_as_of(self, symbol: str, as_of: str, limit: int = 400) -> list[dict[str, Any]]:
        """Return only actions both retrieved and effective by ``as_of``."""
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT * FROM corporate_actions
                    WHERE symbol = ? AND ex_date <= ? AND substr(retrieved_at, 1, 10) <= ?
                    ORDER BY ex_date ASC, action_type ASC
                    LIMIT ?
                    """,
                    (
                        str(symbol).upper()[:16], str(as_of)[:10], str(as_of)[:10],
                        max(1, min(int(limit), 5000)),
                    ),
                ).fetchall()
                return [dict(row) for row in rows]
        except Exception:
            return []

    def audit_append(self, action: str, payload: dict[str, Any] | str,
                     actor: str = "operator", *, required: bool = False) -> bool:
        """Append one tamper-evident link: chain_hash covers the previous link,
        so any retroactive edit breaks every later hash."""
        if not self.available:
            if required:
                raise RuntimeError(self.warning or "Durable audit persistence is unavailable.")
            return False
        import hashlib
        text = payload if isinstance(payload, str) else _json_dumps_stable(payload)
        payload_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        try:
            # Head-read + insert must be atomic or concurrent waitress threads
            # fork the chain and a benign race reads as tampering forever.
            # The lock serializes in-process writers; BEGIN IMMEDIATE takes the
            # SQLite write lock for cross-process ones.
            # Match the privileged-event lock order: acquire the store before
            # the chain lock so an active durable request transaction cannot
            # deadlock with a second writer waiting for the encrypted store.
            with self._connect(durable=required) as conn:
                with _AUDIT_APPEND_LOCK:
                    try:
                        conn.execute("BEGIN IMMEDIATE")
                    except Exception:
                        pass  # already inside a transaction: the lock still guards us
                    row = conn.execute(
                        "SELECT chain_hash FROM audit_chain ORDER BY seq DESC LIMIT 1").fetchone()
                    prev_hash = row["chain_hash"] if row else "genesis"
                    ts = utc_now()
                    safe_actor = redact_secrets(str(actor))[:64]
                    safe_action = str(action)[:64]
                    chain_version = 2
                    chain_hash = hashlib.sha256(
                        f"{prev_hash}|{payload_hash}|{ts}|{safe_actor}|{safe_action}".encode()
                    ).hexdigest()
                    conn.execute(
                        "INSERT INTO audit_chain(ts, actor, action, payload_hash, prev_hash, chain_hash, chain_version) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (ts, safe_actor, safe_action, payload_hash, prev_hash, chain_hash, chain_version),
                    )
            return True
        except Exception as exc:
            if required:
                raise RuntimeError(f"Could not append required audit event '{action}': {exc}") from exc
            return False

    def audit_verify(self, limit: int = 100000) -> dict[str, Any]:
        """Walk the chain re-deriving every hash; any break is named by seq."""
        if not self.available:
            return {"status": "unavailable", "warning": self.warning}
        import hashlib
        try:
            with self._connect() as conn:
                total = int(conn.execute("SELECT COUNT(*) FROM audit_chain").fetchone()[0])
                rows = conn.execute(
                    "SELECT * FROM audit_chain ORDER BY seq ASC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
            prev = "genesis"
            for row in rows:
                if int(row["chain_version"] or 1) >= 2:
                    source = f"{prev}|{row['payload_hash']}|{row['ts']}|{row['actor']}|{row['action']}"
                else:
                    source = f"{prev}|{row['payload_hash']}|{row['ts']}|{row['action']}"
                expected = hashlib.sha256(source.encode()).hexdigest()
                if row["prev_hash"] != prev or row["chain_hash"] != expected:
                    return {"status": "broken", "first_bad_seq": int(row["seq"]),
                            "entries_checked": len(rows)}
                prev = row["chain_hash"]
            return {"status": "partial" if len(rows) < total else "intact", "entries": len(rows),
                    "total_entries": total,
                    "head_hash": prev if rows else "genesis"}
        except Exception as exc:
            return {"status": "error", "warning": str(exc)}

    def upsert_ledger_account(self, account_id: str, display_name: str = "",
                              model_id: str = "") -> None:
        if not self.available:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO ledger_accounts(account_id, display_name, model_id, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(account_id) DO UPDATE SET
                      display_name = CASE WHEN excluded.display_name != ''
                                          THEN excluded.display_name
                                          ELSE ledger_accounts.display_name END,
                      model_id = CASE WHEN excluded.model_id != ''
                                      THEN excluded.model_id
                                      ELSE ledger_accounts.model_id END
                    """,
                    (redact_secrets(str(account_id))[:64],
                     self._store_text(str(display_name)[:80]),
                     str(model_id)[:64], utc_now()),
                )
        except Exception as exc:
            self.warning = f"Could not upsert ledger account: {exc}"

    def ledger_accounts(self) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM ledger_accounts ORDER BY account_id ASC").fetchall()
                return [{
                    "account_id": row["account_id"],
                    "display_name": self._load_text(row["display_name"]),
                    "model_id": row["model_id"],
                    "created_at": row["created_at"],
                } for row in rows]
        except Exception:
            return []

    def delete_ledger_account(self, account_id: str) -> dict[str, Any]:
        """Remove an account and ALL its ledger rows (undo for a bad import)."""
        if not self.available:
            return {"deleted": False, "warning": self.warning}
        try:
            with self._connect() as conn:
                fills = conn.execute("DELETE FROM trade_fills WHERE account_id = ?", (account_id,)).rowcount
                snaps = conn.execute("DELETE FROM account_snapshots WHERE account_id = ?", (account_id,)).rowcount
                conn.execute("DELETE FROM account_positions WHERE account_id = ?", (account_id,))
                revision_ids = [row["snapshot_id"] for row in conn.execute(
                    "SELECT snapshot_id FROM account_snapshot_revisions WHERE account_id = ?",
                    (account_id,),
                ).fetchall()]
                for snapshot_id in revision_ids:
                    conn.execute("DELETE FROM account_position_revisions WHERE snapshot_id = ?", (snapshot_id,))
                conn.execute("DELETE FROM account_snapshot_revisions WHERE account_id = ?", (account_id,))
                conn.execute("DELETE FROM ledger_accounts WHERE account_id = ?", (account_id,))
            return {"deleted": True, "fills_removed": fills, "snapshots_removed": snaps}
        except Exception as exc:
            self.warning = f"Could not delete ledger account: {exc}"
            return {"deleted": False, "warning": str(exc)}

    # ------------------------------------------------------------------ #
    # Point-in-time fundamentals: what Helios KNEW on each date
    # ------------------------------------------------------------------ #
    def record_fundamentals_snapshot(self, snap: dict[str, Any]) -> None:
        """First-of-day is IMMUTABLE (what was knowable at the operator's
        first observation); last-of-day stays current. The old single-row
        REPLACE silently destroyed the morning values on earnings days
        (review finding). Two rows/symbol/day keeps the flush cheap."""
        if not self.available:
            return
        values = (
            str(snap["symbol"]).upper()[:16],
            str(snap["as_of"])[:10],
            utc_now(),
            self._store_json(snap.get("reconciliation_warnings") or []),
            snap.get("dividend_yield"), snap.get("forward_pe"),
            snap.get("trailing_pe"), snap.get("earnings_growth"),
            str(snap.get("growth_basis") or "")[:40],
            str(snap.get("sector") or "")[:60],
            str(snap.get("source") or "")[:60],
            snap.get("roe"), snap.get("profit_margin"),
            snap.get("debt_to_equity"), snap.get("revenue_growth"),
            snap.get("current_price"), snap.get("target_mean_price"),
            snap.get("analyst_rating"), snap.get("n_analysts"),
            str(snap.get("next_earnings_date") or "")[:10],
        )
        sql = """
            INSERT OR {mode} INTO fundamentals_snapshots(
              symbol, as_of, slot, retrieved_at, recon_warnings,
              dividend_yield, forward_pe, trailing_pe, earnings_growth, growth_basis,
              sector, source, roe, profit_margin, debt_to_equity, revenue_growth,
              current_price, target_mean_price, analyst_rating, n_analysts, next_earnings_date
            ) VALUES (?, ?, '{slot}', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        try:
            with self._connect() as conn:
                conn.execute(sql.format(mode="IGNORE", slot="first"), values)
                conn.execute(sql.format(mode="REPLACE", slot="last"), values)
        except Exception:
            pass  # PIT capture is best-effort; a write failure never blocks a fetch

    def fundamentals_history(self, symbol: str, limit: int = 400) -> list[dict[str, Any]]:
        """Both slots per day, oldest first. Day-granularity consumers should
        prefer slot='first' (what was knowable at first observation) and fall
        back to 'last' for pre-v9 rows; first-vs-last diff exposes intraday
        revision (earnings-day flips) for free."""
        if not self.available:
            return []
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM fundamentals_snapshots WHERE symbol = ? "
                    "ORDER BY as_of ASC, slot ASC LIMIT ?",
                    (str(symbol).upper(), max(1, min(int(limit), 5000))),
                ).fetchall()
                return [dict(row) for row in rows]
        except Exception:
            return []

    def fundamentals_as_of(self, symbol: str, as_of: str) -> dict[str, Any] | None:
        """Latest first-observed fundamentals known on or before ``as_of``."""
        if not self.available:
            return None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM fundamentals_snapshots
                    WHERE symbol = ? AND as_of <= ?
                    ORDER BY as_of DESC,
                      CASE slot WHEN 'first' THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    (str(symbol).upper()[:16], str(as_of)[:10]),
                ).fetchone()
                return dict(row) if row else None
        except Exception:
            return None

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
                    ORDER BY created_at DESC, rowid DESC
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


def _migrate_fundamentals_snapshots_v9(conn: sqlite3.Connection) -> None:
    """One-shot in-place migration: v8 (symbol, as_of) -> v9 (+ slot).

    Existing rows were last-write-of-day, so they migrate as slot='last' with
    empty retrieved_at. The copy happens inside the caller's transaction —
    a failure rolls back and never drops accumulated PIT evidence."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(fundamentals_snapshots)").fetchall()]
    if not cols or "slot" in cols:
        return  # table absent (fresh DB) or already migrated
    conn.execute("ALTER TABLE fundamentals_snapshots RENAME TO fundamentals_snapshots_v8")
    conn.execute(
        """
        CREATE TABLE fundamentals_snapshots (
          symbol TEXT NOT NULL,
          as_of TEXT NOT NULL,
          slot TEXT NOT NULL DEFAULT 'last',
          retrieved_at TEXT NOT NULL DEFAULT '',
          recon_warnings TEXT NOT NULL DEFAULT '',
          dividend_yield REAL,
          forward_pe REAL,
          trailing_pe REAL,
          earnings_growth REAL,
          growth_basis TEXT NOT NULL DEFAULT '',
          sector TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT '',
          roe REAL,
          profit_margin REAL,
          debt_to_equity REAL,
          revenue_growth REAL,
          current_price REAL,
          target_mean_price REAL,
          analyst_rating REAL,
          n_analysts INTEGER,
          next_earnings_date TEXT NOT NULL DEFAULT '',
          PRIMARY KEY (symbol, as_of, slot)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO fundamentals_snapshots(
          symbol, as_of, slot, retrieved_at, recon_warnings,
          dividend_yield, forward_pe, trailing_pe, earnings_growth, growth_basis,
          sector, source, roe, profit_margin, debt_to_equity, revenue_growth,
          current_price, target_mean_price, analyst_rating, n_analysts, next_earnings_date
        )
        SELECT symbol, as_of, 'last', '', '',
               dividend_yield, forward_pe, trailing_pe, earnings_growth, growth_basis,
               sector, source, roe, profit_margin, debt_to_equity, revenue_growth,
               current_price, target_mean_price, analyst_rating, n_analysts, next_earnings_date
        FROM fundamentals_snapshots_v8
        """
    )
    conn.execute("DROP TABLE fundamentals_snapshots_v8")


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
        CREATE TABLE IF NOT EXISTS decision_journal (
          decision_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          target_kind TEXT NOT NULL,
          target_id TEXT NOT NULL,
          target_name TEXT NOT NULL DEFAULT '',
          mandate TEXT NOT NULL DEFAULT '',
          benchmark TEXT NOT NULL DEFAULT '',
          engine_action TEXT NOT NULL DEFAULT '',
          engine_score REAL,
          tactical_action TEXT NOT NULL DEFAULT '',
          strategic_action TEXT NOT NULL DEFAULT '',
          my_action TEXT NOT NULL,
          agreement TEXT NOT NULL DEFAULT '',
          rationale TEXT NOT NULL DEFAULT '',
          decision_date TEXT NOT NULL DEFAULT '',
          decision_price REAL,
          data_mode TEXT NOT NULL DEFAULT '',
          context_json TEXT NOT NULL DEFAULT '{}',
          outcome_status TEXT NOT NULL DEFAULT 'pending',
          outcomes_json TEXT NOT NULL DEFAULT '{}',
          evaluated_at TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS macro_history (
          reading_date TEXT PRIMARY KEY,
          recorded_at TEXT NOT NULL,
          fed_stance REAL,
          fed_n_documents INTEGER,
          gpr_index REAL,
          fomc_days_until INTEGER,
          policy_themes_json TEXT NOT NULL DEFAULT '{}'
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
        """
        CREATE TABLE IF NOT EXISTS data_quality_alerts (
          alert_id TEXT PRIMARY KEY,
          category TEXT NOT NULL,
          severity TEXT NOT NULL,
          target TEXT NOT NULL,
          detail TEXT NOT NULL,
          next_step TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL,
          last_changed_at TEXT NOT NULL,
          resolved_at TEXT NOT NULL DEFAULT '',
          occurrence_count INTEGER NOT NULL DEFAULT 1,
          last_fingerprint TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    # --- Ledger (minimum-viable accounting: fills, snapshots, positions) --- #
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ledger_accounts (
          account_id TEXT PRIMARY KEY,
          display_name TEXT NOT NULL DEFAULT '',
          model_id TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_fills (
          fill_id TEXT PRIMARY KEY,
          account_id TEXT NOT NULL,
          trade_date TEXT NOT NULL,
          settle_date TEXT NOT NULL DEFAULT '',
          ticker TEXT NOT NULL DEFAULT '',
          side TEXT NOT NULL,
          shares REAL NOT NULL DEFAULT 0,
          price REAL NOT NULL DEFAULT 0,
          fees REAL NOT NULL DEFAULT 0,
          amount REAL,
          source_file TEXT NOT NULL DEFAULT '',
          decision_id TEXT NOT NULL DEFAULT '',
          dedupe_key TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_snapshots (
          account_id TEXT NOT NULL,
          as_of TEXT NOT NULL,
          cash REAL,
          total_value REAL NOT NULL,
          source TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          PRIMARY KEY (account_id, as_of)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_positions (
          account_id TEXT NOT NULL,
          as_of TEXT NOT NULL,
          ticker TEXT NOT NULL,
          shares REAL NOT NULL DEFAULT 0,
          price REAL,
          market_value REAL,
          cost_basis REAL,
          PRIMARY KEY (account_id, as_of, ticker)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_snapshot_revisions (
          snapshot_id TEXT PRIMARY KEY,
          account_id TEXT NOT NULL,
          as_of TEXT NOT NULL,
          revision INTEGER NOT NULL,
          supersedes_snapshot_id TEXT NOT NULL DEFAULT '',
          correction_reason TEXT NOT NULL DEFAULT '',
          cash REAL,
          total_value REAL NOT NULL,
          source TEXT NOT NULL DEFAULT '',
          content_hash TEXT NOT NULL,
          created_at TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          UNIQUE(account_id, as_of, revision)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_position_revisions (
          snapshot_id TEXT NOT NULL,
          ticker TEXT NOT NULL,
          shares REAL NOT NULL DEFAULT 0,
          price REAL,
          market_value REAL,
          cost_basis REAL,
          PRIMARY KEY(snapshot_id, ticker),
          FOREIGN KEY(snapshot_id) REFERENCES account_snapshot_revisions(snapshot_id) ON DELETE CASCADE
        )
        """
    )
    # Preserve every pre-v11 current snapshot as revision 1. SQLite supplies a
    # unique immutable ID; content_hash is explicitly marked legacy because
    # earlier schemas did not retain a canonical hash.
    conn.execute(
        """
        INSERT OR IGNORE INTO account_snapshot_revisions(
          snapshot_id, account_id, as_of, revision, supersedes_snapshot_id,
          correction_reason, cash, total_value, source, content_hash,
          created_at, metadata_json
        )
        SELECT 'legacy-' || lower(hex(randomblob(12))), account_id, as_of, 1, '',
               'Migrated from pre-v11 current snapshot.', cash, total_value,
               source, 'legacy-unhashed', created_at, metadata_json
        FROM account_snapshots
        WHERE NOT EXISTS (
          SELECT 1 FROM account_snapshot_revisions r
          WHERE r.account_id = account_snapshots.account_id
            AND r.as_of = account_snapshots.as_of
        )
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO account_position_revisions(
          snapshot_id, ticker, shares, price, market_value, cost_basis
        )
        SELECT r.snapshot_id, p.ticker, p.shares, p.price, p.market_value, p.cost_basis
        FROM account_positions p
        JOIN account_snapshot_revisions r
          ON r.account_id = p.account_id AND r.as_of = p.as_of AND r.revision = 1
        WHERE r.content_hash = 'legacy-unhashed'
          AND NOT EXISTS (
            SELECT 1 FROM account_position_revisions existing
            WHERE existing.snapshot_id = r.snapshot_id
          )
        """
    )
    # --- Point-in-time fundamentals: what Helios KNEW on each date --- #
    # v9: (symbol, as_of, slot) with slot in ('first','last') — the old
    # single-row-per-day INSERT OR REPLACE destroyed the morning observation
    # on earnings days, exactly when intraday revision matters (review
    # finding). The migration COPIES existing rows (they are evidence).
    _migrate_fundamentals_snapshots_v9(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamentals_snapshots (
          symbol TEXT NOT NULL,
          as_of TEXT NOT NULL,
          slot TEXT NOT NULL DEFAULT 'last',
          retrieved_at TEXT NOT NULL DEFAULT '',
          recon_warnings TEXT NOT NULL DEFAULT '',
          dividend_yield REAL,
          forward_pe REAL,
          trailing_pe REAL,
          earnings_growth REAL,
          growth_basis TEXT NOT NULL DEFAULT '',
          sector TEXT NOT NULL DEFAULT '',
          source TEXT NOT NULL DEFAULT '',
          roe REAL,
          profit_margin REAL,
          debt_to_equity REAL,
          revenue_growth REAL,
          current_price REAL,
          target_mean_price REAL,
          analyst_rating REAL,
          n_analysts INTEGER,
          next_earnings_date TEXT NOT NULL DEFAULT '',
          PRIMARY KEY (symbol, as_of, slot)
        )
        """
    )
    # --- v10: data-architecture spine (review: reproducible-as-of research) --- #
    # Security identity, raw vendor payload vault (hash-deduped), silent price
    # restatement ledger, corporate actions, and a hash-chained journal audit.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS security_master (
          symbol TEXT PRIMARY KEY,
          name TEXT NOT NULL DEFAULT '',
          figi TEXT NOT NULL DEFAULT '',
          exchange TEXT NOT NULL DEFAULT '',
          security_type TEXT NOT NULL DEFAULT '',
          price_provider TEXT NOT NULL DEFAULT '',
          first_seen TEXT NOT NULL DEFAULT '',
          last_verified TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS vendor_vault (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          provider TEXT NOT NULL,
          endpoint TEXT NOT NULL DEFAULT '',
          symbol TEXT NOT NULL DEFAULT '',
          retrieved_at TEXT NOT NULL,
          payload_hash TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          UNIQUE (provider, symbol, payload_hash)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS price_revisions (
          symbol TEXT NOT NULL,
          bar_date TEXT NOT NULL,
          old_close REAL NOT NULL,
          new_close REAL NOT NULL,
          change_pct REAL NOT NULL,
          provider TEXT NOT NULL DEFAULT '',
          observed_at TEXT NOT NULL,
          PRIMARY KEY (symbol, bar_date, observed_at)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS corporate_actions (
          symbol TEXT NOT NULL,
          action_type TEXT NOT NULL,
          ex_date TEXT NOT NULL,
          value REAL NOT NULL,
          source TEXT NOT NULL DEFAULT '',
          retrieved_at TEXT NOT NULL DEFAULT '',
          PRIMARY KEY (symbol, action_type, ex_date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_chain (
          seq INTEGER PRIMARY KEY AUTOINCREMENT,
          ts TEXT NOT NULL,
          actor TEXT NOT NULL DEFAULT 'operator',
          action TEXT NOT NULL,
          payload_hash TEXT NOT NULL,
          prev_hash TEXT NOT NULL,
          chain_hash TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS evidence_snapshots (
          evidence_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          artifact_kind TEXT NOT NULL,
          target_kind TEXT NOT NULL DEFAULT '',
          target_id TEXT NOT NULL DEFAULT '',
          calculation_version TEXT NOT NULL,
          input_hash TEXT NOT NULL,
          output_hash TEXT NOT NULL,
          envelope_hash TEXT NOT NULL DEFAULT '',
          manifest_json TEXT NOT NULL DEFAULT '{}',
          input_json TEXT NOT NULL DEFAULT '{}',
          output_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prospective_trials (
          trial_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          actor TEXT NOT NULL,
          target_kind TEXT NOT NULL,
          target_id TEXT NOT NULL,
          model_version INTEGER NOT NULL DEFAULT 0,
          target_snapshot_hash TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'open',
          starts_on TEXT NOT NULL,
          closed_at TEXT NOT NULL DEFAULT '',
          supersedes_trial_id TEXT NOT NULL DEFAULT '',
          protocol_json TEXT NOT NULL,
          protocol_hash TEXT NOT NULL UNIQUE,
          registration_snapshot_json TEXT NOT NULL,
          registration_snapshot_hash TEXT NOT NULL,
          close_note TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_reconciliations (
          reconciliation_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          actor TEXT NOT NULL,
          data_domain TEXT NOT NULL,
          primary_provider TEXT NOT NULL,
          backup_provider TEXT NOT NULL,
          symbol_count INTEGER NOT NULL DEFAULT 0,
          compared_count INTEGER NOT NULL DEFAULT 0,
          mismatch_count INTEGER NOT NULL DEFAULT 0,
          max_abs_difference_pct REAL,
          status TEXT NOT NULL,
          evidence_json TEXT NOT NULL DEFAULT '{}',
          note TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_cutovers (
          cutover_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          actor TEXT NOT NULL,
          data_domain TEXT NOT NULL,
          primary_provider TEXT NOT NULL,
          backup_provider TEXT NOT NULL,
          reconciliation_id TEXT NOT NULL,
          status TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS privileged_events (
          event_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          actor TEXT NOT NULL,
          actor_roles TEXT NOT NULL DEFAULT '',
          action TEXT NOT NULL,
          resource TEXT NOT NULL DEFAULT '',
          outcome TEXT NOT NULL,
          source_ip_hash TEXT NOT NULL DEFAULT '',
          details_json TEXT NOT NULL DEFAULT '{}',
          previous_hash TEXT NOT NULL,
          event_hash TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operational_incidents (
          incident_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          category TEXT NOT NULL,
          severity TEXT NOT NULL,
          status TEXT NOT NULL,
          owner TEXT NOT NULL,
          title TEXT NOT NULL,
          source_key TEXT NOT NULL UNIQUE,
          detail TEXT NOT NULL DEFAULT '',
          acknowledged_at TEXT NOT NULL DEFAULT '',
          resolved_at TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS incident_events (
          event_id TEXT PRIMARY KEY,
          incident_id TEXT NOT NULL,
          created_at TEXT NOT NULL,
          actor TEXT NOT NULL,
          action TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          FOREIGN KEY(incident_id) REFERENCES operational_incidents(incident_id) ON DELETE CASCADE
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS independent_validation_reviews (
          review_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          model_id TEXT NOT NULL,
          model_version INTEGER NOT NULL,
          sponsor TEXT NOT NULL,
          validator TEXT NOT NULL,
          outcome TEXT NOT NULL,
          reviewed_at TEXT NOT NULL,
          next_review_due TEXT NOT NULL,
          controls_json TEXT NOT NULL DEFAULT '{}',
          findings_json TEXT NOT NULL DEFAULT '{"items":[]}',
          note TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS model_validation_exceptions (
          exception_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          model_id TEXT NOT NULL,
          model_version INTEGER NOT NULL,
          control_key TEXT NOT NULL,
          owner TEXT NOT NULL,
          approver TEXT NOT NULL,
          reason TEXT NOT NULL,
          compensating_controls_json TEXT NOT NULL DEFAULT '{"items":[]}',
          expires_at TEXT NOT NULL,
          status TEXT NOT NULL,
          resolved_at TEXT NOT NULL DEFAULT '',
          resolution_note TEXT NOT NULL DEFAULT ''
        )
        """
    )
    _ensure_column(conn, "signal_journal", "trial_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "signal_journal", "recording_source", "TEXT NOT NULL DEFAULT 'exploratory'")
    _ensure_column(conn, "decision_journal", "trial_id", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "evidence_snapshots", "envelope_hash", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "audit_chain", "chain_version", "INTEGER NOT NULL DEFAULT 1")
    _backfill_evidence_envelope_hashes(conn)
    conn.execute(
        "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, utc_now()),
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_price_history_symbol_date ON price_history(symbol, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_refresh_log_symbol_time ON refresh_log(symbol, attempted_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_journal_target ON signal_journal(target_kind, target_id, input_end_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_journal_target ON decision_journal(target_kind, target_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_report_snapshots_target ON report_snapshots(target_kind, target_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_governance_model ON model_governance_events(model_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_data_quality_alerts_status ON data_quality_alerts(status, category, severity)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_fills_account_date ON trade_fills(account_id, trade_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_fills_decision ON trade_fills(decision_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_fundamentals_snapshots_symbol ON fundamentals_snapshots(symbol, as_of)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_account_snapshot_revisions ON account_snapshot_revisions(account_id, as_of, revision)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_evidence_target ON evidence_snapshots(target_kind, target_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trials_target ON prospective_trials(target_kind, target_id, status, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_signal_trial ON signal_journal(trial_id, recording_source, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_decision_trial ON decision_journal(trial_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_reconciliation_domain ON provider_reconciliations(data_domain, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_cutover_domain ON provider_cutovers(data_domain, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_privileged_events_created ON privileged_events(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_incidents_status ON operational_incidents(status, severity, updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_incident_events_incident ON incident_events(incident_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_independent_reviews_model ON independent_validation_reviews(model_id, model_version, reviewed_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_validation_exceptions_model ON model_validation_exceptions(model_id, model_version, status, expires_at)")


def _preflight_schema_version(conn: sqlite3.Connection) -> None:
    """Reject newer databases before creating or altering any application table."""
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'"
    ).fetchone()
    if not has_table:
        return
    versions = [int(row[0]) for row in conn.execute("SELECT version FROM schema_version").fetchall()]
    if versions and max(versions) > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {max(versions)} is newer than this Helios build "
            f"supports ({SCHEMA_VERSION}) — open it with a matching or newer Helios version"
        )


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _backfill_evidence_envelope_hashes(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT * FROM evidence_snapshots WHERE envelope_hash = ''"
    ).fetchall()
    for row in rows:
        envelope = {
            "evidence_id": row["evidence_id"], "created_at": row["created_at"],
            "artifact_kind": row["artifact_kind"], "target_kind": row["target_kind"],
            "target_id": row["target_id"], "calculation_version": row["calculation_version"],
            "input_hash": row["input_hash"], "output_hash": row["output_hash"],
            "manifest": _json_loads(row["manifest_json"]),
        }
        conn.execute(
            "UPDATE evidence_snapshots SET envelope_hash = ? WHERE evidence_id = ?",
            (_sha256_text(_evidence_json_dumps(envelope)), row["evidence_id"]),
        )


def _check_schema_version(conn: sqlite3.Connection) -> None:
    """Fail closed on a database written by a newer Helios; stamp older ones.

    No migrations exist yet, so an older stamp is simply updated to the current
    version. A newer stamp means a newer build wrote this data — refusing to
    open it protects persisted research evidence from silent misreads.
    """
    rows = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    versions = [int(row["version"]) for row in rows]
    stored = max(versions) if versions else SCHEMA_VERSION
    if stored > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema version {stored} is newer than this Helios build "
            f"supports ({SCHEMA_VERSION}) — open it with a matching or newer Helios version"
        )
    if versions != [SCHEMA_VERSION]:
        conn.execute("DELETE FROM schema_version")
        conn.execute(
            "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, utc_now()),
        )


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
    # Non-finite closes (NaN/±inf) are not analyzable price facts.
    finite = out["close"].notna() & ~out["close"].isin([float("inf"), float("-inf")])
    return out[finite]


_SF_DATALESS = getattr(stat, "SF_DATALESS", 0x40000000)


def _is_dataless(candidate: Path) -> bool:
    """True for cloud-evicted placeholder files (macOS); reading one blocks on download."""
    try:
        flags = getattr(os.stat(candidate, follow_symlinks=False), "st_flags", 0)
    except OSError:
        return False
    return bool(flags & _SF_DATALESS)


def _plaintext_sqlite_artifacts(directory: Path, *, ignore: set[Path]) -> list[Path]:
    if not directory.is_dir():
        return []
    found: list[Path] = []
    for candidate in sorted(directory.rglob("*")):
        if not candidate.is_file() or candidate in ignore or _is_dataless(candidate):
            continue
        try:
            with candidate.open("rb") as handle:
                header = handle.read(len(SQLITE_HEADER))
        except OSError:
            continue
        if header == SQLITE_HEADER:
            found.append(candidate)
    return found


def _deserialize_sqlite_payload(conn: sqlite3.Connection, payload: bytes) -> None:
    conn.deserialize(payload)


def _validate_loaded_sqlite_payload(conn: sqlite3.Connection) -> None:
    conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone()


def _load_sqlite_payload_into_memory(conn: sqlite3.Connection, payload: bytes, temp_dir: Path) -> sqlite3.Connection:
    try:
        _deserialize_sqlite_payload(conn, payload)
        _validate_loaded_sqlite_payload(conn)
        return conn
    except sqlite3.OperationalError as exc:
        if "unable to open database file" not in str(exc).lower():
            raise
        try:
            conn.close()
        except Exception:
            pass
    temp_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".helios-decrypted-", suffix=".sqlite", dir=str(temp_dir))
    os.close(fd)
    tmp_path = Path(tmp_name)
    source: sqlite3.Connection | None = None
    loaded = sqlite3.connect(":memory:", timeout=5, check_same_thread=False)
    loaded.row_factory = sqlite3.Row
    loaded.execute("PRAGMA foreign_keys = ON")
    try:
        tmp_path.write_bytes(payload)
        try:
            tmp_path.chmod(0o600)
        except Exception:
            pass
        source = sqlite3.connect(str(tmp_path), timeout=5)
        source.backup(loaded)
        return loaded
    except Exception:
        loaded.close()
        raise
    finally:
        if source is not None:
            source.close()
        for candidate in (tmp_path, tmp_path.with_name(f"{tmp_path.name}-wal"), tmp_path.with_name(f"{tmp_path.name}-shm")):
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass


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


def _evidence_json_dumps(value: dict[str, Any]) -> str:
    """Canonical evidence JSON without the ordinary metadata list cap."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


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
        "trial_id": row["trial_id"] if "trial_id" in row.keys() else "",
        "recording_source": row["recording_source"] if "recording_source" in row.keys() else "exploratory",
        "metadata": object_json("metadata_json"),
    }


def _prospective_trial_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "trial_id": row["trial_id"],
        "created_at": row["created_at"],
        "actor": row["actor"],
        "target_kind": row["target_kind"],
        "target_id": row["target_id"],
        "model_version": int(row["model_version"] or 0),
        "target_snapshot_hash": row["target_snapshot_hash"],
        "status": row["status"],
        "starts_on": row["starts_on"],
        "closed_at": row["closed_at"] or None,
        "supersedes_trial_id": row["supersedes_trial_id"] or None,
        "protocol": _json_loads(row["protocol_json"]),
        "protocol_hash": row["protocol_hash"],
        "registration_snapshot": _json_loads(row["registration_snapshot_json"]),
        "registration_snapshot_hash": row["registration_snapshot_hash"],
        "close_note": row["close_note"],
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


def _data_quality_alert_row(row: sqlite3.Row, store: SQLiteStore | None = None) -> dict[str, Any]:
    def text(name: str) -> str:
        return store._load_text(row[name]) if store else str(row[name] or "")

    def object_json(name: str) -> dict[str, Any]:
        return store._load_json(row[name]) if store else _json_loads(row[name])

    return {
        "id": row["alert_id"],
        "category": text("category"),
        "severity": text("severity"),
        "target": text("target"),
        "detail": text("detail"),
        "next_step": text("next_step"),
        "status": text("status"),
        "first_seen_at": row["first_seen_at"],
        "last_seen_at": row["last_seen_at"],
        "last_changed_at": row["last_changed_at"],
        "resolved_at": text("resolved_at") or None,
        "occurrence_count": int(row["occurrence_count"] or 0),
        "metadata": object_json("metadata_json"),
    }


def _report_snapshot_row(row: sqlite3.Row, store: SQLiteStore, *, include_report: bool) -> dict[str, Any]:
    warnings = store._load_json(row["warnings_json"]).get("warnings") or []
    metadata = store._load_json(row["metadata_json"])
    ai_narrative = store._load_text(row["ai_narrative"])
    version = int(metadata.get("version") or 1) if isinstance(metadata, dict) else 1
    snapshot = {
        "id": row["snapshot_id"],
        "created_at": row["created_at"],
        "report_package": metadata.get("report_package") or "report_snapshot",
        "version": version,
        "version_label": metadata.get("version_label") or f"v{version}",
        "target_kind": row["target_kind"],
        "target_id": row["target_id"],
        "target_name": store._load_text(row["target_name"]),
        "prepared_for": metadata.get("prepared_for") or "",
        "prepared_by": metadata.get("prepared_by") or "",
        "reviewer": metadata.get("reviewer") or "",
        "report_purpose": metadata.get("report_purpose") or "",
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
        "signal_journal": metadata.get("signal_journal") if isinstance(metadata.get("signal_journal"), dict) else {},
        "warnings": warnings if isinstance(warnings, list) else [],
        "ai_narrative": ai_narrative,
        "ai_narrative_included": bool(ai_narrative),
        "ai_narrative_status": metadata.get("ai_narrative_status") or ("included" if ai_narrative else "not_included"),
        "ai_provider": metadata.get("ai_provider") or {},
        "audit_trail": metadata.get("audit_trail") if isinstance(metadata.get("audit_trail"), list) else [],
        "disclosure_blocks": metadata.get("disclosure_blocks") if isinstance(metadata.get("disclosure_blocks"), list) else [],
        "output_formats": metadata.get("output_formats") if isinstance(metadata.get("output_formats"), list) else [],
        "evidence": metadata.get("evidence") if isinstance(metadata.get("evidence"), dict) else {},
        "metadata": metadata,
    }
    if include_report:
        snapshot["report"] = store._load_json(row["report_json"])
        snapshot["html"] = store._load_text(row["html"])
    return snapshot


def _normalise_alerts(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for alert in alerts or []:
        alert_id = redact_secrets(str(alert.get("id") or ""))[:160]
        if not alert_id or alert_id in seen:
            continue
        seen.add(alert_id)
        out.append({
            "id": alert_id,
            "category": redact_secrets(str(alert.get("category") or "data_quality"))[:80],
            "severity": redact_secrets(str(alert.get("severity") or "info"))[:32],
            "target": redact_secrets(str(alert.get("target") or "Workspace"))[:160],
            "detail": redact_secrets(str(alert.get("detail") or ""))[:600],
            "next_step": redact_secrets(str(alert.get("next_step") or ""))[:600],
            "metadata": alert.get("metadata") if isinstance(alert.get("metadata"), dict) else {},
        })
    return out


def _alert_fingerprint(alert: dict[str, Any]) -> str:
    return _json_dumps({
        "category": alert.get("category"),
        "severity": alert.get("severity"),
        "target": alert.get("target"),
        "detail": alert.get("detail"),
        "next_step": alert.get("next_step"),
    })


def _data_quality_alert_result(alerts: list[dict[str, Any]], *, tracking_available: bool, warning: str = "") -> dict[str, Any]:
    active = [alert for alert in alerts if alert.get("status") == "active"]
    resolved = [alert for alert in alerts if alert.get("status") == "resolved"]
    notifications = [alert for alert in alerts if alert.get("should_notify")]
    return {
        "tracking_available": bool(tracking_available),
        "warning": warning,
        "active": active,
        "resolved": resolved,
        "notifications": notifications,
        "summary": {
            "active_count": len(active),
            "resolved_count": len(resolved),
            "notification_count": len(notifications),
            "new_count": sum(1 for alert in alerts if alert.get("notification_state") in {"new", "reopened"}),
            "changed_count": sum(1 for alert in alerts if alert.get("notification_state") == "changed"),
            "blocker_count": sum(1 for alert in active if alert.get("severity") == "blocker"),
            "warning_count": sum(1 for alert in active if alert.get("severity") == "warning"),
        },
    }


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
