"""Render self-hosted profile SVGs (light + dark) into dist/.

Outputs:
  contributions-quarterly[-dark].svg  - bar chart, last 20 quarters
  stats[-dark].svg                    - stat tiles (stars, followers, contributions, ...)
  top-langs[-dark].svg                - language share bar + legend
  streak[-dark].svg                   - total / this month / longest streak

Contribution counts come from GitHub's public per-day contributions endpoint
(no auth needed — same source the snake action uses). Stars/followers/languages
come from the REST API; GITHUB_TOKEN is optional but avoids rate limits in CI.
Stdlib only.
"""

import datetime as dt
import json
import os
import re
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

LOGIN = os.environ.get("GH_LOGIN", "dataraptor")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
OUT_DIR = os.environ.get("OUT_DIR", "dist")
QUARTERS = 20  # 5 years


def http_get(url: str, headers: dict | None = None) -> str:
    h = {"User-Agent": "dataraptor-profile-cards"}
    h.update(headers or {})
    last_err = None
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers=h), timeout=30) as resp:
                return resp.read().decode()
        except HTTPError:
            raise
        except OSError as e:  # timeouts, connection resets
            last_err = e
            import time
            time.sleep(2 * (attempt + 1))
    raise last_err


def api(path: str, required: bool = True):
    headers = {"Accept": "application/vnd.github+json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    try:
        return json.loads(http_get(f"https://api.github.com{path}", headers))
    except HTTPError as e:
        if required:
            sys.exit(f"GET {path} -> HTTP {e.code}: {e.read()[:300]!r}")
        return None


# ── Contribution calendar (public HTML endpoint, no auth) ──────────────────

def calendar_counts(year: int) -> dict[str, int]:
    html = http_get(f"https://github.com/users/{LOGIN}/contributions?from={year}-01-01&to={year}-12-31")
    day_ids = {}
    for td in re.findall(r"<td\b[^>]*>", html):
        m_id = re.search(r'id="([^"]+)"', td)
        m_date = re.search(r'data-date="(\d{4}-\d{2}-\d{2})"', td)
        if m_id and m_date:
            day_ids[m_id.group(1)] = m_date.group(1)
    counts = {}
    for tip_for, text in re.findall(r'<tool-tip[^>]*for="([^"]+)"[^>]*>([^<]*)</tool-tip>', html):
        if tip_for not in day_ids:
            continue
        m = re.match(r"\s*([\d,]+|No)\s+contribution", text)
        if m:
            counts[day_ids[tip_for]] = 0 if m.group(1) == "No" else int(m.group(1).replace(",", ""))
    if not counts:
        sys.exit(f"Parsed no contribution days for {year} — endpoint markup may have changed.")
    return counts


def quarter_index(day: dt.date) -> int:
    return day.year * 4 + (day.month - 1) // 3


today = dt.date.today()
last_idx = quarter_index(today)
first_idx = last_idx - (QUARTERS - 1)
start_year = first_idx // 4

# Profile first: account creation bounds the full-history scrape for the streak card.
profile = api(f"/users/{LOGIN}")
created = dt.date.fromisoformat(profile["created_at"][:10])

day_counts: dict[dt.date, int] = {}
for year in range(min(start_year, created.year), today.year + 1):
    for iso, n in calendar_counts(year).items():
        day = dt.date.fromisoformat(iso)
        if day <= today:
            day_counts[day] = n

q_counts: dict[int, int] = {}
for day, n in day_counts.items():
    q_counts[quarter_index(day)] = q_counts.get(quarter_index(day), 0) + n

quarters = []
for idx in range(first_idx, last_idx + 1):
    year, q0 = divmod(idx, 4)
    quarters.append({"year": year, "q": q0 + 1, "count": q_counts.get(idx, 0)})
total_5y = sum(q["count"] for q in quarters)
past_year = sum(n for day, n in day_counts.items() if day > today - dt.timedelta(days=365))
active_year = sum(1 for day, n in day_counts.items() if n > 0 and day > today - dt.timedelta(days=365))
peak = max(quarters, key=lambda q: q["count"])
total_all = sum(day_counts.values())

longest, longest_range, run, run_start = 0, None, 0, None
prev = None
for day in sorted(d for d, n in day_counts.items() if n > 0):
    if prev is not None and (day - prev).days == 1:
        run += 1
    else:
        run, run_start = 1, day
    if run > longest:
        longest, longest_range = run, (run_start, day)
    prev = day

month_days = [d for d in day_counts if d.year == today.year and d.month == today.month]
month_contrib = sum(day_counts[d] for d in month_days)
month_active = sum(1 for d in month_days if day_counts[d] > 0)

# ── REST: stars, languages ─────────────────────────────────────────────────

repos, page = [], 1
while True:
    batch = api(f"/users/{LOGIN}/repos?per_page=100&page={page}&type=owner")
    repos += batch
    if len(batch) < 100:
        break
    page += 1

lang_bytes: dict[str, int] = {}
lang_ok = 0
for r in repos:
    if r.get("fork"):
        continue
    data = api(f"/repos/{r['full_name']}/languages", required=False)
    if data:
        lang_ok += 1
        for name, size in data.items():
            lang_bytes[name] = lang_bytes.get(name, 0) + size
if not lang_ok:  # rate-limited: fall back to each repo's primary language, weight 1
    for r in repos:
        if not r.get("fork") and r.get("language"):
            lang_bytes[r["language"]] = lang_bytes.get(r["language"], 0) + 1

# GitHub linguist colors for identity; unknown/Other fall back to gray.
LINGUIST = {
    "Python": "#3572A5", "Jupyter Notebook": "#DA5B0B", "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6", "HTML": "#e34c26", "CSS": "#563d7c", "C++": "#f34b7d",
    "C": "#555555", "C#": "#178600", "Java": "#b07219", "Swift": "#F05138",
    "Kotlin": "#A97BFF", "R": "#198CE7", "Yacc": "#4B6C4B", "PLSQL": "#dad8d8",
    "Shell": "#89e051", "Dart": "#00B4AB", "PHP": "#4F5D95", "Go": "#00ADD8",
    "Rust": "#dea584", "MATLAB": "#e16737", "Cuda": "#3A4E3A", "TeX": "#3D6117",
    "Vue": "#41b883", "SCSS": "#c6538c", "Dockerfile": "#384d54",
    "Makefile": "#427819", "CMake": "#DA3434", "Assembly": "#6E4C13",
}
GRAY = "#8b949e"

total_bytes = sum(lang_bytes.values()) or 1
ranked = sorted(lang_bytes.items(), key=lambda kv: -kv[1])
langs = [(name, size / total_bytes) for name, size in ranked[:5] if size / total_bytes >= 0.015]
other = 1.0 - sum(share for _, share in langs)
if other >= 0.005:
    langs.append(("Other", other))

# ── Themes / shared chrome ─────────────────────────────────────────────────

THEMES = {
    "light": {
        "bar": "#2a78d6", "ink": "#1f2328", "secondary": "#59636e", "muted": "#818b98",
        "grid": "#d8dee4", "baseline": "#afb8c1", "border": "#d8dee4",
    },
    "dark": {
        "bar": "#3987e5", "ink": "#e6edf3", "secondary": "#9198a1", "muted": "#767e89",
        "grid": "#262d36", "baseline": "#3d444d", "border": "#30363d",
    },
}
FONT = '-apple-system, &quot;Segoe UI&quot;, Helvetica, Arial, sans-serif'


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── Quarterly bar chart ────────────────────────────────────────────────────

step = 10
while step * 4 < peak["count"]:
    for mult in (1, 2, 2.5, 5, 10):
        candidate = int(step * mult)
        if candidate * 4 >= peak["count"]:
            step = candidate
            break
    else:
        step *= 10
        continue
    break
y_max = step * 4

W, H = 880, 300
M_LEFT, M_RIGHT, M_TOP, M_BOTTOM = 56, 20, 52, 64
INNER_W, INNER_H = W - M_LEFT - M_RIGHT, H - M_TOP - M_BOTTOM
BASE_Y = M_TOP + INNER_H
SLOT = INNER_W / QUARTERS
BAR_W = min(24, SLOT - 14)


def bar_path(x: float, height: float) -> str:
    """Bar with 4px-rounded data end, square at the baseline."""
    r = min(4, height, BAR_W / 2)
    top = BASE_Y - height
    return (
        f"M{x:.1f},{BASE_Y:.1f} L{x:.1f},{top + r:.1f} Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} "
        f"L{x + BAR_W - r:.1f},{top:.1f} Q{x + BAR_W:.1f},{top:.1f} {x + BAR_W:.1f},{top + r:.1f} "
        f"L{x + BAR_W:.1f},{BASE_Y:.1f} Z"
    )


def render_chart(theme: dict) -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="Contributions per quarter over the last five years">',
        f'<text x="{M_LEFT}" y="24" font-family="{FONT}" font-size="14" font-weight="600" '
        f'fill="{theme["ink"]}">{total_5y:,} contributions in the last 5 years</text>',
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
            parts.append(f'<path d="{bar_path(x, INNER_H * q["count"] / y_max)}" fill="{theme["bar"]}"/>')
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


# ── Stats card ─────────────────────────────────────────────────────────────

CW, CH = 440, 170


def card_frame(theme: dict, title: str, aria: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{CW}" height="{CH}" '
        f'viewBox="0 0 {CW} {CH}" role="img" aria-label="{aria}">',
        f'<rect x="0.5" y="0.5" width="{CW - 1}" height="{CH - 1}" rx="6" fill="none" '
        f'stroke="{theme["border"]}" stroke-width="1"/>',
        f'<text x="24" y="30" font-family="{FONT}" font-size="12" font-weight="600" '
        f'fill="{theme["secondary"]}">{title}</text>',
    ]


def render_stats(theme: dict) -> str:
    tiles = [
        ("days active · past year", f"{active_year:,}"),
        ("followers", f"{profile['followers']:,}"),
        ("public repos", f"{profile['public_repos']:,}"),
        ("contributions · past year", f"{past_year:,}"),
        ("contributions · 5 years", f"{total_5y:,}"),
        (f"best quarter (Q{peak['q']} {peak['year']})", f"{peak['count']:,}"),
    ]
    parts = card_frame(theme, f"{LOGIN} · GitHub stats", "GitHub statistics")
    for i, (label, value) in enumerate(tiles):
        x = 24 + (i % 3) * 140
        y_label = 58 if i < 3 else 118
        parts.append(
            f'<text x="{x}" y="{y_label}" font-family="{FONT}" font-size="10" '
            f'fill="{theme["muted"]}">{esc(label)}</text>'
        )
        parts.append(
            f'<text x="{x}" y="{y_label + 26}" font-family="{FONT}" font-size="22" '
            f'font-weight="600" fill="{theme["ink"]}">{value}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


# ── Top languages card ─────────────────────────────────────────────────────

def render_langs(theme: dict) -> str:
    parts = card_frame(theme, "most used languages", "Most used languages")
    bar_x, bar_y, bar_w, bar_h, gap = 24, 44, CW - 48, 12, 2
    inner = bar_w - gap * (len(langs) - 1)
    parts.append(f'<clipPath id="round"><rect x="{bar_x}" y="{bar_y}" width="{bar_w}" '
                 f'height="{bar_h}" rx="{bar_h / 2}"/></clipPath>')
    x = float(bar_x)
    for name, share in langs:
        w = inner * share
        color = GRAY if name == "Other" else LINGUIST.get(name, GRAY)
        parts.append(f'<rect x="{x:.1f}" y="{bar_y}" width="{max(w, 1):.1f}" height="{bar_h}" '
                     f'fill="{color}" clip-path="url(#round)"/>')
        x += w + gap
    for i, (name, share) in enumerate(langs):
        lx = 24 + (i % 2) * 208
        ly = 84 + (i // 2) * 26
        color = GRAY if name == "Other" else LINGUIST.get(name, GRAY)
        parts.append(f'<circle cx="{lx + 4}" cy="{ly - 4}" r="4" fill="{color}"/>')
        parts.append(
            f'<text x="{lx + 14}" y="{ly}" font-family="{FONT}" font-size="12" '
            f'fill="{theme["ink"]}">{esc(name)} '
            f'<tspan fill="{theme["secondary"]}">{share * 100:.1f}%</tspan></text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


# ── Streak card (wide, three columns) ──────────────────────────────────────

def fmt_day(d: dt.date) -> str:
    return f"{d.strftime('%b')} {d.day}"


def render_streak(theme: dict) -> str:
    SW, SH = 880, 170
    if longest_range:
        ls, le = longest_range
        longest_sub = f"{fmt_day(ls)} to {fmt_day(le)}, {le.year}"
    else:
        longest_sub = "no streak yet"
    cols = [
        (f"{total_all:,}", "total contributions", f"since {fmt_day(created)}, {created.year}"),
        (f"{month_active}", f"days active · {today.strftime('%B')}",
         f"{month_contrib:,} contributions this month"),
        (f"{longest:,}", "longest streak · days", longest_sub),
    ]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SW}" height="{SH}" '
        f'viewBox="0 0 {SW} {SH}" role="img" aria-label="Contribution streak statistics">',
        f'<rect x="0.5" y="0.5" width="{SW - 1}" height="{SH - 1}" rx="6" fill="none" '
        f'stroke="{theme["border"]}" stroke-width="1"/>',
    ]
    for i, x in enumerate((SW / 3, SW * 2 / 3)):
        parts.append(f'<line x1="{x:.1f}" y1="30" x2="{x:.1f}" y2="{SH - 30}" '
                     f'stroke="{theme["border"]}" stroke-width="1"/>')
    for i, (value, label, sub) in enumerate(cols):
        cx = SW / 6 + i * SW / 3
        parts.append(
            f'<text x="{cx:.1f}" y="76" text-anchor="middle" font-family="{FONT}" '
            f'font-size="30" font-weight="600" fill="{theme["ink"]}">{value}</text>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="101" text-anchor="middle" font-family="{FONT}" '
            f'font-size="12" font-weight="600" fill="{theme["secondary"]}">{esc(label)}</text>'
        )
        parts.append(
            f'<text x="{cx:.1f}" y="121" text-anchor="middle" font-family="{FONT}" '
            f'font-size="10" fill="{theme["muted"]}">{esc(sub)}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


# ── Write everything ───────────────────────────────────────────────────────

os.makedirs(OUT_DIR, exist_ok=True)
for mode, suffix in (("light", ""), ("dark", "-dark")):
    theme = THEMES[mode]
    for name, svg in (
        (f"contributions-quarterly{suffix}.svg", render_chart(theme)),
        (f"stats{suffix}.svg", render_stats(theme)),
        (f"top-langs{suffix}.svg", render_langs(theme)),
        (f"streak{suffix}.svg", render_streak(theme)),
    ):
        path = os.path.join(OUT_DIR, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(svg)
        print(f"wrote {path}")
print(f"total_5y={total_5y:,} past_year={past_year:,} active_year={active_year} "
      f"peak=Q{peak['q']} {peak['year']} ({peak['count']:,}) langs={langs}")
