"""Chart generation for Telegram. Returns PNG bytes."""

from __future__ import annotations

import io
from datetime import date, timedelta
from typing import Any

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from ..db.models import Database

# Style defaults
COLORS = {
    "primary": "#4A90D9",
    "secondary": "#7B68EE",
    "accent": "#FF6B6B",
    "success": "#50C878",
    "warning": "#FFB347",
    "text": "#2C3E50",
    "grid": "#E8E8E8",
    "bg": "#FAFAFA",
}


def _setup_style() -> None:
    plt.rcParams.update({
        "figure.facecolor": COLORS["bg"],
        "axes.facecolor": "white",
        "axes.grid": True,
        "grid.color": COLORS["grid"],
        "grid.alpha": 0.7,
        "font.size": 11,
        "axes.titlesize": 14,
        "axes.titleweight": "bold",
        "axes.spines.top": False,
        "axes.spines.right": False,
    })


def _fig_to_bytes(fig: plt.Figure) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def ski_speed_chart(db: Database, days: int = 365) -> bytes | None:
    """Generate ski speed trend chart. Returns PNG bytes or None if no data."""
    activities = db.get_recent_activities(days=days, activity_type="skiing")
    if len(activities) < 2:
        return None

    _setup_style()

    dates = []
    max_speeds = []
    avg_speeds = []

    for a in reversed(activities):  # oldest first for plotting
        runs = db.get_ski_runs(a["id"])
        if not runs:
            continue
        speeds = [r.get("max_speed_kmh", 0) or 0 for r in runs]
        avg_run_speeds = [r.get("avg_speed_kmh", 0) or 0 for r in runs]
        dates.append(date.fromisoformat(a["date"]))
        max_speeds.append(max(speeds))
        avg_speeds.append(sum(avg_run_speeds) / len(avg_run_speeds) if avg_run_speeds else 0)

    if len(dates) < 2:
        return None

    fig, ax = plt.subplots(figsize=(10, 5))

    ax.plot(dates, max_speeds, "o-", color=COLORS["accent"], linewidth=2,
            markersize=8, label="Max Speed", zorder=3)
    ax.plot(dates, avg_speeds, "s--", color=COLORS["primary"], linewidth=1.5,
            markersize=6, label="Avg Speed", alpha=0.8, zorder=2)

    # Highlight season best
    best_idx = max_speeds.index(max(max_speeds))
    ax.annotate(f"{max_speeds[best_idx]:.1f}",
                xy=(dates[best_idx], max_speeds[best_idx]),
                xytext=(0, 12), textcoords="offset points",
                ha="center", fontweight="bold", color=COLORS["accent"])

    ax.set_ylabel("Speed (km/h)")
    ax.set_title("Ski Speed Trend")
    ax.legend(loc="lower right")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    fig.autofmt_xdate()

    return _fig_to_bytes(fig)


def gym_volume_chart(db: Database, days: int = 365) -> bytes | None:
    """Generate gym volume trend chart. Returns PNG bytes or None if no data."""
    activities = db.get_recent_activities(days=days, activity_type="strength")
    if len(activities) < 2:
        return None

    _setup_style()

    dates = []
    volumes = []
    durations = []

    for a in reversed(activities):
        sets = db.get_gym_sets(a["id"])
        if not sets:
            continue
        total_volume = sum(
            (s.get("weight_kg", 0) or 0) * (s.get("reps", 0) or 0)
            for s in sets
        )
        dates.append(date.fromisoformat(a["date"]))
        volumes.append(total_volume)
        durations.append(a.get("duration_min", 0) or 0)

    if len(dates) < 2:
        return None

    fig, ax1 = plt.subplots(figsize=(10, 5))

    ax1.bar(dates, volumes, width=1.5, color=COLORS["primary"], alpha=0.7,
            label="Volume (kg)", zorder=2)
    ax1.set_ylabel("Total Volume (kg)")
    ax1.set_title("Gym Training Volume")

    # Duration on secondary axis
    ax2 = ax1.twinx()
    ax2.plot(dates, durations, "o-", color=COLORS["warning"], linewidth=1.5,
             markersize=5, label="Duration (min)", zorder=3)
    ax2.set_ylabel("Duration (min)")
    ax2.spines["right"].set_visible(True)

    # Combined legend
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left")

    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    fig.autofmt_xdate()

    return _fig_to_bytes(fig)


def recovery_chart(db: Database, days: int = 30) -> bytes | None:
    """Generate HRV/sleep/RHR recovery trend chart. Returns PNG bytes or None."""
    metrics = db.get_recent_metrics(days=days)
    if len(metrics) < 3:
        return None

    _setup_style()

    dates = []
    hrvs = []
    sleeps = []
    rhrs = []

    for m in reversed(metrics):  # oldest first
        dates.append(date.fromisoformat(m["date"]))
        hrvs.append(m.get("hrv_last_night"))
        sleeps.append(
            m.get("sleep_duration_min", 0) / 60 if m.get("sleep_duration_min") else None
        )
        rhrs.append(m.get("resting_hr"))

    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)

    # HRV
    valid_hrvs = [(d, v) for d, v in zip(dates, hrvs) if v is not None]
    if valid_hrvs:
        d, v = zip(*valid_hrvs)
        axes[0].plot(d, v, "o-", color=COLORS["primary"], linewidth=2, markersize=5)
        avg_hrv = sum(v) / len(v)
        axes[0].axhline(y=avg_hrv, color=COLORS["grid"], linestyle="--", alpha=0.8)
        axes[0].set_ylabel("HRV (ms)")
        axes[0].set_title("Recovery Trends")

    # Sleep
    valid_sleeps = [(d, v) for d, v in zip(dates, sleeps) if v is not None]
    if valid_sleeps:
        d, v = zip(*valid_sleeps)
        bar_colors = [COLORS["accent"] if h < 7 else COLORS["success"] for h in v]
        axes[1].bar(d, v, width=0.8, color=bar_colors, alpha=0.7)
        axes[1].axhline(y=7, color=COLORS["warning"], linestyle="--", alpha=0.8, label="7h target")
        axes[1].set_ylabel("Sleep (hrs)")
        axes[1].legend(loc="lower right", fontsize=9)

    # RHR
    valid_rhrs = [(d, v) for d, v in zip(dates, rhrs) if v is not None]
    if valid_rhrs:
        d, v = zip(*valid_rhrs)
        axes[2].plot(d, v, "o-", color=COLORS["secondary"], linewidth=2, markersize=5)
        avg_rhr = sum(v) / len(v)
        axes[2].axhline(y=avg_rhr, color=COLORS["grid"], linestyle="--", alpha=0.8)
        axes[2].set_ylabel("RHR (bpm)")

    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    fig.autofmt_xdate()
    fig.tight_layout()

    return _fig_to_bytes(fig)


def generate_chart(db: Database, topic: str) -> tuple[bytes | None, str]:
    """Route chart request to the right generator. Returns (png_bytes, caption)."""
    t = topic.lower()

    if any(kw in t for kw in ["ski", "speed", "snow", "board"]):
        data = ski_speed_chart(db)
        return data, "Ski Speed Trend"
    elif any(kw in t for kw in ["gym", "volume", "strength", "lift", "weight"]):
        data = gym_volume_chart(db)
        return data, "Gym Training Volume"
    elif any(kw in t for kw in ["recovery", "hrv", "sleep", "rhr", "health"]):
        data = recovery_chart(db)
        return data, "Recovery Trends"
    else:
        # Generate all available charts, return the first one
        for fn, caption in [
            (recovery_chart, "Recovery Trends"),
            (ski_speed_chart, "Ski Speed Trend"),
            (gym_volume_chart, "Gym Training Volume"),
        ]:
            data = fn(db)
            if data is not None:
                return data, caption
        return None, ""
