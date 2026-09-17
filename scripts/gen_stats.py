"""Regenerate the profile README cards and upstream tables from public GitHub data.

Every card on the profile is a static SVG committed to this repo, so there is no
third-party widget to rate-limit me, rot, or track whoever visits the page. This
script is the only thing that writes to assets/, and the only thing that writes
between the upstream markers in README.md -- edit scripts/upstream.json instead.

Run:  GH_TOKEN=<token> python scripts/gen_stats.py
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

LOGIN = os.environ.get("PROFILE_LOGIN", "manjunathshiva")
API = "https://api.github.com/graphql"
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
README = ROOT / "README.md"
UPSTREAM = Path(__file__).resolve().parent / "upstream.json"
START, END = "<!-- upstream:start -->", "<!-- upstream:end -->"

# A dark card surface keeps every card legible in both of GitHub's themes --
# the page never learns which one the visitor picked, so the cards cannot adapt.
BG = "#0b0f14"
EDGE = "#1e2b38"
GRID = "#16212c"
TEXT = "#e6edf3"
MUTED = "#7d8da1"
DIM = "#4e5d6e"
COPPER = "#e8a33d"
SIGNAL = "#4dd0c1"
BLUE = "#7c9cf5"
ROSE = "#d96ab0"

SANS = "-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,SF Mono,Menlo,Consolas,monospace"

QUERY = """
query($login: String!) {
  user(login: $login) {
    name
    followers { totalCount }
    contributionsCollection {
      totalCommitContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
    repositories(ownerAffiliations: OWNER, isFork: false, first: 100,
                 orderBy: {field: STARGAZERS, direction: DESC}) {
      totalCount
      nodes {
        stargazerCount
        languages(first: 12, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
  }
  merged: search(query: "is:pr author:LOGIN is:merged -user:LOGIN", type: ISSUE) { issueCount }
  open: search(query: "is:pr author:LOGIN is:open -user:LOGIN", type: ISSUE) { issueCount }
}
"""

PR_QUERY = """
query($q: String!, $after: String) {
  search(query: $q, type: ISSUE, first: 100, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number title url createdAt mergedAt
        repository { nameWithOwner }
      }
    }
  }
}
"""

# Real parameter counts, each against the hardware it actually runs on. Update
# this table when a new repo moves the low end of the rail.
RAIL = [
    ("312 K", "esp32-gpio-llm", "english to GPIO, $8 ESP32-S3, no radio", 3.12e5, COPPER),
    ("28.9 M", "esp32-tinyllm", "per-layer embeddings, 25M in flash", 2.89e7, SIGNAL),
    ("~3 B", "fmx", "Apple's on-device model, one CLI call", 3e9, BLUE),
    ("7 B +", "turboquant-mlx", "weight + KV compression, Apple Silicon", 7e9, ROSE),
]

# GitHub counts .ipynb bytes including stored cell output, which can make a few
# notebooks outweigh a year of C. Set EXCLUDE_LANGUAGES="Jupyter Notebook" to
# drop that from the spectrum card.
EXCLUDED_LANGUAGES = {
    name.strip() for name in os.environ.get("EXCLUDE_LANGUAGES", "").split(",") if name.strip()
}


@dataclass
class Stats:
    name: str
    stars: int
    repos: int
    commits: int
    followers: int
    merged_upstream: int
    open_upstream: int
    contributions: int
    languages: list[tuple[str, str, float]]  # name, colour, share of bytes 0..1
    weeks: list[int]
    week_starts: list[str]


def graphql(token: str, query: str | None = None, variables: dict | None = None) -> dict:
    if query is None:
        query = QUERY.replace("LOGIN", LOGIN)
        variables = {"login": LOGIN}
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        API,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{LOGIN}-profile-cards",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if payload.get("errors"):
        raise SystemExit(f"GraphQL error: {payload['errors']}")
    return payload["data"]


def collect(data: dict) -> Stats:
    user = data["user"]
    nodes = user["repositories"]["nodes"]

    sizes: dict[str, tuple[str, int]] = {}
    for repo in nodes:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            if name in EXCLUDED_LANGUAGES:
                continue
            colour = edge["node"]["color"] or MUTED
            sizes[name] = (colour, sizes.get(name, (colour, 0))[1] + edge["size"])
    total_bytes = sum(size for _, size in sizes.values()) or 1
    ranked = sorted(sizes.items(), key=lambda kv: -kv[1][1])[:6]

    calendar = user["contributionsCollection"]["contributionCalendar"]
    weeks, week_starts = [], []
    for week in calendar["weeks"]:
        days = week["contributionDays"]
        weeks.append(sum(day["contributionCount"] for day in days))
        week_starts.append(days[0]["date"])

    return Stats(
        name=user["name"] or LOGIN,
        stars=sum(repo["stargazerCount"] for repo in nodes),
        repos=user["repositories"]["totalCount"],
        commits=user["contributionsCollection"]["totalCommitContributions"],
        followers=user["followers"]["totalCount"],
        merged_upstream=data["merged"]["issueCount"],
        open_upstream=data["open"]["issueCount"],
        contributions=calendar["totalContributions"],
        languages=[(n, c, s / total_bytes) for n, (c, s) in ranked],
        weeks=weeks,
        week_starts=week_starts,
    )


def esc(body: str) -> str:
    return body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, body, *, size=12, fill=TEXT, family=SANS, weight="400",
         anchor="start", spacing=None) -> str:
    """One <text> element.

    Styling goes in presentation attributes rather than a <style> block: GitHub
    sanitizes embedded SVG, and attributes are the part that always survives.
    """
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    return (
        f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{extra}>'
        f"{esc(body)}</text>"
    )


def card(width: int, height: int, parts: list[str], label: str) -> str:
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-label="{esc(label)}">',
            f"<title>{esc(label)}</title>",
            f'<rect width="{width}" height="{height}" rx="10" fill="{BG}"/>',
            *parts,
            f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="9.5" '
            f'fill="none" stroke="{EDGE}"/>',
            "</svg>",
        ]
    )


def compact(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
    if value >= 1_000:
        return f"{value / 1_000:.1f}k".replace(".0k", "k")
    return str(value)


def hero(stats: Stats) -> str:
    """The scale rail: how few parameters still do useful work, on what hardware."""
    width, height = 1000, 288
    left, right = 64, width - 64
    baseline = 168
    lo, hi = math.log10(1e5), math.log10(2e10)

    def position(params: float) -> float:
        return left + (math.log10(params) - lo) / (hi - lo) * (right - left)

    parts = [
        text(left, 58, stats.name, size=25, weight="600"),
        text(left, 82, "AI/ML Sr Manager @ Accenture  ·  GenAI, agentic systems, enterprise AI",
             size=13, fill=MUTED),
        text(left, 108, "I keep asking how little hardware a working model actually needs.",
             size=13, fill=SIGNAL, family=MONO),
        f'<line x1="{left}" y1="{baseline}" x2="{right}" y2="{baseline}" '
        f'stroke="{EDGE}" stroke-width="2"/>',
        text(left, 272, "parameters", size=10, fill=DIM, family=MONO, spacing="1.6"),
        text(right, 272, "cloud", size=10, fill=DIM, family=MONO, anchor="end", spacing="1.6"),
    ]

    for i, (label, repo, note, params, colour) in enumerate(RAIL):
        x = round(position(params), 1)
        # A log axis bunches the high end together -- 3B and 7B sit ~60px apart,
        # far too close for their captions. Alternate rows so neighbours never
        # share one, and drop a leader line down to whichever row this node uses.
        row = baseline + (31 if i % 2 == 0 else 74)
        # Outer captions would hang off the card if centred, so align them to
        # the ends of the rail and let the leader line connect them to the node.
        if i == 0:
            caption_x, anchor = left, "start"
        elif i == len(RAIL) - 1:
            caption_x, anchor = right, "end"
        else:
            caption_x, anchor = x, "middle"
        parts += [
            # Connector for the lower row only, and only across the gap between
            # the two caption rows -- a full-height leader would strike through
            # the upper row's caption text.
            *([f'<line x1="{x}" y1="{baseline + 54}" x2="{x}" y2="{baseline + 65}" '
               f'stroke="{colour}" stroke-width="1" stroke-opacity="0.5"/>']
              if i % 2 else []),
            f'<line x1="{x}" y1="{baseline - 9}" x2="{x}" y2="{baseline + 9}" '
            f'stroke="{colour}" stroke-width="2"/>',
            f'<circle cx="{x}" cy="{baseline}" r="4.5" fill="{BG}" stroke="{colour}" '
            f'stroke-width="2"/>',
            text(x, baseline - 21, label, size=15, fill=colour, family=MONO, weight="600",
                 anchor="middle"),
            text(caption_x, row, repo, size=11, fill=TEXT, family=MONO, anchor=anchor),
            text(caption_x, row + 16, note, size=9.5, fill=MUTED, anchor=anchor),
        ]

    readouts = [
        (compact(stats.stars), "stars"),
        (str(stats.merged_upstream), "merged upstream"),
        (str(stats.open_upstream), "in review"),
    ]
    # Laid out right to left so the readouts still read left to right.
    for i, (value, label) in enumerate(reversed(readouts)):
        x = right - i * 136
        parts += [
            text(x, 62, value, size=24, fill=TEXT, family=MONO, weight="600", anchor="end"),
            text(x, 80, label, size=10, fill=MUTED, anchor="end", spacing="0.8"),
        ]
    parts.append(
        text(right, 108, f"regenerated {date.today().isoformat()} · hand-built SVG",
             size=9.5, fill=DIM, family=MONO, anchor="end")
    )
    return card(width, height, parts,
                f"{stats.name}: on-device AI scale rail with live GitHub readouts")


def stats_card(stats: Stats) -> str:
    width, height = 430, 238
    parts = [
        text(22, 34, "instrument readout", size=11, fill=SIGNAL, family=MONO, spacing="1.4"),
        f'<line x1="22" y1="48" x2="{width - 22}" y2="48" stroke="{EDGE}"/>',
    ]
    cells = [
        (compact(stats.stars), "stars earned", COPPER),
        (str(stats.repos), "public repos", SIGNAL),
        (compact(stats.commits), "commits, 12 mo", BLUE),
        (str(stats.merged_upstream), "upstream merged", ROSE),
    ]
    for i, (value, label, colour) in enumerate(cells):
        x = 22 + (i % 2) * 204
        y = 104 + (i // 2) * 74
        parts += [
            text(x, y, value, size=30, fill=colour, family=MONO, weight="600"),
            text(x, y + 19, label, size=10.5, fill=MUTED),
        ]
    parts += [
        f'<line x1="22" y1="{height - 44}" x2="{width - 22}" y2="{height - 44}" '
        f'stroke="{EDGE}"/>',
        text(22, height - 22,
             f"{stats.contributions} contributions · {stats.followers} followers",
             size=10.5, fill=DIM, family=MONO),
    ]
    return card(width, height, parts,
                "GitHub readout: stars, public repos, commits, merged upstream pull requests")


def languages_card(stats: Stats) -> str:
    width, height = 430, 238
    bar_x, bar_y = 22, 62
    bar_w = width - 44
    parts = [text(22, 34, "language spectrum", size=11, fill=SIGNAL, family=MONO,
                  spacing="1.4")]

    cursor = float(bar_x)
    total = sum(share for _, _, share in stats.languages) or 1
    for _, colour, share in stats.languages:
        segment = share / total * bar_w
        parts.append(
            f'<rect x="{cursor:.2f}" y="{bar_y}" width="{max(segment, 1):.2f}" height="12" '
            f'fill="{colour}"/>'
        )
        cursor += segment

    for i, (name, colour, share) in enumerate(stats.languages):
        x = 22 + (i % 2) * 204
        y = 110 + (i // 2) * 30
        parts += [
            f'<rect x="{x}" y="{y - 9}" width="9" height="9" rx="2" fill="{colour}"/>',
            f'<text x="{x + 16}" y="{y}" font-family="{SANS}" font-size="11.5" '
            f'fill="{TEXT}">{esc(name)}</text>',
            text(x + 182, y, f"{share * 100:.1f}%", size=11, fill=MUTED, family=MONO,
                 anchor="end"),
        ]
    footnote = "by bytes across public non-fork repos"
    if EXCLUDED_LANGUAGES:
        footnote += f" · excl. {', '.join(sorted(EXCLUDED_LANGUAGES)).lower()}"
    parts.append(text(22, height - 22, footnote, size=10.5, fill=DIM, family=MONO))
    return card(width, height, parts, "Language spectrum by bytes across public repositories")


def trace_card(stats: Stats) -> str:
    width, height = 1000, 190
    left, right, top, floor = 52, width - 52, 62, 150
    weeks = stats.weeks or [0]
    peak = max(weeks) or 1
    step = (right - left) / max(len(weeks) - 1, 1)

    def point(index: int, value: int) -> tuple[float, float]:
        return left + index * step, floor - (value / peak) * (floor - top)

    points = [point(i, value) for i, value in enumerate(weeks)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area = f"{left},{floor} {line} {points[-1][0]:.1f},{floor}"

    parts = [
        f'<defs><linearGradient id="fade" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{SIGNAL}" stop-opacity="0.30"/>'
        f'<stop offset="1" stop-color="{SIGNAL}" stop-opacity="0.02"/>'
        f"</linearGradient></defs>",
        text(left, 34, "52-week contribution trace", size=11, fill=SIGNAL, family=MONO,
             spacing="1.4"),
        text(right, 34, f"peak {peak}/wk · {stats.contributions} total", size=11, fill=MUTED,
             family=MONO, anchor="end"),
    ]
    for fraction in (0.0, 0.5, 1.0):
        y = floor - fraction * (floor - top)
        parts += [
            f'<line x1="{left}" y1="{y:.1f}" x2="{right}" y2="{y:.1f}" stroke="{GRID}"/>',
            text(left - 10, y + 3.5, str(round(peak * fraction)), size=9, fill=DIM,
                 family=MONO, anchor="end"),
        ]
    parts += [
        f'<polygon points="{area}" fill="url(#fade)"/>',
        f'<polyline points="{line}" fill="none" stroke="{SIGNAL}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round"/>',
    ]

    seen: set[str] = set()
    for i, iso in enumerate(stats.week_starts):
        month = iso[:7]
        if month in seen:
            continue
        seen.add(month)
        parts.append(
            text(round(left + i * step, 1), floor + 22, date.fromisoformat(iso).strftime("%b"),
                 size=9.5, fill=DIM, family=MONO, anchor="middle")
        )

    hottest = max(range(len(weeks)), key=lambda i: weeks[i])
    hx, hy = point(hottest, weeks[hottest])
    parts.append(
        f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="3.5" fill="{BG}" stroke="{COPPER}" '
        f'stroke-width="2"/>'
    )
    return card(width, height, parts, "Weekly contribution trace over the last 52 weeks")


@dataclass
class PR:
    key: str  # owner/repo#number, the id upstream.json uses
    repo: str
    number: int
    title: str
    url: str
    created: str
    merged: str | None


def search_prs(token: str, state: str) -> list[PR]:
    q = f"is:pr author:{LOGIN} is:{state} -user:{LOGIN}"
    prs, after = [], None
    while True:
        page = graphql(token, PR_QUERY, {"q": q, "after": after})["search"]
        for node in page["nodes"]:
            repo = node["repository"]["nameWithOwner"]
            prs.append(PR(f"{repo}#{node['number']}", repo, node["number"], node["title"],
                          node["url"], node["createdAt"], node["mergedAt"]))
        if not page["pageInfo"]["hasNextPage"]:
            return prs
        after = page["pageInfo"]["endCursor"]


def caption(title: str) -> str:
    """Fallback copy for a PR upstream.json doesn't describe yet: its own title,
    minus the 'Python: fix:'-style prefixes, in the table's lower-case voice."""
    title = re.sub(r"^(\s*(\[[^\]]*\]|[\w.]+(\([^)]*\))?:)\s*)+", "", title).strip()
    return (title[:1].lower() + title[1:]).replace("|", "\\|")


NUMBERS = "zero one two three four five six seven eight nine ten eleven twelve".split()


def upstream_tables(merged: list[PR], open_: list[PR], copy: dict) -> str:
    live = {pr.key: pr for pr in merged + open_}
    projects = copy.get("projects", {})
    minor = set(copy.get("minor", []))

    rows, claimed = [], set()
    for row in copy.get("rows", []):
        prs = [live[key] for key in row["prs"] if key in live]  # closed unmerged: gone
        claimed.update(row["prs"])
        if prs:
            rows.append((prs, row["text"]))
    for pr in merged + open_:
        if pr.key not in claimed and pr.key not in minor:
            print(f"upstream.json has no line for {pr.key}: {pr.title}", file=sys.stderr)
            rows.append(([pr], caption(pr.title)))

    def table(heading: str, verb: str, picked: list) -> list[str]:
        lines = [heading, "", f"| project | {verb} | pr |", "|---|---|---|"]
        for prs, blurb in picked:
            repo = prs[0].repo
            links = " · ".join(f"[#{pr.number}]({pr.url})" for pr in prs)
            lines.append(f"| **{projects.get(repo, repo)}** | {blurb} | {links} |")
        return lines

    # A row stays in review until every PR in it has landed; newest activity first.
    done = [r for r in rows if all(pr.merged for pr in r[0])]
    pending = [r for r in rows if not all(pr.merged for pr in r[0])]
    done.sort(key=lambda r: max(pr.merged for pr in r[0]), reverse=True)
    pending.sort(key=lambda r: max(pr.created for pr in r[0]), reverse=True)

    lines = table(f"**merged — {len(merged)} upstream**", "what shipped", done)
    small = [pr for pr in merged if pr.key in minor]
    if small:
        owners: dict[str, list[PR]] = {}
        for pr in sorted(small, key=lambda pr: pr.key.lower()):
            owners.setdefault(pr.repo.split("/")[0], []).append(pr)
        links = []
        for owner, prs in sorted(owners.items(), key=lambda kv: (-len(kv[1]), kv[0].lower())):
            repos = {pr.repo for pr in prs}
            name = prs[0].repo if len(repos) == 1 else owner
            href = (prs[0].url if len(prs) == 1 else
                    f"https://github.com/pulls?q=is%3Apr+author%3A{LOGIN}+org%3A{owner}")
            links.append(f'<a href="{href}">{name}</a>')
        across = links[0] if len(links) == 1 else ", ".join(links[:-1]) + " and " + links[-1]
        count = NUMBERS[len(small)] if len(small) < len(NUMBERS) else str(len(small))
        lines += ["", f"<sub>plus {count} smaller docs, README and dependency fixes "
                      f"across {across}.</sub>"]
    lines += [""] + table(f"**in review — {len(open_)} signals out**", "what i shipped", pending)
    return "\n".join(lines)


def write_readme(tables: str) -> None:
    raw = README.read_bytes().decode("utf-8")
    newline = "\r\n" if "\r\n" in raw else "\n"  # the README is committed with CRLF
    start, end = raw.find(START), raw.find(END)
    if start < 0 or end < start:
        raise SystemExit(f"README.md is missing the {START} / {END} markers")
    body = tables.replace("\n", newline)
    raw = raw[:start + len(START)] + newline + body + newline + raw[end:]
    README.write_bytes(raw.encode("utf-8"))
    print("wrote README.md upstream tables")


def main() -> int:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GH_TOKEN or GITHUB_TOKEN is required", file=sys.stderr)
        return 1
    try:
        data = graphql(token)
        merged, open_ = search_prs(token, "merged"), search_prs(token, "open")
    except urllib.error.HTTPError as exc:
        print(f"GitHub API returned {exc.code}: {exc.read()[:300]!r}", file=sys.stderr)
        return 1

    stats = collect(data)
    ASSETS.mkdir(parents=True, exist_ok=True)
    for filename, svg in [
        ("hero.svg", hero(stats)),
        ("stats.svg", stats_card(stats)),
        ("languages.svg", languages_card(stats)),
        ("trace.svg", trace_card(stats)),
    ]:
        (ASSETS / filename).write_text(svg + "\n", encoding="utf-8")
        print(f"wrote assets/{filename}")
    copy = json.loads(UPSTREAM.read_text(encoding="utf-8"))
    write_readme(upstream_tables(merged, open_, copy))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
