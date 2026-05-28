from __future__ import annotations

import pandas as pd

from examples import web_app


def test_web_app_parses_symbols_and_marks() -> None:
    form = {"symbols": "600519, 000001\n600036", "marks": "600519=1500.5,000001=12.3"}

    assert web_app._symbols(form) == ["600519", "000001", "600036"]
    assert web_app._marks(form) == {"600519": 1500.5, "000001": 12.3}


def test_web_app_renders_table_and_escapes_html() -> None:
    frame = pd.DataFrame([{"symbol": "600001.SH", "reason": "<breakout>"}])

    html = web_app.render_table("Signals", frame)

    assert "Signals" in html
    assert "&lt;breakout&gt;" in html
    assert "<breakout>" not in html


def test_web_app_page_contains_core_workflows() -> None:
    html = web_app.render_page()

    assert 'action="/strategy"' in html
    assert 'action="/turtle"' in html
    assert 'action="/turtle-backtest"' in html
    assert 'action="/portfolio-buy"' in html
