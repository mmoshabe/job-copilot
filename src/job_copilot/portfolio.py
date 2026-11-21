"""Turn the portfolio site's data (portfolio/lib/data.ts) into plain-text evidence.

The portfolio is the most up-to-date record of experience and projects, so tailored resumes
and letters can draw on it -- and the fact-checker checks against it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
