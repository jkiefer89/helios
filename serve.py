"""Production / controlled-network entrypoint for Helios.

By default serves over plain HTTP with waitress (robust pure-Python WSGI),
bound to localhost. Institutional mode refuses a direct public/LAN bind because
the built-in TLS path is development-only; deploy behind a production reverse
proxy while keeping Helios on loopback.

    python serve.py                       # http://127.0.0.1:5000
    HELIOS_PASSWORD='use-a-long-unique-passphrase' python serve.py
    HELIOS_PORT=8080 python serve.py
    HELIOS_TLS=1 python serve.py          # HTTPS (trusted cert paths or self-signed dev)

HELIOS_TLS=1 may generate a self-signed certificate for local development.
The built-in TLS server is for loopback development only and is never accepted
as institutional deployment evidence.
"""
from __future__ import annotations

import errno
import ipaddress
import os
import subprocess
import tempfile
from pathlib import Path

import app as helios
from engine import persistence
from helios_web import core as web_core

MIN_PASSWORD_LENGTH = 12


def parse_port(value: str | None = None) -> int:
    raw = os.environ.get("HELIOS_PORT", "5000") if value is None else value
    try:
        port = int(raw)
    except (TypeError, ValueError):
        raise SystemExit("HELIOS_PORT must be an integer between 1 and 65535.")
    if not 1 <= port <= 65535:
        raise SystemExit("HELIOS_PORT must be an integer between 1 and 65535.")
    return port


def parse_bool_env(name: str, default: str = "0") -> bool:
    raw = os.environ.get(name, default).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise SystemExit(f"{name} must be 0/1, true/false, yes/no, or on/off.")


HOST = os.environ.get("HELIOS_HOST", "127.0.0.1") or "127.0.0.1"
PORT = parse_port()
TLS = parse_bool_env("HELIOS_TLS", "0")

CERT_DIR = Path(__file__).resolve().parent / "certs"
CERT = CERT_DIR / "helios-cert.pem"
KEY = CERT_DIR / "helios-key.pem"


def configured_tls_paths() -> tuple[Path, Path] | None:
    cert_raw = os.environ.get("HELIOS_TLS_CERT_PATH", "").strip()
    key_raw = os.environ.get("HELIOS_TLS_KEY_PATH", "").strip()
    if bool(cert_raw) != bool(key_raw):
        raise SystemExit("HELIOS_TLS_CERT_PATH and HELIOS_TLS_KEY_PATH must be configured together.")
    if not cert_raw:
        return None
    cert = Path(cert_raw).expanduser()
    key = Path(key_raw).expanduser()
    if not cert.is_file() or not key.is_file():
        raise SystemExit("Configured TLS certificate/key files do not exist or are not readable.")
    return cert, key


def trusted_tls_enabled() -> bool:
    return bool(configured_tls_paths() and parse_bool_env("HELIOS_TLS_TRUSTED", "0"))


def _ensure_cert() -> None:
    """Create a self-signed cert (with SANs for localhost + LAN IPs) if missing.

    Uses an openssl config file rather than -addext for compatibility with the
    LibreSSL build that ships with macOS.
    """
    if CERT.exists() and KEY.exists():
        return
    CERT_DIR.mkdir(exist_ok=True)
    sans = ["DNS:localhost", "DNS:helios.local", "IP:127.0.0.1"]
    sans += [f"IP:{ip}" for ip in helios.lan_ips()]
    cfg = (
        "[req]\ndistinguished_name = dn\nx509_extensions = v3\nprompt = no\n"
        "[dn]\nCN = helios.local\n"
        f"[v3]\nsubjectAltName = {','.join(sans)}\nbasicConstraints = CA:FALSE\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False) as fh:
        fh.write(cfg)
        cfg_path = fh.name
    try:
        print("  Generating a self-signed TLS certificate (first run)…", flush=True)
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(KEY), "-out", str(CERT), "-days", "825", "-config", cfg_path],
            check=True, capture_output=True, text=True,
        )
        KEY.chmod(0o600)
    finally:
        os.unlink(cfg_path)


def prepare_tls() -> tuple[Path, Path]:
    """HELIOS_TLS=1 fails closed: never fall back to plain HTTP on 0.0.0.0."""
    configured = configured_tls_paths()
    if configured:
        return configured
    try:
        _ensure_cert()
    except Exception as e:
        raise SystemExit(
            f"HELIOS_TLS=1 but the TLS certificate could not be created ({e}) — "
            "install openssl (or delete the certs/ folder and retry), or unset HELIOS_TLS."
        )
    return CERT, KEY


def validate_deployment_security(host: str, use_tls: bool) -> None:
    """Institutional mode permits only loopback origin binding."""
    institutional = parse_bool_env("HELIOS_INSTITUTIONAL_CONTROLS", "1")
    public = not _is_loopback_host(host)
    if not institutional or not public:
        return
    raise SystemExit(
        "Institutional controls refuse a direct public/LAN bind. Keep HELIOS_HOST="
        "127.0.0.1 and place a production reverse proxy/IdP in front of Helios so "
        "the Werkzeug development TLS server is never internet- or LAN-facing."
    )


def _is_loopback_host(host: str) -> bool:
    value = str(host or "").strip().strip("[]").lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def port_in_use_hint(port: int) -> str:
    return (
        f"Port {port} is already in use — pick another with HELIOS_PORT=<port> "
        "(on macOS, AirPlay Receiver listens on port 5000 by default)."
    )


def print_startup_warnings() -> None:
    """Operational hints printed after the main banner."""
    password = os.environ.get("HELIOS_PASSWORD")
    if password and len(password) < MIN_PASSWORD_LENGTH:
        print(f"  ⚠  HELIOS_PASSWORD is shorter than {MIN_PASSWORD_LENGTH} characters — "
              "use a longer unique passphrase.", flush=True)
    print(f"  {encryption_status_line()}", flush=True)


def encryption_status_line() -> str:
    """One-line persistence encryption summary for the startup banner."""
    try:
        encryption = persistence.get_store().encryption
    except Exception as exc:
        return f"Persistence encryption: unavailable ({exc})"
    state = "enabled" if encryption.get("enabled") else "disabled"
    mode = encryption.get("mode") or "off"
    key_source = encryption.get("key_source") or "none"
    return f"Persistence encryption: {state} (mode={mode}, key source={key_source})"


def main() -> None:
    use_tls = TLS
    validate_deployment_security(HOST, use_tls)
    tls_paths = None
    if use_tls:
        tls_paths = prepare_tls()

    helios.print_banner(HOST, PORT, tls=use_tls)
    print_startup_warnings()

    try:
        if use_tls:
            # Werkzeug TLS is restricted to loopback in institutional mode.
            from werkzeug.serving import run_simple

            cert, key = tls_paths or (CERT, KEY)
            run_simple(HOST, PORT, helios.app, threaded=True,
                       ssl_context=(str(cert), str(key)))
        else:
            from waitress import serve

            serve(helios.app, host=HOST, port=PORT, threads=8, ident="Helios", channel_timeout=60)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            raise SystemExit(port_in_use_hint(PORT))
        raise


if __name__ == "__main__":
    main()
