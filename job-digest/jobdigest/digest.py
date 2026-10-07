"""Runs one digest: fetch → filter → dedupe → skip already-sent → email."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import emailer, github_issue
from .dedupe import dedupe, normalize_company, normalize_title
from .models import Job
from .sources import SOURCES, MissingKey

HERE = Path(__file__).resolve().parent.parent
LOCAL_TZ = ZoneInfo("America/Los_Angeles")

# Some sources can't be held to a strict 24-hour window, so they look back
# further and rely on the "already sent" list to show each job only once:
#  - Remotive's public API publishes listings about a day after posting.
#  - USAJOBS dates are calendar days (midnight Eastern), not exact times.
SOURCE_LOOKBACK_HOURS = {"Remotive": 72, "USAJOBS": 48}

# How long to remember jobs that were already emailed.
SEEN_DAYS = 30


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------

def _words(text: str) -> list[str]:
    # Keep text inside brackets: "Visual Information Specialist (Graphic Designer)".
    return normalize_title(re.sub(r"[()\[\]{}]", " ", text or "")).split()


def _phrase_in(phrase_words: list[str], text_words: list[str]) -> bool:
    # Each keyword word must appear in the text. Comparing the first six
    # letters lets "designer" match "design"/"designers".
    return all(any(t[:6] == w[:6] and len(t) >= min(len(w), 6) for t in text_words)
               for w in phrase_words)


def matches_keywords(job: Job, keywords: list[str]) -> bool:
    """Role phrases ("graphic designer") must be in the title. Single-word
    skills ("InDesign", "ePub") may be in the title or the description."""
    title = _words(job.title)
    desc = None
    for kw in keywords:
        kw_words = _words(kw)
        if not kw_words:
            continue
        if _phrase_in(kw_words, title):
            return True
        if len(kw_words) == 1:
            if desc is None:
                desc = set(_words(job.description))
            if kw_words[0] in desc:
                return True
    return False


# ---------------------------------------------------------------------------
# "Already sent" memory
# ---------------------------------------------------------------------------

def _job_key(job: Job) -> str:
    return f"{normalize_company(job.company)}|{normalize_title(job.title)}"


def load_seen(path: Path) -> dict[str, str]:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_seen(path: Path, seen: dict[str, str], now: datetime) -> None:
    cutoff = (now - timedelta(days=SEEN_DAYS)).isoformat()
    seen = {k: v for k, v in seen.items() if v >= cutoff}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen, indent=0, sort_keys=True))


def already_sent(job: Job, seen: dict[str, str]) -> bool:
    return _job_key(job) in seen or any(u in seen for u in job.merged_uids)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jobdigest", description=__doc__)
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--state", default=str(HERE / "state" / "seen.json"),
                    help="file that remembers jobs already emailed")
    ap.add_argument("--dry-run", action="store_true",
                    help="don't send email or update the memory file")
    ap.add_argument("--preview", metavar="FILE.html",
                    help="also write the email to this HTML file")
    ap.add_argument("--ignore-seen", action="store_true",
                    help="include jobs even if an earlier digest already sent them")
    args = ap.parse_args(argv)

    cfg = json.loads(Path(args.config).read_text())
    now = emailer.now_utc()
    state_path = Path(args.state)
    seen = {} if args.ignore_seen else load_seen(state_path)

    # 1. Fetch from every enabled source. One failing source never stops the run.
    all_jobs: list[Job] = []
    checked: list[str] = []
    notes: list[str] = []
    for key, (label, fetch) in SOURCES.items():
        if not cfg.get("sources", {}).get(key, True):
            notes.append(f"{label}: turned off in config.json")
            continue
        try:
            found = fetch(cfg)
        except MissingKey as e:
            notes.append(f"{label}: skipped, {e}")
            print(f"[{label}] skipped: {e}", file=sys.stderr)
            continue
        except Exception as e:  # noqa: BLE001 - report and keep going
            notes.append(f"{label}: error ({type(e).__name__})")
            print(f"[{label}] error: {e!r}", file=sys.stderr)
            continue
        all_jobs.extend(found)
        checked.append(label)
        print(f"[{label}] fetched {len(found)} listings", file=sys.stderr)

    # 2. Keep only matching, recent listings.
    default_hours = cfg.get("lookback_hours", 24)
    fresh = []
    for job in all_jobs:
        if not matches_keywords(job, cfg["keywords"]):
            continue
        hours = SOURCE_LOOKBACK_HOURS.get(job.source, default_hours)
        if job.posted_at and job.posted_at < now - timedelta(hours=hours):
            continue
        fresh.append(job)

    # 3. Merge duplicates across sites.
    unique = dedupe(fresh)

    # 4. Drop jobs an earlier digest already sent.
    jobs = [job for job in unique if not already_sent(job, seen)]
    print(f"{len(all_jobs)} fetched → {len(fresh)} match → {len(unique)} after dedupe "
          f"→ {len(jobs)} new", file=sys.stderr)

    # 5. Build the email.
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    jobs.sort(key=lambda j: j.posted_at or epoch, reverse=True)
    local_label = cfg["local_search"]["label"]
    sections = [
        (f"{local_label} area", [j for j in jobs if not j.remote]),
        ("Remote (US)", [j for j in jobs if j.remote]),
    ]
    notes.insert(0, f"Sources checked: {', '.join(checked) or 'none'}.")
    date_label = now.astimezone(LOCAL_TZ).strftime("%A, %B %-d, %Y")
    html_body = emailer.build_html(sections, date_label, notes, now)
    text_body = emailer.build_text(sections, date_label, notes, now)
    subject = cfg.get("email_subject", "Job digest: {count} new {jobs}").format(
        count=len(jobs), jobs="job" if len(jobs) == 1 else "jobs",
        date=now.astimezone(LOCAL_TZ).strftime("%b %-d"))

    if args.preview:
        Path(args.preview).write_text(html_body)
        print(f"Preview written to {args.preview}", file=sys.stderr)

    if args.dry_run:
        print(text_body)
        return 0

    # Email when an SMTP login is set up; otherwise post a GitHub issue,
    # which GitHub emails to the repo owner.
    if jobs or cfg.get("send_when_empty", True):
        if os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"):
            emailer.send(subject, html_body, text_body)
            print(f"Email sent: {subject}", file=sys.stderr)
        elif github_issue.available():
            md = emailer.build_markdown(sections, date_label, notes, now)
            url = github_issue.post(subject, md)
            print(f"GitHub issue posted: {url}", file=sys.stderr)
        else:
            print("Nowhere to deliver: set SMTP_USER and SMTP_PASSWORD "
                  "(or run inside GitHub Actions).", file=sys.stderr)
            return 1

    # 6. Remember what was sent (only after the email went out).
    stamp = now.isoformat()
    for job in jobs:
        seen[_job_key(job)] = stamp
        for u in job.merged_uids:
            seen[u] = stamp
    if not args.ignore_seen:
        save_seen(state_path, seen, now)
    return 0


if __name__ == "__main__":
    sys.exit(run())
