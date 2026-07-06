import os
from pathlib import Path

from helios_web import localenv


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

    # The loader writes os.environ directly (bypassing monkeypatch bookkeeping),
    # so snapshot every key it can touch and restore them explicitly — a plain
    # monkeypatch.delenv on an absent key registers no undo.
    touched = ("HELIOS_AI_ENABLED", "HELIOS_AI_PROVIDER", "ANTHROPIC_API_KEY", "HELIOS_AI_MODEL_ANTHROPIC")
    before = {k: os.environ.get(k) for k in touched}
    try:
        monkeypatch.setenv("HELIOS_LOAD_DOTENV", "0")
        skipped = localenv._load_local_env_file(env_file)
        assert skipped["loaded"] is False

        monkeypatch.setenv("HELIOS_LOAD_DOTENV", "1")
        monkeypatch.setenv("HELIOS_AI_PROVIDER", "already-set")
        monkeypatch.delenv("HELIOS_AI_ENABLED", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("HELIOS_AI_MODEL_ANTHROPIC", raising=False)

        loaded = localenv._load_local_env_file(Path(env_file))

        assert loaded["loaded"] is True
        assert loaded["count"] == 3
        assert loaded["path"] == str(env_file)
        assert os.environ["HELIOS_AI_ENABLED"] == "1"
        assert os.environ["HELIOS_AI_PROVIDER"] == "already-set"
        assert os.environ["ANTHROPIC_API_KEY"] == "TEST_LOCAL_KEY"
        assert os.environ["HELIOS_AI_MODEL_ANTHROPIC"] == "claude-test"
        assert "BAD-KEY" not in os.environ
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
