#!/usr/bin/env python3
"""Prefetch one continuous historical price range per imported stock."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data_provider.base import DataFetcherManager
from src.storage import AnalysisHistory, DatabaseManager


def prefetch_prices(*, eval_window_days: int, max_workers: int) -> dict[str, int]:
    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        rows = session.execute(
            select(AnalysisHistory.code, func.min(AnalysisHistory.created_at))
            .where(AnalysisHistory.query_id.like("historical-report:%"))
            .group_by(AnalysisHistory.code)
        ).all()

    end_date = datetime.now().date()

    def fetch_one(code: str, first_seen: datetime) -> tuple[str, int]:
        start_date = first_seen.date() - timedelta(days=7)
        manager = DataFetcherManager()
        frame, source = manager.get_daily_data(
            stock_code=code,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            days=max(eval_window_days * 2, 30),
        )
        saved = db.save_daily_data(frame, code=code, data_source=source)
        return code, int(saved or 0)

    fetched = failed = saved_rows = 0
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
        futures = {pool.submit(fetch_one, code, first_seen): code for code, first_seen in rows}
        for future in as_completed(futures):
            code = futures[future]
            try:
                _, saved = future.result()
                fetched += 1
                saved_rows += saved
                print(f"prefetched {code}: {saved} rows")
            except Exception as exc:
                failed += 1
                logging.warning("prefetch failed for %s: %s", code, exc)

    return {"stocks": len(rows), "fetched": fetched, "failed": failed, "saved_rows": saved_rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--eval-window-days", type=int, default=10)
    parser.add_argument("--max-workers", type=int, default=3)
    args = parser.parse_args()
    stats = prefetch_prices(
        eval_window_days=args.eval_window_days,
        max_workers=args.max_workers,
    )
    print(json.dumps(stats, ensure_ascii=False))
    return 0 if stats["fetched"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
