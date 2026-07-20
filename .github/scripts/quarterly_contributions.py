"""Render quarterly contribution bar charts (light + dark SVG) into dist/.

Pulls per-day contribution counts for the last 20 quarters (5 years) from the
GitHub GraphQL API and aggregates them per calendar quarter. Stdlib only.
"""

import datetime as dt
import json
import os
from urllib.request import Request, urlopen

TOKEN = os.environ["GITHUB_TOKEN"]
LOGIN = os.environ.get("GH_LOGIN", "dataraptor")
OUT_DIR = os.environ.get("OUT_DIR", "dist")
QUARTERS = 20  # 5 years

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""


def fetch_weeks(day_from: dt.date, day_to: dt.date):
    body = json.dumps(
        {
            "query": QUERY,
            "variables": {
                "login": LOGIN,
                "from": f"{day_from.isoformat()}T00:00:00Z",
                "to": f"{day_to.isoformat()}T23:59:59Z",
            },
        }
    ).encode()
    req = Request(
        "https://api.github.com/graphql",
        data=body,
        headers={"Authorization": f"bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urlopen(req) as resp:
        data = json.load(resp)
    if "errors" in data:
        raise SystemExit(f"GraphQL errors: {data['errors']}")
    return data["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]


def quarter_index(day: dt.date) -> int:
    return day.year * 4 + (day.month - 1) // 3


today = dt.date.today()
last_idx = quarter_index(today)
first_idx = last_idx - (QUARTERS - 1)
start_year, start_q0 = divmod(first_idx, 4)
start_date = dt.date(start_year, start_q0 * 3 + 1, 1)

# The API caps a contributionsCollection window at one year — walk in windows.
counts: dict[int, int] = {}
window_start = start_date
while window_start <= today:
    window_end = min(window_start + dt.timedelta(days=364), today)
    for week in fetch_weeks(window_start, window_end):
        for d in week["contributionDays"]:
            day = dt.date.fromisoformat(d["date"])
            counts[quarter_index(day)] = counts.get(quarter_index(day), 0) + d["contributionCount"]
    window_start = window_end + dt.timedelta(days=1)

quarters = []
for idx in range(first_idx, last_idx + 1):
    year, q0 = divmod(idx, 4)
    quarters.append({"year": year, "q": q0 + 1, "count": counts.get(idx, 0)})
total = sum(q["count"] for q in quarters)
peak = max(quarters, key=lambda q: q["count"])

# Clean axis ceiling: smallest 1/2/2.5/5 x 10^n step whose 4th tick clears the max.
step = 10
while step * 4 < peak["count"]:
    for mult in (1, 2, 2.5, 5, 10):
        step_c = int(step * mult) if step * mult >= 10 else step * mult
        if step_c * 4 >= peak["count"]:
            step = step_c
            break
    else:
        step *= 10
        continue
    break
y_max = step * 4

THEMES = {
    "light": {
        "bar": "#2a78d6",
        "ink": "#1f2328",
        "secondary": "#59636e",
        "muted": "#818b98",
        "grid": "#d8dee4",
        "baseline": "#afb8c1",
    },
    "dark": {
        "bar": "#3987e5",
        "ink": "#e6edf3",
        "secondary": "#9198a1",
        "muted": "#767e89",
        "grid": "#262d36",
        "baseline": "#3d444d",
    },
}

W, H = 880, 300
M_LEFT, M_RIGHT, M_TOP, M_BOTTOM = 56, 20, 52, 64
INNER_W, INNER_H = W - M_LEFT - M_RIGHT, H - M_TOP - M_BOTTOM
BASE_Y = M_TOP + INNER_H
SLOT = INNER_W / QUARTERS
BAR_W = min(24, SLOT - 14)
FONT = '-apple-system, &quot;Segoe UI&quot;, Helvetica, Arial, sans-serif'


def bar_path(x: float, height: float) -> str:
    """Bar with 4px-rounded data end, square at the baseline."""
    r = min(4, height, BAR_W / 2)
    top = BASE_Y - height
    return (
        f"M{x:.1f},{BASE_Y:.1f} L{x:.1f},{top + r:.1f} Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} "
        f"L{x + BAR_W - r:.1f},{top:.1f} Q{x + BAR_W:.1f},{top:.1f} {x + BAR_W:.1f},{top + r:.1f} "
        f"L{x + BAR_W:.1f},{BASE_Y:.1f} Z"
    )


def render(theme: dict) -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="Contributions per quarter over the last five years">',
        f'<text x="{M_LEFT}" y="24" font-family="{FONT}" font-size="14" font-weight="600" '
        f'fill="{theme["ink"]}">{total:,} contributions in the last 5 years</text>',
        f'<text x="{M_LEFT}" y="41" font-family="{FONT}" font-size="11" '
        f'fill="{theme["secondary"]}">per quarter · * current quarter, in progress</text>',
    ]

    for tick in range(0, y_max + 1, step):
        y = BASE_Y - INNER_H * tick / y_max
        if tick > 0:
            parts.append(
                f'<line x1="{M_LEFT}" y1="{y:.1f}" x2="{W - M_RIGHT}" y2="{y:.1f}" '
                f'stroke="{theme["grid"]}" stroke-width="1"/>'
            )
        parts.append(
            f'<text x="{M_LEFT - 8}" y="{y + 3.5:.1f}" text-anchor="end" font-family="{FONT}" '
            f'font-size="10" fill="{theme["muted"]}">{tick:,}</text>'
        )
    parts.append(
        f'<line x1="{M_LEFT}" y1="{BASE_Y}" x2="{W - M_RIGHT}" y2="{BASE_Y}" '
        f'stroke="{theme["baseline"]}" stroke-width="1"/>'
    )

    year_spans: dict[int, list[float]] = {}
    for i, q in enumerate(quarters):
        x = M_LEFT + i * SLOT + (SLOT - BAR_W) / 2
        center = x + BAR_W / 2
        year_spans.setdefault(q["year"], []).append(center)
        if q["count"] > 0:
            height = INNER_H * q["count"] / y_max
            parts.append(f'<path d="{bar_path(x, height)}" fill="{theme["bar"]}"/>')
        if q is peak:
            parts.append(
                f'<text x="{center:.1f}" y="{BASE_Y - INNER_H * q["count"] / y_max - 7:.1f}" '
                f'text-anchor="middle" font-family="{FONT}" font-size="11" font-weight="600" '
                f'fill="{theme["secondary"]}">{q["count"]:,}</text>'
            )
        label = f"Q{q['q']}*" if i == len(quarters) - 1 else f"Q{q['q']}"
        parts.append(
            f'<text x="{center:.1f}" y="{BASE_Y + 16}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="9" fill="{theme["muted"]}">{label}</text>'
        )

    for year, centers in year_spans.items():
        parts.append(
            f'<text x="{sum(centers) / len(centers):.1f}" y="{BASE_Y + 34}" text-anchor="middle" '
            f'font-family="{FONT}" font-size="11" fill="{theme["secondary"]}">{year}</text>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


os.makedirs(OUT_DIR, exist_ok=True)
for mode, suffix in (("light", ""), ("dark", "-dark")):
    path = os.path.join(OUT_DIR, f"contributions-quarterly{suffix}.svg")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(THEMES[mode]))
    print(f"wrote {path}")
print(f"total={total:,} peak=Q{peak['q']} {peak['year']} ({peak['count']:,})")
