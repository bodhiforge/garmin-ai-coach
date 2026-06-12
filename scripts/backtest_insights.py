"""Run all Phase-1 detectors against a copy of the production DB and print
everything they would store/surface. Read-only on production: always point
this at a copy."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.observations import detect_observations
from src.ai.strength_profile import strength_profile_block, strength_structural_findings
from src.db.models import Database

db_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/garmin-prod-copy.db"
db = Database(db_path)

print("=== Observations (revived detectors) ===")
memory_dir = Path("/tmp/backtest-memory")
memory_dir.mkdir(parents=True, exist_ok=True)
for observation in detect_observations(db, memory_dir):
    print(f"- {observation}")

print("\n=== Strength structural findings ===")
for finding in strength_structural_findings(db):
    print(f"- [{finding['key']}] {finding['statement']}")
    print(f"  evidence: {finding['evidence']}")

print("\n=== Strength profile block (as Riko would see it) ===")
print(strength_profile_block(db))

print("\n=== Discovery findings (gated) ===")
from src.ai.discovery import discover_patterns

for finding in discover_patterns(db):
    print(f"- [{finding['key']}] {finding['statement']}")
    print(f"  evidence: {finding['evidence']}")

print("\n=== Warning composite — would it fire today? ===")
from src.ai.warnings import health_warning

warning = health_warning(db)
print(warning if warning else "No warning (composite below threshold)")

print("\n=== Basketball profile ===")
from src.ai.basketball_profile import basketball_profile_block

print(basketball_profile_block(db))

print("\n=== Sleep rhythm ===")
from src.ai.discovery import sleep_rhythm_block

print(sleep_rhythm_block(db))

print("\n=== Deload check — would it fire today? ===")
from src.ai.deload import deload_check

deload = deload_check(db)
print(deload if deload else "No deload due (load not rising 3+ weeks with degraded recovery)")

print("\n=== Session reviews (5 most recent real sessions) ===")
from src.ai.session_review import EXCLUDED_TYPES, MIN_DURATION_MIN, review_block

recent_real = [
    a for a in db.get_recent_activities(days=60)
    if str(a.get("type")) not in EXCLUDED_TYPES
    and (a.get("duration_min") or 0) >= MIN_DURATION_MIN
][:5]
for activity in recent_real:
    print()
    print(review_block(db, activity))
