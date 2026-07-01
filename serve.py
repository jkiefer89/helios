"""Production / local-network entrypoint for Helios.

By default serves over plain HTTP with waitress (robust pure-Python WSGI),
bound to all interfaces so devices on your Wi-Fi / office network can reach it.
The whole app stays behind the HTTP Basic Auth gate configured in app.py.

    python serve.py                       # http://0.0.0.0:5000
    HELIOS_PASSWORD='use-a-long-unique-passphrase' python serve.py
    HELIOS_PORT=8080 python serve.py
    HELIOS_TLS=1 python serve.py          # self-signed HTTPS (encrypts the login)

HELIOS_TLS=1 generates a self-signed certificate on first run and serves over
HTTPS so the Basic-Auth password is not sent in cleartext — recommended on any
network you do not fully trust. Browsers will warn once about the self-signed
cert; that is expected. Delete the certs/ folder to regenerate (e.g. after your
LAN IP changes).
"""
from __future__ import annotations

import errno
import os
import subprocess
import tempfile
from pathlib import Path

import app as helios
from engine import persistence

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


HOST = os.environ.get("HELIOS_HOST", "0.0.0.0") or "0.0.0.0"
PORT = parse_port()
TLS = parse_bool_env("HELIOS_TLS", "0")

CERT_DIR = Path(__file__).resolve().parent / "certs"
CERT = CERT_DIR / "helios-cert.pem"
KEY = CERT_DIR / "helios-key.pem"


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


def prepare_tls() -> None:
    """HELIOS_TLS=1 fails closed: never fall back to plain HTTP on 0.0.0.0."""
    try:
        _ensure_cert()
    except Exception as e:
        raise SystemExit(
            f"HELIOS_TLS=1 but the TLS certificate could not be created ({e}) — "
            "install openssl (or delete the certs/ folder and retry), or unset HELIOS_TLS."
        )


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
    if use_tls:
        prepare_tls()

    helios.print_banner(HOST, PORT, tls=use_tls)
    print_startup_warnings()

    try:
        if use_tls:
            # Self-signed HTTPS via werkzeug — appropriate for a small trusted-LAN tool.
            from werkzeug.serving import run_simple

            run_simple(HOST, PORT, helios.app, threaded=True,
                       ssl_context=(str(CERT), str(KEY)))
        else:
            from waitress import serve

            serve(helios.app, host=HOST, port=PORT, threads=8, ident="Helios", channel_timeout=60)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            raise SystemExit(port_in_use_hint(PORT))
        raise


if __name__ == "__main__":
    main()
