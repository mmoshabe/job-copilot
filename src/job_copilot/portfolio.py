"""Turn the portfolio site's data (portfolio/lib/data.ts) into plain-text evidence.

The portfolio is the most up-to-date record of experience and projects, so tailored resumes
and letters can draw on it -- and the fact-checker checks against it."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import httpx
import json5

_EXPORT = ("import(process.argv[1]).then(m => console.log(JSON.stringify("
           "{site: m.site, experience: m.experience, work: m.work, credentials: m.credentials, "
           "sides: m.sides, stats: m.stats})))")


def export_portfolio(data_ts: Path) -> dict:
    """Uses Node's built-in TypeScript type-stripping (Node >= 22.18 / 24)."""
    if not data_ts.exists():
        raise FileNotFoundError(f"portfolio data not found: {data_ts}")
    out = subprocess.run(["node", "--no-warnings", "-e", _EXPORT, str(data_ts)],
                         capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def portfolio_markdown(p: dict) -> str:
    s = p.get("site", {})
    lines = [f"# Portfolio: {s.get('name', '')}", f"{s.get('role', '')} · {s.get('location', '')} · {s.get('status', '')}", ""]
    if p.get("stats"):
        lines += ["## Headline numbers"] + [f"- {x['value']}: {x['label']}" for x in p["stats"]] + [""]
    lines.append("## Experience")
    for e in p.get("experience", []):
        lines.append(f"### {e['role']}, {e['org']} ({e['dates']})")
        lines += [f"- {pt}" for pt in e.get("points", [])]
    lines += ["", "## Products and projects"]
    for w in p.get("work", []):
        link = f" ({w['href']})" if w.get("href") else ""
        lines.append(f"- **{w['name']}**: {w['tag']}. {w['desc']}{link}")
    if p.get("sides"):
        lines += ["", "## Skills"] + [f"- {k}: {', '.join(v)}" for k, v in p["sides"].items()]
    if p.get("credentials"):
        lines += ["", "## Credentials"] + [f"- {c['kicker']}: {c['title']} ({c.get('meta', '')})" for c in p["credentials"]]
    return "\n".join(lines)


def evidence(cv: str, portfolio_md: str | None = None, projects_md: str | None = None) -> str:
    parts = [f"<cv>\n{cv}\n</cv>"]
    if portfolio_md:
        parts.append(f"<portfolio>\n{portfolio_md}\n</portfolio>")
    if projects_md:
        parts.append(f"<projects>\n{projects_md}\n</projects>")
    return "\n\n".join(parts)


_WANTED = ("site", "experience", "work", "credentials", "sides", "stats")


def _literal_end(src: str, start: int) -> int:
    """Index just past the object/array literal starting at src[start], skipping strings and comments."""
    depth, i, quote = 0, start, None
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'`":
            quote = ch
        elif src.startswith("//", i):
            i = src.index("\n", i)
            continue
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise ValueError("unterminated literal")


def parse_data_ts(src: str) -> dict:
    """Parse `export const name = {...}` / `[...]` literals without Node (used on the server)."""
    out = {}
    for m in re.finditer(r"^export const (\w+)(?:\s*:[^=]+)?\s*=\s*", src, re.M):
        name, start = m.group(1), m.end()
        if name in _WANTED and src[start] in "[{":
            out[name] = json5.loads(src[start:_literal_end(src, start)])
    return out


def fetch_portfolio(url: str) -> dict:
    r = httpx.get(url, timeout=20, follow_redirects=True)
    r.raise_for_status()
    return parse_data_ts(r.text)
