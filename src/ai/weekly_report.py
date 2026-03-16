"""Weekly training report — chart + text summary sent every Sunday."""

from __future__ import annotations

import io
from datetime import date, timedelta
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import matplotlib.dates as mdates

from ..db.models import Database


def _fig_to_bytes(fig: plt.Figure) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def _week_range() -> tuple[date, date]:
    """Return (Monday, Sunday) of the current week."""
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def weekly_report_chart(db: Database) -> bytes | None:
    """Generate a weekly summary chart. Returns PNG bytes or None if no data."""
    monday, sunday = _week_range()
    days = (date.today() - monday).days + 1

    metrics = db.get_recent_metrics(days=days)
    activities = db.get_recent_activities(days=days)

    if not metrics and not activities:
        return None

    # Filter to this week only
    week_metrics = [m for m in metrics if m["date"] >= str(monday)]
    week_activities = [a for a in activities if a["date"] >= str(monday)]

    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    fig.suptitle(
        f"Weekly Report: {monday.strftime('%b %d')} – {sunday.strftime('%b %d')}",
        fontsize=16, fontweight="bold", y=0.98,
    )
    fig.patch.set_facecolor("#FAFAFA")

    for ax in axes.flat:
        ax.set_facecolor("white")
        ax.grid(True, alpha=0.3)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)

    # Panel 1: Activity calendar (what did you do each day)
    ax1 = axes[0, 0]
    day_labels = []
    day_colors = []
    activity_map = {"skiing": "#4A90D9", "strength": "#FF6B6B", "running": "#50C878"}

    for i in range(7):
        d = monday + timedelta(days=i)
        day_labels.append(d.strftime("%a"))
        day_acts = [a for a in week_activities if a["date"] == str(d)]
        if day_acts:
            act_type = day_acts[0]["type"]
            day_colors.append(activity_map.get(act_type, "#FFB347"))
        else:
            day_colors.append("#E8E8E8")

    bars = ax1.bar(day_labels, [1] * 7, color=day_colors, edgecolor="white", linewidth=2)
    ax1.set_ylim(0, 1.5)
    ax1.set_yticks([])
    ax1.set_title("Activity Days", fontsize=12, fontweight="bold")

    # Add activity type labels
    for i, d in enumerate(range(7)):
        day = monday + timedelta(days=d)
        day_acts = [a for a in week_activities if a["date"] == str(day)]
        if day_acts:
            label = day_acts[0]["type"][:3].upper()
            ax1.text(i, 0.5, label, ha="center", va="center",
                     fontsize=9, fontweight="bold", color="white")

    # Panel 2: HRV trend
    ax2 = axes[0, 1]
    if week_metrics:
        dates = [date.fromisoformat(m["date"]) for m in reversed(week_metrics)]
        hrvs = [m.get("hrv_last_night") for m in reversed(week_metrics)]
        valid = [(d, h) for d, h in zip(dates, hrvs) if h is not None]
        if valid:
            d, h = zip(*valid)
            ax2.plot(d, h, "o-", color="#4A90D9", linewidth=2, markersize=8)
            avg = sum(h) / len(h)
            ax2.axhline(y=avg, color="#E8E8E8", linestyle="--")
            ax2.text(d[-1], avg, f"avg: {avg:.0f}", va="bottom", fontsize=9, color="#888")
    ax2.set_title("HRV (ms)", fontsize=12, fontweight="bold")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%a"))

    # Panel 3: Sleep
    ax3 = axes[1, 0]
    if week_metrics:
        dates = [date.fromisoformat(m["date"]) for m in reversed(week_metrics)]
        sleeps = [
            (m.get("sleep_duration_min", 0) or 0) / 60
            for m in reversed(week_metrics)
        ]
        valid = [(d, s) for d, s in zip(dates, sleeps) if s > 0]
        if valid:
            d, s = zip(*valid)
            colors = ["#FF6B6B" if h < 7 else "#50C878" for h in s]
            ax3.bar(d, s, width=0.6, color=colors, alpha=0.8)
            ax3.axhline(y=7, color="#FFB347", linestyle="--", label="7h target")
            ax3.legend(fontsize=9)
    ax3.set_title("Sleep (hours)", fontsize=12, fontweight="bold")
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%a"))

    # Panel 4: Stats summary
    ax4 = axes[1, 1]
    ax4.set_xlim(0, 10)
    ax4.set_ylim(0, 10)
    ax4.set_axis_off()
    ax4.set_title("Week Summary", fontsize=12, fontweight="bold")

    total_activities = len(week_activities)
    activity_types = {}
    for a in week_activities:
        t = a["type"]
        activity_types[t] = activity_types.get(t, 0) + 1

    total_duration = sum(a.get("duration_min", 0) or 0 for a in week_activities)
    total_calories = sum(a.get("calories", 0) or 0 for a in week_activities)

    avg_sleep = 0
    sleep_vals = [
        (m.get("sleep_duration_min", 0) or 0) / 60
        for m in week_metrics if m.get("sleep_duration_min")
    ]
    if sleep_vals:
        avg_sleep = sum(sleep_vals) / len(sleep_vals)

    stats = [
        f"Sessions: {total_activities}",
        f"Types: {', '.join(f'{v}× {k}' for k, v in activity_types.items())}" if activity_types else "No activities",
        f"Total time: {total_duration:.0f} min",
        f"Calories: {total_calories:.0f} kcal",
        f"Avg sleep: {avg_sleep:.1f}h",
    ]

    for i, stat in enumerate(stats):
        ax4.text(1, 8 - i * 1.6, stat, fontsize=13, va="center", color="#2C3E50")

    fig.tight_layout(rect=[0, 0, 1, 0.95])
    return _fig_to_bytes(fig)


def weekly_report_text(db: Database) -> str:
    """Generate weekly summary text for LLM to present."""
    monday, _ = _week_range()
    days = (date.today() - monday).days + 1

    activities = db.get_recent_activities(days=days)
    metrics = db.get_recent_metrics(days=days)

    week_activities = [a for a in activities if a["date"] >= str(monday)]
    week_metrics = [m for m in metrics if m["date"] >= str(monday)]

    lines = ["## Weekly Summary (computed)"]

    # Activity count
    total = len(week_activities)
    lines.append(f"Sessions: {total}")
    if week_activities:
        types = {}
        for a in week_activities:
            types[a["type"]] = types.get(a["type"], 0) + 1
        lines.append(f"Types: {', '.join(f'{v}× {k}' for k, v in types.items())}")
        total_min = sum(a.get("duration_min", 0) or 0 for a in week_activities)
        lines.append(f"Total training time: {total_min:.0f} min")

    # Sleep quality
    sleep_vals = [
        (m.get("sleep_duration_min", 0) or 0) / 60
        for m in week_metrics if m.get("sleep_duration_min")
    ]
    if sleep_vals:
        avg = sum(sleep_vals) / len(sleep_vals)
        under_7 = sum(1 for s in sleep_vals if s < 7)
        lines.append(f"Avg sleep: {avg:.1f}h ({under_7} nights under 7h)")

    # HRV trend
    hrvs = [m.get("hrv_last_night") for m in week_metrics if m.get("hrv_last_night")]
    if len(hrvs) >= 2:
        direction = "rising" if hrvs[0] > hrvs[-1] else "declining"
        lines.append(f"HRV: {hrvs[-1]:.0f} → {hrvs[0]:.0f}ms ({direction})")

    return "\n".join(lines)


def generate_weekly_report(db: Database, coach) -> tuple[bytes | None, str]:
    """Generate weekly report chart + text. Returns (png_bytes, message)."""
    chart = weekly_report_chart(db)
    summary = weekly_report_text(db)

    message = coach._call_ai(
        "You are a concise fitness coach delivering a weekly training summary via Telegram. "
        "Present the computed summary below. Be brief, highlight wins and areas to improve. "
        "End with one specific goal for next week. Under 400 characters.",
        summary,
    )

    return chart, message
