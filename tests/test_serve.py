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
