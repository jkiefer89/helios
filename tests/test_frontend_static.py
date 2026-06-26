from pathlib import Path

import app as helios


ROOT = Path(__file__).resolve().parents[1]


def test_return_metric_labels_are_explicit_about_mean_annualization():
    text = (ROOT / "static" / "app.js").read_text()

    assert text.count("Mean annual return") == 2
    assert '"Annual return"' not in text


def test_react_frontend_scaffold_exists():
    assert (ROOT / "frontend" / "package.json").is_file()
    assert (ROOT / "frontend" / "vite.config.ts").is_file()
    assert (ROOT / "frontend" / "src" / "App.tsx").is_file()
    assert (ROOT / "frontend" / "src" / "api" / "client.ts").is_file()


def test_flask_serves_react_build_when_present(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=\"root\"></div>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('helios')", encoding="utf-8")
    monkeypatch.setattr(helios, "FRONTEND_DIST", dist)
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    client = helios.app.test_client()

    root = client.get("/")
    asset = client.get("/assets/app.js")
    spa = client.get("/reports")

    assert root.status_code == 200
    assert b'id="root"' in root.data
    assert asset.status_code == 200
    assert asset.data == b"console.log('helios')"
    assert spa.status_code == 200
    assert b'id="root"' in spa.data


def test_flask_falls_back_to_legacy_when_react_build_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(helios, "FRONTEND_DIST", tmp_path / "missing-dist")
    helios.app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    client = helios.app.test_client()

    root = client.get("/")
    legacy = client.get("/legacy")
    api_miss = client.get("/api/nope")

    assert root.status_code == 200
    assert legacy.status_code == 200
    assert b"Helios" in root.data
    assert b"Helios" in legacy.data
    assert api_miss.status_code == 404
    assert api_miss.is_json
