from pathlib import Path

import app as helios


def test_local_env_loader_respects_opt_out_and_existing_values(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join([
            "HELIOS_AI_ENABLED=1",
            "HELIOS_AI_PROVIDER=anthropic",
            "ANTHROPIC_API_KEY=TEST_LOCAL_KEY",
            "export HELIOS_AI_MODEL_ANTHROPIC='claude-test'",
            "BAD-KEY=ignored",
        ]),
        encoding="utf-8",
    )

    monkeypatch.setenv("HELIOS_LOAD_DOTENV", "0")
    skipped = helios._load_local_env_file(env_file)
    assert skipped["loaded"] is False

    monkeypatch.setenv("HELIOS_LOAD_DOTENV", "1")
    monkeypatch.setenv("HELIOS_AI_PROVIDER", "already-set")
    monkeypatch.delenv("HELIOS_AI_ENABLED", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("HELIOS_AI_MODEL_ANTHROPIC", raising=False)

    loaded = helios._load_local_env_file(Path(env_file))

    assert loaded["loaded"] is True
    assert loaded["count"] == 3
    assert loaded["path"] == str(env_file)
    assert helios.os.environ["HELIOS_AI_ENABLED"] == "1"
    assert helios.os.environ["HELIOS_AI_PROVIDER"] == "already-set"
    assert helios.os.environ["ANTHROPIC_API_KEY"] == "TEST_LOCAL_KEY"
    assert helios.os.environ["HELIOS_AI_MODEL_ANTHROPIC"] == "claude-test"
    assert "BAD-KEY" not in helios.os.environ
