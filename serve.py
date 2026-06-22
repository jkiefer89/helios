"""Production / local-network entrypoint for Helios.

By default serves over plain HTTP with waitress (robust pure-Python WSGI),
bound to all interfaces so devices on your Wi-Fi / office network can reach it.
The whole app stays behind the HTTP Basic Auth gate configured in app.py.

    python serve.py                       # http://0.0.0.0:5000
    HELIOS_PASSWORD=hunter2 python serve.py
    HELIOS_PORT=8080 python serve.py
    HELIOS_TLS=1 python serve.py          # self-signed HTTPS (encrypts the login)

HELIOS_TLS=1 generates a self-signed certificate on first run and serves over
HTTPS so the Basic-Auth password is not sent in cleartext — recommended on any
network you do not fully trust. Browsers will warn once about the self-signed
cert; that is expected. Delete the certs/ folder to regenerate (e.g. after your
LAN IP changes).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import app as helios

HOST = os.environ.get("HELIOS_HOST", "0.0.0.0")
PORT = int(os.environ.get("HELIOS_PORT", "5000"))
TLS = os.environ.get("HELIOS_TLS", "0") == "1"

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
    finally:
        os.unlink(cfg_path)


if __name__ == "__main__":
    use_tls = TLS
    if use_tls:
        try:
            _ensure_cert()
        except Exception as e:
            print(f"  ⚠  Could not create TLS cert ({e}); serving plain HTTP instead.", flush=True)
            use_tls = False

    helios.print_banner(HOST, PORT, tls=use_tls)

    if use_tls:
        # Self-signed HTTPS via werkzeug — appropriate for a small trusted-LAN tool.
        from werkzeug.serving import run_simple

        run_simple(HOST, PORT, helios.app, threaded=True,
                   ssl_context=(str(CERT), str(KEY)))
    else:
        from waitress import serve

        serve(helios.app, host=HOST, port=PORT, threads=8, ident="Helios", channel_timeout=60)
