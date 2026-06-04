from __future__ import annotations

import pandas as pd

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
