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
    assert 'class="table-wrap"' in html
    assert "&lt;breakout&gt;" in html
    assert "<breakout>" not in html


def test_web_app_page_contains_core_workflows() -> None:
    html = web_app.render_page(page="strategy")

    assert 'action="/strategy"' in html
    assert 'action="/turtle"' not in html
    assert 'href="/turtle"' in html
    assert 'value="600519,000001,600036"' not in html
    assert 'value="600172"' not in html


def test_web_app_renders_separate_feature_pages() -> None:
    turtle = web_app.render_page(page="turtle")
    backtest = web_app.render_page(page="backtest")
    portfolio = web_app.render_page(page="portfolio")

    assert 'action="/turtle"' in turtle
    assert 'action="/strategy"' not in turtle
    assert 'action="/turtle-backtest"' in backtest
    assert 'action="/portfolio-buy"' in portfolio
    assert 'action="/portfolio-sell"' in portfolio


def test_web_app_preserves_submitted_form_values() -> None:
    html = web_app.render_page(form={"symbols": "600172", "start": "20260501", "refresh": "on"})

    assert 'value="600172"' in html
    assert 'value="20260501"' in html
    assert 'name="refresh" checked' in html


def test_web_app_can_render_last_form_on_refresh() -> None:
    web_app.LAST_FORM.clear()
    web_app.LAST_FORM.update({"symbols": "600172"})

    html = web_app.render_page(form=web_app.LAST_FORM)

    assert 'value="600172"' in html


def test_web_app_result_shows_request_parameters() -> None:
    result = web_app.handle_strategy(
        {
            "strategy": "turtle",
            "symbols": "600172",
            "start": "20250527",
            "end": "20260527",
            "top": "10",
        }
    )

    assert result.summaries[0]["symbols"] == "600172"
    assert result.summaries[0]["strategy"] == "turtle"
