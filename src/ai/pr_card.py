"""PR achievement card generator. Creates shareable images for personal records."""

from __future__ import annotations

import io
from datetime import date
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches

from ..db.models import Database


def _fig_to_bytes(fig: plt.Figure) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def ski_pr_card(
    speed: float,
    previous_best: float,
    session_date: str,
    run_count: int,
    season_sessions: int,
) -> bytes:
    """Generate a ski speed PR achievement card."""
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5.5)
    ax.set_axis_off()
    fig.patch.set_facecolor("#1a1a2e")

    # Background gradient card
    card = patches.FancyBboxPatch(
        (0.3, 0.3), 9.4, 4.9, boxstyle="round,pad=0.2",
        facecolor="#16213e", edgecolor="#0f3460", linewidth=2,
    )
    ax.add_patch(card)

    # Title
    ax.text(5, 4.7, "NEW PERSONAL RECORD", ha="center", va="center",
            fontsize=20, fontweight="bold", color="#e94560",
            fontfamily="monospace")

    # Speed
    ax.text(5, 3.4, f"{speed:.1f}", ha="center", va="center",
            fontsize=52, fontweight="bold", color="white",
            fontfamily="monospace")
    ax.text(5, 2.5, "km/h", ha="center", va="center",
            fontsize=16, color="#a0a0a0", fontfamily="monospace")

    # Improvement
    improvement = speed - previous_best
    if previous_best > 0 and improvement > 0:
        ax.text(5, 1.8, f"+{improvement:.1f} km/h from previous best",
                ha="center", va="center", fontsize=12, color="#50C878",
                fontfamily="monospace")

    # Footer stats
    ax.text(1.5, 0.8, f"{session_date}", ha="center", va="center",
            fontsize=11, color="#a0a0a0", fontfamily="monospace")
    ax.text(5, 0.8, f"{run_count} runs", ha="center", va="center",
            fontsize=11, color="#a0a0a0", fontfamily="monospace")
    ax.text(8.5, 0.8, f"Session #{season_sessions}", ha="center", va="center",
            fontsize=11, color="#a0a0a0", fontfamily="monospace")

    return _fig_to_bytes(fig)


def gym_pr_card(
    exercise: str,
    weight: float,
    reps: int,
    previous_best: float,
    session_date: str,
) -> bytes:
    """Generate a gym exercise PR achievement card."""
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5.5)
    ax.set_axis_off()
    fig.patch.set_facecolor("#1a1a2e")

    card = patches.FancyBboxPatch(
        (0.3, 0.3), 9.4, 4.9, boxstyle="round,pad=0.2",
        facecolor="#16213e", edgecolor="#0f3460", linewidth=2,
    )
    ax.add_patch(card)

    # Title
    ax.text(5, 4.7, "NEW PERSONAL RECORD", ha="center", va="center",
            fontsize=20, fontweight="bold", color="#e94560",
            fontfamily="monospace")

    # Exercise name
    display_name = exercise.replace("_", " ").title()
    ax.text(5, 3.8, display_name, ha="center", va="center",
            fontsize=14, color="#a0a0a0", fontfamily="monospace")

    # Weight
    ax.text(5, 2.9, f"{weight:.0f} kg", ha="center", va="center",
            fontsize=48, fontweight="bold", color="white",
            fontfamily="monospace")
    ax.text(5, 2.1, f"× {reps} reps", ha="center", va="center",
            fontsize=16, color="#a0a0a0", fontfamily="monospace")

    # Improvement
    improvement = weight - previous_best
    if previous_best > 0 and improvement > 0:
        ax.text(5, 1.4, f"+{improvement:.0f} kg from previous best",
                ha="center", va="center", fontsize=12, color="#50C878",
                fontfamily="monospace")

    # Date
    ax.text(5, 0.8, session_date, ha="center", va="center",
            fontsize=11, color="#a0a0a0", fontfamily="monospace")

    return _fig_to_bytes(fig)


def check_and_generate_pr(db: Database) -> list[tuple[bytes, str]]:
    """Check for new PRs in the latest activity. Returns list of (png_bytes, caption)."""
    results = []

    activities = db.get_recent_activities(days=1)
    if not activities:
        return results

    latest = activities[0]

    # Ski PR check
    if latest["type"] == "skiing":
        all_ski = db.get_recent_activities(days=365, activity_type="skiing")
        if len(all_ski) < 2:
            return results

        # Find season best EXCLUDING latest
        previous_best = 0
        for a in all_ski[1:]:
            runs = db.get_ski_runs(a["id"])
            for r in (runs or []):
                speed = r.get("max_speed_kmh", 0) or 0
                if speed > previous_best:
                    previous_best = speed

        # Check latest
        latest_runs = db.get_ski_runs(latest["id"])
        if not latest_runs:
            return results
        latest_max = max((r.get("max_speed_kmh", 0) or 0 for r in latest_runs), default=0)

        if latest_max > previous_best and latest_max > 0:
            card = ski_pr_card(
                speed=latest_max,
                previous_best=previous_best,
                session_date=latest["date"],
                run_count=len(latest_runs),
                season_sessions=len(all_ski),
            )
            results.append((card, f"New season PR: {latest_max:.1f} km/h!"))

    # Gym PR check
    elif latest["type"] == "strength":
        latest_sets = db.get_gym_sets(latest["id"])
        if not latest_sets:
            return results

        all_gym = db.get_recent_activities(days=365, activity_type="strength")
        if len(all_gym) < 2:
            return results

        # Build historical max per exercise (excluding latest)
        exercise_max: dict[str, float] = {}
        for a in all_gym[1:]:
            sets = db.get_gym_sets(a["id"])
            for s in (sets or []):
                ex = s.get("exercise", "unknown")
                weight = s.get("weight_kg", 0) or 0
                if weight > exercise_max.get(ex, 0):
                    exercise_max[ex] = weight

        # Check latest session for PRs
        for s in latest_sets:
            ex = s.get("exercise", "unknown")
            weight = s.get("weight_kg", 0) or 0
            reps = s.get("reps", 0) or 0
            prev = exercise_max.get(ex, 0)
            if weight > prev and weight > 0 and prev > 0:
                card = gym_pr_card(
                    exercise=ex,
                    weight=weight,
                    reps=reps,
                    previous_best=prev,
                    session_date=latest["date"],
                )
                display = ex.replace("_", " ").title()
                results.append((card, f"New PR: {display} — {weight:.0f} kg!"))

    return results
