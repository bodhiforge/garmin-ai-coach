"""
One-shot backfill: pull exercise sets from Garmin Connect API for every
strength activity in the local DB that doesn't already have gym_sets rows.

Safe to re-run (skips activities that already have sets).
Rate-limited with 0.5s delay between API calls.
"""
from __future__ import annotations

import time

from src.main import build_components


def main() -> None:
    _, _, _, sync, coach, _ = build_components(None)
    db = coach.db
    client = sync.client

    with db._connection() as conn:
        rows = conn.execute(
            """SELECT a.id, a.date, a.activity_name
               FROM activities a
               WHERE a.type = 'strength'
                 AND NOT EXISTS (SELECT 1 FROM gym_sets gs WHERE gs.activity_id = a.id)
               ORDER BY a.date DESC"""
        ).fetchall()
        targets = [dict(r) for r in rows]

    if not targets:
        print("Nothing to backfill — all strength activities already have gym_sets.")
        return

    print(f"Backfilling {len(targets)} strength activities...")
    filled = 0
    empty = 0
    failed = 0

    for i, row in enumerate(targets, 1):
        aid = row["id"]
        try:
            sets = client.get_exercise_sets(aid)
        except Exception as e:
            print(f"  [{i}/{len(targets)}] {row['date']} {aid}: ERROR {e}")
            failed += 1
            continue

        if sets:
            db.insert_gym_sets(aid, sets)
            print(f"  [{i}/{len(targets)}] {row['date']} {aid}: {len(sets)} sets")
            filled += 1
        else:
            print(f"  [{i}/{len(targets)}] {row['date']} {aid}: (no sets)")
            empty += 1

        time.sleep(0.5)

    print(
        f"\nDone. Filled: {filled}, No sets: {empty}, Failed: {failed}, "
        f"Total: {len(targets)}"
    )


if __name__ == "__main__":
    main()
