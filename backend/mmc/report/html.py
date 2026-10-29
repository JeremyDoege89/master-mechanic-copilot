"""HTML report rendering (M6 §3): Jinja2 with autoescape, print CSS fallback."""

from __future__ import annotations

from pathlib import Path

import jinja2

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _environment() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )


def render_html(report: dict) -> str:
    """Render ``report`` to an HTML string (all values escaped)."""
    template = _environment().get_template("report.html.j2")
    return template.render(report=report)
