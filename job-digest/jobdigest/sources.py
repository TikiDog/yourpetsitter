"""One fetch function per job source.

Every function takes the loaded config and returns a list of Job objects.
They only use official APIs and RSS feeds -- nothing here scrapes a website.

If a source needs an API key and the key isn't set, the function raises
MissingKey and the run carries on with the other sources.
"""

from __future__ import annotations

import html
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import http
from .models import Job, Link


class MissingKey(Exception):
    pass


def _env(*names: str) -> list[str]:
    values = [os.environ.get(n, "").strip() for n in names]
    missing = [n for n, v in zip(names, values) if not v]
    if missing:
        raise MissingKey(f"not configured (set {', '.join(missing)})")
    return values


_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", text or ""))).strip()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip().replace("Z", "+00:00")
    # USAJobs sends 7-digit fractional seconds, which fromisoformat rejects
    # on older Pythons; trim to microseconds.
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _salary(lo, hi, period: str = "") -> str:
    def fmt(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return f"${v:,.0f}" if v >= 1000 else f"${v:,.2f}"

    lo, hi = fmt(lo), fmt(hi)
    if not lo and not hi:
        return ""
    text = f"{lo}–{hi}" if lo and hi and lo != hi else (lo or hi)
    period = re.sub(r"^per\s+", "", period.strip().lower())
    return f"{text} / {period}" if period else text


# Text that means a remote job is open to people living in the US.
_US_OK = re.compile(
    r"\b(usa|u\.s\.a?\.?|united states|americas?|north america|"
    r"anywhere|worldwide|world|global|remote)\b|\bUS\b",
    re.IGNORECASE,
)


def remote_open_to_us(location_text: str) -> bool:
    text = (location_text or "").strip()
    if not text:
        return True
    return bool(_US_OK.search(text))


def _local_label(cfg) -> str:
    return cfg["local_search"]["label"]


def _miles_to_km(miles: float) -> int:
    return round(miles * 1.609)


# --------------------------------------------------------------------------
# JSearch (RapidAPI) -- Google for Jobs: LinkedIn, Indeed, Glassdoor,
# ZipRecruiter, company career sites and more.
# --------------------------------------------------------------------------

def fetch_jsearch(cfg) -> list[Job]:
    (key,) = _env("RAPIDAPI_KEY")
    headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
    local = cfg["local_search"]

    # The free plan has a monthly request cap, so by default all keywords
    # go into one OR query per location (2 requests a day).
    if cfg.get("jsearch_one_query_per_keyword"):
        terms = [f'"{k}"' for k in cfg["keywords"]]
    else:
        terms = [" OR ".join(f'"{k}"' for k in cfg["keywords"])]

    searches = []
    for t in terms:
        searches.append(({"query": f"{t} in {local['city']}, {local['state']}",
                          "radius": _miles_to_km(local["radius_miles"])}, False))
        if cfg.get("include_remote_us", True):
            searches.append(({"query": f"{t} remote", "work_from_home": "true",
                              "remote_jobs_only": "true"}, True))

    jobs = []
    for params, remote_search in searches:
        params.update({"page": 1, "num_pages": 1, "date_posted": "today", "country": "us"})
        data = http.get_json("https://jsearch.p.rapidapi.com/search", params, headers)
        for r in data.get("data") or []:
            links = []
            for opt in r.get("apply_options") or []:
                if opt.get("apply_link"):
                    links.append(Link(opt["apply_link"], opt.get("publisher") or "Apply",
                                      bool(opt.get("is_direct"))))
            if r.get("job_apply_link") and not any(l.url == r["job_apply_link"] for l in links):
                links.append(Link(r["job_apply_link"], r.get("job_publisher") or "Apply",
                                  bool(r.get("job_apply_is_direct"))))
            if r.get("job_google_link"):
                links.append(Link(r["job_google_link"], "Google Jobs"))

            remote = bool(r.get("job_is_remote")) or remote_search
            place = ", ".join(p for p in (r.get("job_city"), r.get("job_state")) if p)
            ts = r.get("job_posted_at_timestamp")
            posted = (datetime.fromtimestamp(ts, timezone.utc) if ts
                      else _parse_iso(r.get("job_posted_at_datetime_utc")))
            jobs.append(Job(
                source="JSearch",
                source_id=str(r.get("job_id")),
                title=r.get("job_title") or "",
                company=r.get("employer_name") or "",
                location="Remote (US)" if remote and not place else
                         (f"Remote · {place}" if remote else place or _local_label(cfg)),
                remote=remote,
                posted_at=posted,
                links=links,
                salary=_salary(r.get("job_min_salary"), r.get("job_max_salary"),
                               r.get("job_salary_period") or ""),
                description=strip_html(r.get("job_description") or ""),
            ))
    return jobs


# --------------------------------------------------------------------------
# Adzuna
# --------------------------------------------------------------------------

def fetch_adzuna(cfg) -> list[Job]:
    app_id, app_key = _env("ADZUNA_APP_ID", "ADZUNA_APP_KEY")
    local = cfg["local_search"]
    base = {"app_id": app_id, "app_key": app_key, "results_per_page": 50,
            "max_days_old": 1, "sort_by": "date", "content-type": "application/json"}

    searches = []
    for kw in cfg["keywords"]:
        searches.append(({"what_phrase": kw, "where": f"{local['city']}, {local['state']}",
                          "distance": _miles_to_km(local["radius_miles"])}, False))
        if cfg.get("include_remote_us", True):
            searches.append(({"what_phrase": kw, "what_and": "remote"}, True))

    jobs = []
    for params, remote_search in searches:
        data = http.get_json("https://api.adzuna.com/v1/api/jobs/us/search/1", {**base, **params})
        for r in data.get("results") or []:
            place = (r.get("location") or {}).get("display_name") or ""
            jobs.append(Job(
                source="Adzuna",
                source_id=str(r.get("id")),
                title=strip_html(r.get("title") or ""),
                company=strip_html((r.get("company") or {}).get("display_name") or ""),
                location=f"Remote · {place}" if remote_search and place else
                         ("Remote (US)" if remote_search else place),
                remote=remote_search,
                posted_at=_parse_iso(r.get("created")),
                links=[Link(r["redirect_url"], "Adzuna")] if r.get("redirect_url") else [],
                salary=_salary(r.get("salary_min"), r.get("salary_max"), "year"
                               if r.get("salary_min") else ""),
                description=strip_html(r.get("description") or ""),
            ))
    return jobs


# --------------------------------------------------------------------------
# Remotive -- remote only, no key needed. Their API terms ask for at most a
# few calls a day and a link back to Remotive, which the digest keeps.
# Remotive's public API shows listings about 24 hours after they are posted,
# which is why this source uses a longer lookback (see digest.py).
# --------------------------------------------------------------------------

def fetch_remotive(cfg) -> list[Job]:
    if not cfg.get("include_remote_us", True):
        return []
    data = http.get_json("https://remotive.com/api/remote-jobs")
    jobs = []
    for r in data.get("jobs") or []:
        where = r.get("candidate_required_location") or ""
        if not remote_open_to_us(where):
            continue
        jobs.append(Job(
            source="Remotive",
            source_id=str(r.get("id")),
            title=r.get("title") or "",
            company=r.get("company_name") or "",
            location=f"Remote · {where}" if where else "Remote",
            remote=True,
            posted_at=_parse_iso(r.get("publication_date")),
            links=[Link(r["url"], "Remotive")] if r.get("url") else [],
            salary=r.get("salary") or "",
            description=strip_html(r.get("description") or ""),
        ))
    return jobs


# --------------------------------------------------------------------------
# We Work Remotely -- design category RSS feed, no key needed.
# --------------------------------------------------------------------------

WWR_FEEDS = [
    "https://weworkremotely.com/categories/remote-design-jobs.rss",
]


def fetch_weworkremotely(cfg) -> list[Job]:
    if not cfg.get("include_remote_us", True):
        return []
    jobs = []
    for feed in WWR_FEEDS:
        root = ET.fromstring(http.get(feed))
        for item in root.iter("item"):
            def text(tag):
                el = item.find(tag)
                return (el.text or "").strip() if el is not None else ""

            region = text("region")
            if not remote_open_to_us(region):
                continue
            # Titles look like "Acme Co: Senior Graphic Designer".
            raw = text("title")
            company, _, title = raw.partition(": ")
            if not title:
                company, title = "", raw
            try:
                posted = parsedate_to_datetime(text("pubDate")).astimezone(timezone.utc)
            except (TypeError, ValueError):
                posted = None
            link = text("link")
            jobs.append(Job(
                source="We Work Remotely",
                source_id=text("guid") or link,
                title=title,
                company=company,
                location=f"Remote · {region}" if region else "Remote",
                remote=True,
                posted_at=posted,
                links=[Link(link, "We Work Remotely")] if link else [],
                description=strip_html(text("description")),
            ))
    return jobs


# --------------------------------------------------------------------------
# USAJOBS -- official federal jobs API.
# --------------------------------------------------------------------------

def fetch_usajobs(cfg) -> list[Job]:
    key, email = _env("USAJOBS_API_KEY", "USAJOBS_EMAIL")
    headers = {"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key}
    local = cfg["local_search"]

    searches = []
    for kw in cfg["keywords"]:
        searches.append(({"Keyword": kw,
                          "LocationName": f"{local['city']}, {local['state_name']}",
                          "Radius": local["radius_miles"]}, False))
        if cfg.get("include_remote_us", True):
            searches.append(({"Keyword": kw, "RemoteIndicator": "True"}, True))

    jobs = []
    for params, remote_search in searches:
        params.update({"DatePosted": 1, "ResultsPerPage": 100})
        data = http.get_json("https://data.usajobs.gov/api/search", params, headers)
        items = ((data.get("SearchResult") or {}).get("SearchResultItems")) or []
        for it in items:
            d = it.get("MatchedObjectDescriptor") or {}
            pay = (d.get("PositionRemuneration") or [{}])[0]
            url = d.get("PositionURI") or (d.get("ApplyURI") or [None])[0]
            links = [Link(url, "USAJOBS", direct=True)] if url else []
            place = d.get("PositionLocationDisplay") or ""
            jobs.append(Job(
                source="USAJOBS",
                source_id=str(d.get("PositionID") or it.get("MatchedObjectId")),
                title=d.get("PositionTitle") or "",
                company=d.get("OrganizationName") or d.get("DepartmentName") or "",
                location=f"Remote · {place}" if remote_search else place,
                remote=remote_search,
                posted_at=_parse_iso(d.get("PublicationStartDate")),
                links=links,
                salary=_salary(pay.get("MinimumRange"), pay.get("MaximumRange"),
                               pay.get("Description") or ""),
                description=strip_html((d.get("UserArea") or {}).get("Details", {})
                                       .get("JobSummary") or d.get("QualificationSummary") or ""),
            ))
    return jobs


SOURCES = {
    "jsearch": ("JSearch", fetch_jsearch),
    "adzuna": ("Adzuna", fetch_adzuna),
    "remotive": ("Remotive", fetch_remotive),
    "weworkremotely": ("We Work Remotely", fetch_weworkremotely),
    "usajobs": ("USAJOBS", fetch_usajobs),
}
