from pathlib import Path


def test_runtime_requirements_include_imported_openpyxl_dependency():
    requirements = Path("requirements.txt").read_text()

    assert "openpyxl" in requirements


def test_env_example_documents_supported_runtime_variables():
    env_example = Path(".env.example")

    assert env_example.exists()
    text = env_example.read_text()
    for name in (
        "HELIOS_USER",
        "HELIOS_PASSWORD",
        "HELIOS_PORT",
        "HELIOS_HOST",
        "HELIOS_TLS",
        "HELIOS_AUTH",
        "HELIOS_RF",
    ):
        assert name in text
