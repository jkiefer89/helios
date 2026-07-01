import pytest

import serve


def test_parse_port_rejects_non_integer_with_clear_message(monkeypatch):
    monkeypatch.setenv("HELIOS_PORT", "not-a-port")

    with pytest.raises(SystemExit, match="HELIOS_PORT"):
        serve.parse_port()


def test_parse_port_rejects_out_of_range_with_clear_message(monkeypatch):
    monkeypatch.setenv("HELIOS_PORT", "70000")

    with pytest.raises(SystemExit, match="HELIOS_PORT"):
        serve.parse_port()


def test_tls_fails_closed_when_cert_generation_fails(monkeypatch):
    def broken_cert():
        raise RuntimeError("openssl missing")

    monkeypatch.setattr(serve, "_ensure_cert", broken_cert)

    with pytest.raises(SystemExit, match="HELIOS_TLS"):
        serve.prepare_tls()


def test_port_in_use_hint_names_env_var_and_airplay_gotcha():
    hint = serve.port_in_use_hint(5000)

    assert "HELIOS_PORT" in hint
    assert "AirPlay" in hint
    assert "5000" in hint


def test_main_exits_with_port_hint_on_eaddrinuse(monkeypatch, capsys):
    import errno

    import waitress

    def bind_failure(*args, **kwargs):
        raise OSError(errno.EADDRINUSE, "address already in use")

    monkeypatch.setattr(serve, "TLS", False)
    monkeypatch.setattr(waitress, "serve", bind_failure)

    with pytest.raises(SystemExit, match="HELIOS_PORT"):
        serve.main()


def test_short_password_warning_is_printed(monkeypatch, capsys):
    monkeypatch.setenv("HELIOS_PASSWORD", "short")

    serve.print_startup_warnings()

    out = capsys.readouterr().out
    assert "HELIOS_PASSWORD" in out
    assert "12" in out


def test_long_password_produces_no_warning(monkeypatch, capsys):
    monkeypatch.setenv("HELIOS_PASSWORD", "a-much-longer-unique-passphrase")

    serve.print_startup_warnings()

    assert "⚠" not in capsys.readouterr().out


def test_startup_banner_reports_persistence_encryption_status():
    line = serve.encryption_status_line()

    assert line.startswith("Persistence encryption:")
    assert "mode=" in line


def test_docstring_does_not_advertise_weak_example_password():
    assert "hunter2" not in (serve.__doc__ or "")
