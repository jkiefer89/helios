from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_return_metric_labels_are_explicit_about_mean_annualization():
    text = (ROOT / "static" / "app.js").read_text()

    assert text.count("Mean annual return") == 2
    assert '"Annual return"' not in text
