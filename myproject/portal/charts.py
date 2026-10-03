"""Tiny dependency-free line-chart helper.

Views ask for a chart; this module counts rows into weekly/monthly buckets
and returns everything pre-computed (point strings, areas, grid, labels) so
the template only prints values — no math in templates, no JS library to
load. The chart is drawn as plain SVG and scales to its container.

Usage:
    build_line_chart(
        title="Applications per week",
        series=[{"name": "Applied", "color": "#E8A33D",
                 "qs": Booking.objects.all(), "field": "applied_at"}],
        mode="week",   # or "month"
        n=8,           # number of buckets
    )
"""
from datetime import date, timedelta

from django.db.models import Count
from django.db.models.functions import TruncMonth, TruncWeek
from django.utils import timezone

# SVG geometry — viewBox coordinates, not pixels; the SVG scales to fit.
# Padding is generous on purpose: the last data point's label is wide, and
# a header/label jammed against the canvas edge looks broken.
W, H = 600, 240
PAD_L, PAD_R, PAD_T, PAD_B = 46, 28, 20, 32


def _buckets(mode, n, today):
    """The starting date of each of the last `n` weeks/months, oldest first."""
    starts = []
    if mode == "week":
        monday = today - timedelta(days=today.weekday())
        for i in range(n - 1, -1, -1):
            starts.append(monday - timedelta(weeks=i))
    elif mode == "month":
        first = today.replace(day=1)
        for i in range(n - 1, -1, -1):
            # walk back i months without calendar arithmetic pitfalls
            y, m = first.year, first.month - i
            while m <= 0:
                m += 12
                y -= 1
            starts.append(date(y, m, 1))
    else:
        raise ValueError(f"unknown chart mode: {mode!r}")
    labels = [f"{d.strftime('%b')} {d.day}" for d in starts]
    return starts, labels


def _counts(qs, field, truncate, starts):
    """One grouped-count query; returned aligned to the bucket starts."""
    rows = (
        qs.annotate(bucket=truncate(field))
        .values("bucket")
        .annotate(c=Count("id"))
    )
    by_start = {}
    for row in rows:
        key = row["bucket"].date() if hasattr(row["bucket"], "date") else row["bucket"]
        by_start[key] = row["c"]
    return [by_start.get(s, 0) for s in starts]


def build_line_chart(title, series, mode="week", n=8):
    """series: list of {name, color, qs, field} — field is the timestamp
    column to count. Returns a dict the _chart.html include can render."""
    today = timezone.localdate()
    starts, labels = _buckets(mode, n, today)
    truncate = TruncWeek if mode == "week" else TruncMonth

    values = [
        _counts(s["qs"], s["field"], truncate, starts) for s in series
    ]

    # y-axis top: a round number that leaves headroom above the tallest point
    top = max([max(v) for v in values] + [1])
    if top % 2:
        top += 1
    if top < 4:
        top = 4

    plot_w, plot_h = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    count = len(starts)
    span = count - 1 if count > 1 else 1

    def x(i):
        return round(PAD_L + plot_w * i / span, 1)

    def y(v):
        return round(PAD_T + plot_h * (1 - v / top), 1)

    rendered = []
    for definition, vals in zip(series, values):
        pts = [(x(i), y(v)) for i, v in enumerate(vals)]
        points = " ".join(f"{px},{py}" for px, py in pts)
        base = PAD_T + plot_h
        area = (
            f"M {pts[0][0]},{base} "
            + " ".join(f"L {px},{py}" for px, py in pts)
            + f" L {pts[-1][0]},{base} Z"
        )
        rendered.append({
            "name": definition["name"],
            "color": definition["color"],
            "points": points,
            "area": area,
            "dots": [{"x": px, "y": py} for px, py in pts],
            "last": vals[-1],
        })

    # x labels: with 8 buckets, label every other one so they don't crowd.
    # The LAST label is end-anchored so it can't spill past the right edge.
    step = 2 if count > 6 else 1
    xlabels = []
    for i in range(count):
        if i % step == 0 or i == count - 1:
            anchor = "end" if x(i) > W - 40 else "middle"
            xlabels.append({"x": x(i), "t": labels[i], "anchor": anchor})
        else:
            xlabels.append({"x": x(i), "t": "", "anchor": "middle"})

    grid = [
        {"y": y(0), "label": "0"},
        {"y": y(top // 2), "label": str(top // 2)},
        {"y": y(top), "label": str(top)},
    ]

    return {
        "title": title,
        "series": rendered,
        "xlabels": xlabels,
        "grid": grid,
        "w": W,
        "h": H,
        "pad_l": PAD_L,
    }
