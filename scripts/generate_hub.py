#!/usr/bin/env python3
"""Rebuild the ysrao.github.io hub index from projects.json + live GitHub data.

- Curated cards come from projects.json (titles, descriptions, links).
- Every link is checked live: a page link that 404s is dropped; a code link
  whose repo no longer exists is dropped; a card left with no links is
  dropped. A renamed repo is followed to its new URL.
- Any public ysrao repo not referenced in projects.json is auto-added under
  "More projects (added automatically)", with its GitHub description and a
  Code link, plus an Open page link only if https://ysrao.github.io/<repo>/
  actually serves (200).
- Run daily by .github/workflows/update-hub.yml; also runnable by hand:
  python3 scripts/generate_hub.py
  Only the Python standard library is used, so it runs in the Action as-is.
"""
import html
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "projects.json"
OUT = ROOT / "index.html"
USER = "ysrao"
API_REPOS = f"https://api.github.com/users/{USER}/repos?per_page=100&type=public"
HEADERS = {"User-Agent": "ysrao-hub-generator", "Accept": "application/vnd.github+json"}
AUTO_SECTION = "More projects (added automatically)"


def http_status(url, timeout=20):
    """Final HTTP status after redirects, using a GET with Range (cheap)."""
    req = urllib.request.Request(url, headers={**HEADERS, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.geturl() if hasattr(e, "geturl") else url
    except Exception as e:  # network error, DNS, timeout
        print(f"  ! check failed for {url}: {e}", file=sys.stderr)
        return 0, url


def repo_exists(repo):
    status, _ = http_status(f"https://api.github.com/repos/{USER}/{repo}")
    return status in (200, 206)  # API answers the Range GET with 206


def fetch_public_repos():
    req = urllib.request.Request(API_REPOS, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def repo_name_from_code_url(url):
    # https://github.com/ysrao/<repo>[/tree/...] -> <repo>
    parts = url.split("github.com/")[1].split("/")
    return parts[1] if len(parts) > 1 else ""


def check_link(link, report):
    url, kind = link["url"], link["kind"]
    if kind == "page":
        status, final = http_status(url)
        if status in (200, 206):
            if final != url:
                report.append(f"page moved: {url} -> {final}")
                return {**link, "url": final}
            return link
        report.append(f"DROPPED dead page link ({status}): {url}")
        return None
    # code link: repo must exist on GitHub (tree/ sub-paths included)
    repo = repo_name_from_code_url(url)
    if repo and repo_exists(repo):
        return link
    report.append(f"DROPPED dead code link (repo gone): {url}")
    return None


def card_html(card):
    links = "".join(
        f'<a class="{"btn" if l["kind"] == "page" else "btn ghost"}" '
        f'href="{html.escape(l["url"])}">{html.escape(l["label"])}</a>'
        for l in card["links"]
    )
    return (
        f'<div class="card"><h3>{html.escape(card["title"])}</h3>\n'
        f'<p>{html.escape(card["description"])}</p>\n{links}</div>\n'
    )


def main():
    data = json.loads(PROJECTS.read_text())
    report = []
    known_repos = set()
    sections_html = []

    for section in data["sections"]:
        cards = []
        for card in section["cards"]:
            links = []
            for link in card["links"]:
                if link["kind"] == "code":
                    known_repos.add(repo_name_from_code_url(link["url"]))
                kept = check_link(link, report)
                if kept:
                    links.append(kept)
            if not links:
                report.append(f'DROPPED card with no live links: {card["title"]}')
                continue
            cards.append({**card, "links": links})
        if cards:
            body = "\n".join(card_html(c) for c in cards)
            sections_html.append(f'<h2>{html.escape(section["heading"])}</h2>\n{body}')

    # Auto-discovery: public repos not referenced anywhere in projects.json.
    try:
        repos = fetch_public_repos()
    except Exception as e:
        print(f"repo list fetch failed ({e}); keeping curated cards only", file=sys.stderr)
        repos = []
    auto_cards = []
    for repo in sorted(repos, key=lambda r: r["name"].lower()):
        name = repo["name"]
        if name == "ysrao.github.io" or name in known_repos:
            continue
        page_url = f"https://ysrao.github.io/{name}/"
        status, _ = http_status(page_url)
        links = []
        if status in (200, 206):
            links.append({"kind": "page", "label": "Open page", "url": page_url})
        links.append({"kind": "code", "label": "Code", "url": repo["html_url"]})
        auto_cards.append({
            "title": name,
            "description": repo.get("description") or "Code repository; no description on GitHub.",
            "links": links,
        })
        report.append(f"auto-added repo: {name} (page {'live' if status in (200, 206) else 'none'})")
    if auto_cards:
        body = "\n".join(card_html(c) for c in auto_cards)
        sections_html.append(f"<h2>{AUTO_SECTION}</h2>\n{body}")

    # Timestamp: only move it when the page content actually changed, so the
    # daily Action does not commit a timestamp-only diff every day.
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    new_sections = "\n".join(sections_html)
    if OUT.exists():
        old = OUT.read_text()
        m = re.search(r"Last updated: ([^·<]+)·", old)
        old_sections = ""
        if "<main>" in old and "<footer>" in old:
            old_sections = old.split("<main>", 1)[1].split("<footer>", 1)[0].strip()
        if m and old_sections == new_sections.strip():
            updated = m.group(1).strip()
    page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rao S. Yenamandra — Project Hub</title>
<style>
body{{margin:0;font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;background:#f6f8fb;color:#1a2332;line-height:1.55}}
header{{background:linear-gradient(135deg,#0b2d52,#0b5cad);color:#fff;padding:34px 20px 26px}}
.wrap,main{{max-width:920px;margin:0 auto}}main{{padding:22px 20px 44px}}
h1{{margin:0 0 6px;font-size:27px}}header p{{color:#cfe1f5;margin:4px 0;font-size:15px}}
h2{{font-size:19px;margin:28px 0 6px;border-bottom:2px solid #0b5cad;padding-bottom:5px}}
.card{{background:#fff;border:1px solid #dbe3ee;border-radius:10px;padding:15px 18px;margin:12px 0}}
.card h3{{margin:0 0 4px;font-size:17px}}.card p{{margin:4px 0 8px;color:#45566e;font-size:14.5px}}
a.btn{{display:inline-block;background:#0b5cad;color:#fff;text-decoration:none;padding:7px 13px;border-radius:8px;margin:2px 8px 2px 0;font-size:14px}}
a.ghost{{background:#eaf1f9;color:#0b5cad}}
footer{{color:#5b6b82;font-size:13px;border-top:1px solid #dbe3ee;margin-top:30px;padding-top:14px}}
footer a{{color:#0b5cad}}
</style></head><body>
<header><div class="wrap">
<h1>Rao S. Yenamandra — Project Hub</h1>
<p>Every technical project in one place. <b>Open page</b> runs it in your browser (github.io); <b>Code</b> takes you to the source (github.com). Non-technical web apps live on Cloudflare, not here.</p>
</div></header>
<main>

{chr(10).join(sections_html)}
<footer>Main website: <a href="https://raosyenamandra.in">raosyenamandra.in</a> · All repositories: <a href="https://github.com/ysrao">github.com/ysrao</a><br>
Last updated: {updated} · Updated automatically daily from github.com/ysrao — new public repos appear here on their own, dead links drop off. Curated entries live in <a href="https://github.com/ysrao/ysrao.github.io/blob/main/projects.json">projects.json</a>.</footer>
</main></body></html>
"""
    OUT.write_text(page)
    print(f"wrote {OUT} ({len(page)} bytes)")
    for line in report:
        print(" -", line)
    if not report:
        print(" - no changes: all curated links live, no new repos")


if __name__ == "__main__":
    main()
