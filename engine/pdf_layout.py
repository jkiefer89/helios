"""Shared ReportLab layout primitives for Helios PDF exports.

Low-level drawing helpers (text, wrapped text, card grids, panels, page
chrome) used by both the institutional report exports and the model
governance approval packets. Callers import ReportLab lazily and pass its
``colors`` module in, so this layer stays import-safe when ReportLab is
absent.
"""
from __future__ import annotations

import textwrap
from typing import Any

DEFAULT_FOOTER_NOTE = "Analysis only - not investment advice, order execution, or a return guarantee."

# Lowest y coordinate page content may reach; footer() draws its rule at y=66.
CONTENT_BOTTOM = 78


def clean_text(value: Any) -> str:
    """Coerce to str and replace dash variants Helvetica cannot render."""
    return str(value or "").replace("—", "-").replace("–", "-").replace("‑", "-")


def fmt_value(value: Any, *, suffix: str = "", none_label: str = "Research locked") -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return none_label if value is None else str(value)
    return f"{number:.1f}{suffix}"


def fmt_optional_pct(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "Pending"
    return f"{number:.1f}%"


def draw_text(pdf, text: Any, x: float, y: float, size: float, color, *, bold: bool = False) -> None:
    pdf.setFillColor(color)
    pdf.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    pdf.drawString(x, y, clean_text(text))


def wrap_lines(text: Any, width: float, size: float, *, max_lines: int) -> list[str]:
    chars = max(24, int(width / max(size * 0.50, 1)))
    lines = textwrap.wrap(clean_text(text), width=chars)[:max_lines]
    return lines or [""]


def wrapped_drop(text: Any, width: float, size: float, *, max_lines: int) -> float:
    """Vertical space draw_wrapped consumes, so layouts can be planned without a canvas."""
    return len(wrap_lines(text, width, size, max_lines=max_lines)) * size * 1.38


def draw_wrapped(pdf, text: Any, x: float, y: float, width: float, size: float, color, *, bold: bool = False, max_lines: int) -> float:
    leading = size * 1.38
    for line in wrap_lines(text, width, size, max_lines=max_lines):
        draw_text(pdf, line, x, y, size, color, bold=bold)
        y -= leading
    return y


def card_grid(pdf, cards: list[tuple[str, Any]], x: float, y: float, card_width: float, card_height: float, colors) -> None:
    for index, (label, value) in enumerate(cards):
        col = index % 2
        row = index // 2
        card(pdf, x + col * (card_width + 24), y - row * (card_height + 20), card_width, card_height, str(label), str(value), colors)


def card(pdf, x: float, y: float, width: float, height: float, label: str, value: str, colors) -> None:
    pdf.setFillColor(colors.HexColor("#0d1622"))
    pdf.setStrokeColor(colors.HexColor("#263548"))
    pdf.roundRect(x, y, width, height, 8, stroke=1, fill=1)
    draw_text(pdf, label.upper(), x + 14, y + height - 20, 7.5, colors.HexColor("#9fb2c8"), bold=True)
    draw_wrapped(pdf, value, x + 14, y + height - 38, width - 28, 10.5, colors.HexColor("#f8fafc"), bold=True, max_lines=2)


def panel(pdf, x: float, y: float, width: float, height: float, title: str, body: str, colors, *, accent: str) -> None:
    pdf.setFillColor(colors.HexColor("#111827"))
    pdf.setStrokeColor(colors.HexColor("#263548"))
    pdf.roundRect(x, y, width, height, 8, stroke=1, fill=1)
    pdf.setFillColor(colors.HexColor(accent))
    pdf.rect(x, y, 4, height, stroke=0, fill=1)
    draw_text(pdf, title, x + 16, y + height - 22, 10.5, colors.HexColor(accent), bold=True)
    max_lines = max(1, int((height - 42) / 12))
    draw_wrapped(pdf, body, x + 16, y + height - 42, width - 32, 8.8, colors.HexColor("#c8d3df"), max_lines=max_lines)


def page_background(pdf, width: float, height: float, colors) -> None:
    pdf.setFillColor(colors.HexColor("#071019"))
    pdf.rect(0, 0, width, height, stroke=0, fill=1)
    pdf.setFillColor(colors.HexColor("#0d1622"))
    pdf.rect(0, height - 82, width, 82, stroke=0, fill=1)
    pdf.setStrokeColor(colors.HexColor("#263548"))
    pdf.setLineWidth(1)
    pdf.line(36, height - 82, width - 36, height - 82)


def header(
    pdf,
    width: float,
    height: float,
    section: str,
    colors,
    *,
    subtitle: str = "Advisor-Grade Research Terminal",
    subtitle_size: float = 8.5,
    subtitle_color: str = "#9fb2c8",
    section_color: str = "#55a7ff",
) -> None:
    draw_text(pdf, "HELIOS PRO", 42, height - 42, 18, colors.HexColor("#e6edf7"), bold=True)
    draw_text(pdf, subtitle, 42, height - 58, subtitle_size, colors.HexColor(subtitle_color), bold=True)
    draw_text(pdf, section, width - 260, height - 42, 10, colors.HexColor(section_color), bold=True)


def footer(
    pdf,
    width: float,
    page_no: int,
    total: int,
    colors,
    *,
    note: str = DEFAULT_FOOTER_NOTE,
    version_label: str = "",
) -> None:
    pdf.setStrokeColor(colors.HexColor("#263548"))
    pdf.line(42, 66, width - 42, 66)
    draw_text(pdf, note, 42, 46, 7.5, colors.HexColor("#9fb2c8"))
    draw_text(pdf, f"Page {page_no} of {total}", width - 104, 46, 8, colors.HexColor("#c8d3df"), bold=True)
    if version_label:
        draw_text(pdf, version_label, width - 104, 34, 7, colors.HexColor("#9fb2c8"))
