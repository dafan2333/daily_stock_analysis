from pathlib import Path

from scripts.import_historical_reports import parse_report


def test_parse_historical_report(tmp_path: Path) -> None:
    report = tmp_path / "report_20260520.md"
    report.write_text(
        """# 2026-05-20 决策仪表盘

## 分析结果摘要

🟢 **测试股票(600519)**: 买入 | 评分 78 | 看多

---

## 🟢 测试股票 (600519)

### 核心结论

**🟢 买入** | 看多

| 点位类型 | 价格 |
|---------|------|
| 🎯 理想买入点 | 100.50元 |
| ⚡ 次优买入点 | 102.00元 |
| 🛡️ 止损位 | 95.00元 |
| 🎁 目标位 | 115.00元 |
""",
        encoding="utf-8",
    )

    signals = parse_report(report)

    assert len(signals) == 1
    signal = signals[0]
    assert signal.code == "600519"
    assert signal.name == "测试股票"
    assert signal.operation_advice == "买入"
    assert signal.sentiment_score == 78
    assert signal.trend_prediction == "看多"
    assert signal.ideal_buy == 100.5
    assert signal.secondary_buy == 102.0
    assert signal.stop_loss == 95.0
    assert signal.take_profit == 115.0
