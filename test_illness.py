from src.main import build_components
from src.ai.insights import systemic_strain_check
import sqlite3

_, _, _, sync, coach, _ = build_components(None)
conn = sqlite3.connect("data/garmin.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    "SELECT * FROM daily_metrics WHERE date >= '2025-10-01' ORDER BY date"
).fetchall()
print("Retrospective strain signal scan (MODERATE+ only):")
hits = []
for r in rows:
    m = dict(r)
    if not m.get("respiration_avg"):
        continue
    severity, signals = systemic_strain_check(coach.db, m)
    if signals:
        hits.append((m["date"], severity, signals))

for date, severity, signals in hits:
    print(f"{date}: {severity} ({len(signals)} signals)")
    for s in signals:
        print(f"    - {s}")
print(f"\nFlagged days: {len(hits)} / {len(rows)} scanned ({len(hits)/len(rows)*100:.1f}%)")
