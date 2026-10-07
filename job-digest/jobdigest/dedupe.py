"""Duplicate detection and "best link" selection.

Two listings are the same job when the companies match and the titles match
after normalizing case, punctuation, common abbreviations and noise words
like "(Remote)" or "- Sacramento, CA". Small wording differences are
tolerated with a token-overlap check.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlparse

from .models import Job, Link

_COMPANY_SUFFIXES = {
    "inc", "incorporated", "llc", "l", "c", "ltd", "limited", "co", "corp",
    "corporation", "company", "plc", "lp", "llp", "pllc", "pc", "the",
}

# Applied to titles before punctuation is stripped.
_TITLE_PHRASES = [
    (r"\bui\s*/\s*ux\b|\bux\s*/\s*ui\b", "ui ux"),
    (r"\buser interface\b", "ui"),
    (r"\buser experience\b", "ux"),
    (r"\be-?\s?pub\b", "epub"),
    (r"\bin-?\s?design\b", "indesign"),
    (r"\bgraphics designer\b", "graphic designer"),
    (r"\bfull[\s-]?time\b|\bpart[\s-]?time\b", " "),
    (r"&", " and "),
]

_TITLE_ABBREVIATIONS = {
    "sr": "senior", "jr": "junior", "snr": "senior", "mgr": "manager",
    "assoc": "associate", "asst": "assistant", "dsgnr": "designer",
    "prod": "production", "coord": "coordinator", "spec": "specialist",
}

# Words that describe the arrangement or location, not the job itself.
_TITLE_NOISE = {
    "remote", "hybrid", "onsite", "on", "site", "in", "office", "contract",
    "contractor", "temporary", "temp", "freelance", "w2", "1099", "position",
    "opening", "opportunity", "job", "usa", "us", "ca", "california",
    "sacramento", "anywhere", "wfh", "work", "from", "home", "the", "a", "an",
}

_SEGMENT_NOISE = re.compile(
    r"^(remote|hybrid|on-?site|contract|temporary|temp|freelance|full[\s-]?time|"
    r"part[\s-]?time|us|usa|united states|[a-z .]+,\s*[a-z]{2}|work from home)"
    r"([\s/,&-]+(remote|hybrid|on-?site|contract|us|usa|[a-z .]+,\s*[a-z]{2}))*$",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text.lower())).strip()


def normalize_company(name: str) -> str:
    text = _clean((name or "").replace("&", " and "))
    words = [w for w in text.split() if w not in _COMPANY_SUFFIXES]
    return " ".join(words)


def normalize_title(title: str) -> str:
    text = (title or "").lower()
    # Drop (Remote), [Hybrid], etc.
    text = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", " ", text)
    # Drop trailing " - Remote" / " | Sacramento, CA" style segments.
    parts = re.split(r"\s+[-–—|]\s+", text)
    while len(parts) > 1 and _SEGMENT_NOISE.match(parts[-1].strip()):
        parts.pop()
    text = " - ".join(parts)
    for pattern, repl in _TITLE_PHRASES:
        text = re.sub(pattern, repl, text)
    words = [_TITLE_ABBREVIATIONS.get(w, w) for w in _clean(text).split()]
    return " ".join(w for w in words if w not in _TITLE_NOISE)


def _same_company(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a == b or SequenceMatcher(None, a, b).ratio() >= 0.9


# If two titles differ by one of these, they are different jobs
# ("Graphic Designer" vs "Senior Graphic Designer" or "Graphic Designer II").
_LEVEL_WORDS = {
    "senior", "junior", "lead", "principal", "staff", "associate", "intern",
    "internship", "manager", "director", "head", "chief", "entry", "level",
    "i", "ii", "iii", "iv", "v", "1", "2", "3", "4", "5",
}


def _same_title(a: str, b: str) -> bool:
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb or (ta ^ tb) & _LEVEL_WORDS:
        return False
    overlap = len(ta & tb) / len(ta | tb)
    return overlap >= 0.75 or SequenceMatcher(None, a, b).ratio() >= 0.88


# ---------------------------------------------------------------------------
# Link ranking: the employer's own careers page beats an aggregator.
# ---------------------------------------------------------------------------

# Applicant-tracking systems host companies' own careers pages.
_ATS_DOMAINS = (
    "greenhouse.io", "lever.co", "myworkdayjobs.com", "myworkdaysite.com",
    "workday.com", "ashbyhq.com", "smartrecruiters.com", "icims.com",
    "jobvite.com", "bamboohr.com", "breezy.hr", "workable.com",
    "recruitee.com", "applytojob.com", "taleo.net", "successfactors.com",
    "paylocity.com", "ultipro.com", "ukg.com", "adp.com", "dayforcehcm.com",
    "paycomonline.net", "rippling.com", "teamtailor.com", "pinpointhq.com",
    "jazzhr.com", "personio.com", "usajobs.gov", "calcareers.ca.gov",
)

_AGGREGATOR_DOMAINS = (
    "linkedin.com", "indeed.com", "glassdoor.com", "ziprecruiter.com",
    "adzuna.com", "remotive.com", "weworkremotely.com", "monster.com",
    "simplyhired.com", "talent.com", "careerbuilder.com", "jooble.org",
    "lensa.com", "jobright.ai", "snagajob.com", "dice.com", "google.com",
    "salary.com", "builtin.com", "learn4good.com", "jobleads.com",
    "bebee.com", "recruit.net", "tallo.com", "whatjobs.com", "jobgether.com",
    "himalayas.app", "flexjobs.com", "wellfound.com", "dailyremote.com",
    "careerjet.com", "getwork.com", "jobilize.com", "workingnomads.com",
)


def _host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _matches(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def link_score(link: Link, company: str = "") -> int:
    """3 = employer's own page, 2 = probably employer, 1 = unknown, 0 = aggregator."""
    host = _host(link.url)
    if _matches(host, _AGGREGATOR_DOMAINS):
        return 0
    if link.direct or _matches(host, _ATS_DOMAINS):
        return 3
    slug = normalize_company(company).replace(" ", "")
    if slug and len(slug) >= 4 and slug in host.replace("-", "").replace(".", ""):
        return 3
    path = urlparse(link.url).path.lower()
    if host.startswith(("careers.", "jobs.")) or "/careers" in path or "/jobs" in path:
        return 2
    return 1


def best_link(job: Job) -> Link | None:
    if not job.links:
        return None
    # max() keeps the first of equal scores, so the source's own order breaks ties.
    return max(job.links, key=lambda l: link_score(l, job.company))


def is_company_page(job: Job) -> bool:
    link = best_link(job)
    return bool(link) and link_score(link, job.company) >= 2


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------

def _merge(group: list[Job]) -> Job:
    # Start from the listing with the best link; fill gaps from the rest.
    group = sorted(group, key=lambda j: -max((link_score(l, j.company) for l in j.links),
                                            default=-1))
    keep = group[0]
    seen_urls = {l.url for l in keep.links}
    for other in group[1:]:
        for l in other.links:
            if l.url not in seen_urls:
                keep.links.append(l)
                seen_urls.add(l.url)
        keep.salary = keep.salary or other.salary
        keep.description = keep.description or other.description
        keep.remote = keep.remote or other.remote
        if other.posted_at and (not keep.posted_at or other.posted_at < keep.posted_at):
            keep.posted_at = other.posted_at
    keep.also_on = sorted({j.source for j in group} - {keep.source})
    keep.merged_uids = [j.uid for j in group]
    return keep


def dedupe(jobs: list[Job]) -> list[Job]:
    groups: list[tuple[str, str, list[Job]]] = []  # (company key, title key, jobs)
    for job in jobs:
        ck, tk = normalize_company(job.company), normalize_title(job.title)
        for g_ck, g_tk, members in groups:
            if _same_company(ck, g_ck) and _same_title(tk, g_tk):
                members.append(job)
                break
        else:
            groups.append((ck, tk, [job]))
    return [_merge(members) for _, _, members in groups]
