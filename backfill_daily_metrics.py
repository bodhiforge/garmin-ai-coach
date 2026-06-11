"""
One-shot backfill: pull daily_metrics from Garmin API for historical dates.
Skips dates that already exist in DB.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

from src.main import build_components


def main() -> None:
    _, _, _, sync, coach, _ = build_components(None)
    db = coach.db

    start = date(2025, 10, 1)
    end = date(2026, 3, 14)

    with db._connection() as conn:
        existing = {r[0] for r in conn.execute("SELECT date FROM daily_metrics").fetchall()}

    targets: list[date] = []
    d = start
    while d <= end:
        if d.isoformat() not in existing:
            targets.append(d)
        d += timedelta(days=1)

    if not targets:
        print("Nothing to backfill — all historical dates already present.")
        return

    print(f"Backfilling {len(targets)} days: {targets[0]} to {targets[-1]}")
    filled = 0
    empty = 0
    failed = 0

    for i, target in enumerate(targets, 1):
        try:
            metrics = sync.sync_daily_metrics(target)
            if metrics.get("sleep_duration_min") is not None or metrics.get("hrv_last_night") is not None:
                filled += 1
                if i % 20 == 0 or i == len(targets):
                    print(f"  [{i}/{len(targets)}] {target}: ok (filled={filled}, empty={empty}, failed={failed})")
            else:
                empty += 1
        except Exception as e:
            print(f"  [{i}/{len(targets)}] {target}: ERROR {type(e).__name__}: {str(e)[:80]}")
            failed += 1

        time.sleep(0.3)

    print(f"\nDone. Filled: {filled}, Empty: {empty}, Failed: {failed}, Total: {len(targets)}")


if __name__ == "__main__":
    main()
