from __future__ import annotations

import sys

import pandas as pd

from examples import list_lhb_candidates
from examples.list_lhb_candidates import rank_lhb_candidates


def test_rank_lhb_candidates_sums_and_orders_net_buy() -> None:
    raw = pd.DataFrame(
        [
            {"code": "1", "name": "A", "net_buy": 20},
            {"code": "000001", "name": "A", "net_buy": 30},
            {"code": "600001", "name": "B", "net_buy": 40},
        ]
    )

    ranked = rank_lhb_candidates(raw)

    assert ranked["code"].tolist() == ["000001", "600001"]
    assert ranked["net_buy"].tolist() == [50, 40]
    assert ranked["rank"].tolist() == [1, 2]


def test_rank_lhb_candidates_empty_frame_has_stable_columns() -> None:
    ranked = rank_lhb_candidates(pd.DataFrame())

    assert ranked.empty
    assert ranked.columns.tolist() == ["code", "name", "net_buy", "rank"]


def test_lhb_candidate_cli_prints_counts_without_undefined_frame(tmp_path, monkeypatch, capsys) -> None:
    ranked = pd.DataFrame(
        [
            {"code": "600001", "name": "A", "net_buy": 100, "rank": 1},
            {"code": "600002", "name": "B", "net_buy": 80, "rank": 2},
        ]
    )
    monkeypatch.setattr(list_lhb_candidates, "build_lhb_candidates", lambda start, end, top, page_size=500: (ranked.head(top), ranked))
    output = tmp_path / "symbols.txt"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "list_lhb_candidates.py",
            "--start",
            "20260623",
            "--end",
            "20260629",
            "--top",
            "2",
            "--output",
            str(output),
        ],
    )

    list_lhb_candidates.main()

    captured = capsys.readouterr()
    assert "rows=2 unique=2 top=2" in captured.out
    assert output.read_text(encoding="utf-8") == "600001,600002"
