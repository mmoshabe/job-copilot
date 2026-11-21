"""Get a job post's text.

- Greenhouse / Lever URLs -> their public job-board APIs (built for this purpose).
- Other career pages -> one polite fetch, HTML stripped to text.
- LinkedIn / Indeed -> refused: login walls and terms that forbid scraping. Paste the text instead.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path

import httpx

BLOCKED = ("linkedin.com", "indeed.com", "glassdoor.com")
UA = "job-copilot/0.1 (personal job-search tool)"


class SourceError(Exception):
    pass


class _Text(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in {"p", "br", "li", "h1", "h2", "h3", "h4", "div", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    p = _Text()
    p.feed(html.unescape(markup))
    text = "".join(p.parts)
    return re.sub(r"\n\s*\n+", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


def _greenhouse(url: str) -> str | None:
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([\w-]+)/jobs/(\d+)", url)
    if not m:
        return None
    board, job_id = m.groups()
    r = httpx.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}", timeout=20)
    r.raise_for_status()
    j = r.json()
    return f"{j.get('title', '')}\nLocation: {(j.get('location') or {}).get('name', '')}\n\n{html_to_text(j.get('content', ''))}"


def _lever(url: str) -> str | None:
    m = re.search(r"jobs\.lever\.co/([\w-]+)/([\w-]+)", url)
    if not m:
        return None
    company, posting = m.groups()
    r = httpx.get(f"https://api.lever.co/v0/postings/{company}/{posting}", timeout=20)
    r.raise_for_status()
    j = r.json()
    lists = "\n\n".join(f"{l['text']}\n{html_to_text(l['content'])}" for l in j.get("lists", []))
    loc = (j.get("categories") or {}).get("location", "")
    return f"{j.get('text', '')} at {company}\nLocation: {loc}\n\n{j.get('descriptionPlain', '')}\n\n{lists}"


def fetch_job(source: str) -> tuple[str, str | None]:
    """Returns (job_text, url_or_None). `source` is a URL, a file path, or '-' for stdin."""
    if source == "-":
        import sys

        return sys.stdin.read(), None
    path = Path(source)
    if path.exists():
        return path.read_text(), None
    if not source.startswith("http"):
        raise SourceError(f"not a URL or an existing file: {source}")
    if any(b in source for b in BLOCKED):
        raise SourceError("That site blocks automated access and its terms forbid scraping. "
                          "Copy the job text into a file and run: jobs add job.txt --url <link>")
    try:
        for handler in (_greenhouse, _lever):
            text = handler(source)
            if text:
                return text, source
        r = httpx.get(source, headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise SourceError(f"couldn't fetch {source}: {e}") from e
    text = html_to_text(r.text)
    if len(text) < 300:
        raise SourceError("The page has almost no text (probably rendered by JavaScript). Paste the job text into a file instead.")
    return text, source
