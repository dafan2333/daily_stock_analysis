#!/usr/bin/env python3
"""Import archived Markdown reports into analysis_history for backtesting."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.storage import AnalysisHistory, DatabaseManager


REPORT_DATE_RE = re.compile(r"report_(\d{8})\.md$", re.IGNORECASE)
SECTION_RE = re.compile(r"^##\s+(?!#)(?:[^\w\u4e00-\u9fff]*\s*)?(.+?)\s+\((\d{6})\)\s*$")
SUMMARY_RE = re.compile(
    r"^\s*[^\w\u4e00-\u9fff]*\s*\*\*(.+?)\((\d{6})\)\*\*:\s*(.+?)\s*\|\s*评分\s*(\d+)",
)
ADVICE_RE = re.compile(
    r"^\s*\*\*[^\w\u4e00-\u9fff]*(买入|加仓|持有|减仓|卖出|观望|等待)\*\*\s*\|\s*(.+?)\s*$",
)
PRICE_RE = re.compile(r"(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)")


@dataclass
class ImportedSignal:
    code: str
    name: str
    analysis_date: datetime
    operation_advice: str = "观望"
    sentiment_score: Optional[int] = None
    trend_prediction: Optional[str] = None
    ideal_buy: Optional[float] = None
    secondary_buy: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    raw_section: str = ""


def _first_price(text: str) -> Optional[float]:
    normalized = text.replace(",", "")
    has_yuan = "元" in normalized
    if "元" in normalized:
        normalized = normalized.split("元", 1)[0]
    matches = list(PRICE_RE.finditer(normalized))
    if not matches:
        return None
    # Explanatory text may mention MA5/MA10 before the actual price.
    match = matches[-1] if has_yuan else matches[0]
    value = float(match.group(1))
    return value if value > 0 else None


def _last_table_cell(line: str) -> str:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return cells[-1] if cells else line


def parse_report(path: Path) -> list[ImportedSignal]:
    date_match = REPORT_DATE_RE.search(path.name)
    if not date_match:
        return []
    analysis_date = datetime.strptime(date_match.group(1), "%Y%m%d")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = text.splitlines()

    summaries: dict[str, tuple[str, int]] = {}
    for line in lines:
        match = SUMMARY_RE.match(line)
        if match:
            summaries[match.group(2)] = (match.group(3).strip(), int(match.group(4)))

    starts: list[tuple[int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        match = SECTION_RE.match(line)
        if match:
            starts.append((index, match))

    signals: list[ImportedSignal] = []
    for position, (start, match) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        section_lines = lines[start:end]
        code = match.group(2)
        signal = ImportedSignal(
            code=code,
            name=match.group(1).strip(),
            analysis_date=analysis_date,
            raw_section="\n".join(section_lines),
        )
        if code in summaries:
            signal.operation_advice, signal.sentiment_score = summaries[code]

        for line in section_lines:
            advice_match = ADVICE_RE.match(line)
            if advice_match:
                signal.operation_advice = advice_match.group(1)
                signal.trend_prediction = advice_match.group(2).strip()
                continue

            if "理想买入点" in line:
                signal.ideal_buy = _first_price(_last_table_cell(line))
            elif "次优买入点" in line:
                signal.secondary_buy = _first_price(_last_table_cell(line))
            elif "止损位" in line:
                signal.stop_loss = _first_price(_last_table_cell(line))
            elif "目标位" in line or "止盈位" in line:
                signal.take_profit = _first_price(_last_table_cell(line))

        signals.append(signal)
    return signals


def discover_reports(root: Path) -> Iterable[Path]:
    # Multiple artifacts can contain the same report date. Keep one deterministic copy.
    unique: dict[str, Path] = {}
    for path in sorted(root.rglob("report_*.md")):
        match = REPORT_DATE_RE.search(path.name)
        if match:
            unique.setdefault(match.group(1), path)
    return unique.values()


def import_reports(root: Path, *, dry_run: bool = False) -> dict[str, int]:
    db = DatabaseManager.get_instance()
    parsed = inserted = skipped = 0

    for report in discover_reports(root):
        for signal in parse_report(report):
            parsed += 1
            query_id = f"historical-report:{signal.analysis_date:%Y%m%d}:{signal.code}"
            with db.get_session() as session:
                exists = session.execute(
                    select(AnalysisHistory.id).where(AnalysisHistory.query_id == query_id).limit(1)
                ).scalar()
                if exists:
                    skipped += 1
                    continue
                if dry_run:
                    inserted += 1
                    continue
                session.add(
                    AnalysisHistory(
                        query_id=query_id,
                        code=signal.code,
                        name=signal.name,
                        report_type="historical_import",
                        sentiment_score=signal.sentiment_score,
                        operation_advice=signal.operation_advice,
                        trend_prediction=signal.trend_prediction,
                        analysis_summary="Imported from archived GitHub Actions Markdown report.",
                        raw_result=json.dumps(
                            {"source": "github_actions_artifact", "markdown": signal.raw_section},
                            ensure_ascii=False,
                        ),
                        context_snapshot=json.dumps(
                            {"enhanced_context": {"date": signal.analysis_date.date().isoformat()}},
                            ensure_ascii=False,
                        ),
                        ideal_buy=signal.ideal_buy,
                        secondary_buy=signal.secondary_buy,
                        stop_loss=signal.stop_loss,
                        take_profit=signal.take_profit,
                        created_at=signal.analysis_date,
                    )
                )
                session.commit()
                inserted += 1

    return {"parsed": parsed, "inserted": inserted, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Directory containing extracted report artifacts")
    parser.add_argument("--dry-run", action="store_true", help="Parse without writing to the database")
    args = parser.parse_args()

    stats = import_reports(args.root, dry_run=args.dry_run)
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
