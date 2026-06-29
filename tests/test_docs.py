from __future__ import annotations

from pathlib import Path


def test_readme_recommends_thermostat_as_normal_path() -> None:
    text = Path("README.md").read_text(encoding="utf-8")
    normal = text.split("## 历史兼容", 1)[0]

    assert "恒温器策略" in normal
    assert "股票池" in normal
    assert "自选股组合不是持仓" in normal
    assert "可用现金从账户读取" in normal
    assert "模拟资金只影响临时策略测算" in normal
    assert "账户页统一管理自选组合" in normal
    assert "剔除科创板只影响本次运行" in normal
    assert "同花顺龙虎榜不可用" in normal
    assert "海龟系统" not in normal
    assert "默认技术筛选" not in normal
    assert "双均线" not in normal
