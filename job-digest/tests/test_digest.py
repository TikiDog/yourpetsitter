"""Run with:  python -m unittest discover -s tests   (from the job-digest folder)"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobdigest import digest, sources  # noqa: E402
from jobdigest.dedupe import (best_link, dedupe, normalize_company,  # noqa: E402
                              normalize_title)
from jobdigest.models import Job, Link  # noqa: E402

NOW = datetime.now(timezone.utc)


def job(title, company, source="X", links=(), hours_ago=2, **kw):
    return Job(source=source, source_id=f"{source}-{title}-{company}", title=title,
               company=company, location=kw.pop("location", "Remote"),
               remote=kw.pop("remote", True), posted_at=NOW - timedelta(hours=hours_ago),
               links=list(links), **kw)


class Normalize(unittest.TestCase):
    def test_company(self):
        self.assertEqual(normalize_company("Acme, Inc."), "acme")
        self.assertEqual(normalize_company("ACME Inc"), "acme")
        self.assertEqual(normalize_company("The Acme Co."), "acme")
        self.assertEqual(normalize_company("Smith & Jones LLC"), "smith and jones")

    def test_title(self):
        self.assertEqual(normalize_title("Sr. Graphic Designer (Remote)"), "senior graphic designer")
        self.assertEqual(normalize_title("Senior Graphic Designer - Remote"), "senior graphic designer")
        self.assertEqual(normalize_title("Senior Graphic Designer – Sacramento, CA"),
                         "senior graphic designer")
        self.assertEqual(normalize_title("UI/UX Designer"), "ui ux designer")
        self.assertEqual(normalize_title("E-Pub Production Specialist"),
                         "epub production specialist")


class Dedupe(unittest.TestCase):
    def test_same_job_on_three_sites_shows_once_with_company_link(self):
        jobs = [
            job("Graphic Designer", "Acme Inc", "Adzuna",
                [Link("https://www.adzuna.com/details/1", "Adzuna")]),
            job("Graphic Designer (Remote)", "ACME", "JSearch",
                [Link("https://www.linkedin.com/jobs/view/1", "LinkedIn"),
                 Link("https://boards.greenhouse.io/acme/jobs/1", "Acme Careers", direct=True)]),
            job("Graphic designer - Remote", "Acme, Inc.", "Remotive",
                [Link("https://remotive.com/remote-jobs/design/1", "Remotive")]),
        ]
        out = dedupe(jobs)
        self.assertEqual(len(out), 1)
        self.assertEqual(best_link(out[0]).url, "https://boards.greenhouse.io/acme/jobs/1")
        self.assertEqual(len(out[0].merged_uids), 3)

    def test_company_domain_beats_aggregator(self):
        j = job("Production Artist", "Bright Studio", links=[
            Link("https://www.indeed.com/viewjob?jk=1", "Indeed"),
            Link("https://careers.brightstudio.com/jobs/42", "Bright Studio")])
        self.assertEqual(best_link(j).label, "Bright Studio")

    def test_minor_wording_difference_is_duplicate(self):
        out = dedupe([job("Production Artist, Print & Digital", "Foo"),
                      job("Production Artist - Print and Digital", "Foo Inc.")])
        self.assertEqual(len(out), 1)

    def test_different_levels_are_not_duplicates(self):
        out = dedupe([job("Graphic Designer", "Foo"), job("Senior Graphic Designer", "Foo"),
                      job("Graphic Designer II", "Foo")])
        self.assertEqual(len(out), 3)

    def test_different_companies_are_not_duplicates(self):
        self.assertEqual(len(dedupe([job("UI Designer", "Foo"), job("UI Designer", "Bar")])), 2)


class Keywords(unittest.TestCase):
    KW = ["InDesign", "production artist", "graphic designer", "UI designer", "ePub"]

    def test_matches(self):
        self.assertTrue(digest.matches_keywords(job("Senior UI/UX Designer", "x"), self.KW))
        self.assertTrue(digest.matches_keywords(job("Graphic Design Specialist", "x"), self.KW))
        self.assertTrue(digest.matches_keywords(
            job("Layout Specialist", "x", description="Expert in Adobe InDesign required"),
            self.KW))
        self.assertTrue(digest.matches_keywords(
            job("Digital Publishing Associate", "x", description="Build EPUB files"), self.KW))

    def test_non_matches(self):
        self.assertFalse(digest.matches_keywords(
            job("Software Engineer", "x", description="Work with our graphic designer"),
            self.KW))
        self.assertFalse(digest.matches_keywords(job("Product Manager", "x"), self.KW))


class RemoteUS(unittest.TestCase):
    def test(self):
        for ok in ["USA Only", "Anywhere in the World", "US", "Americas", "North America", ""]:
            self.assertTrue(sources.remote_open_to_us(ok), ok)
        for bad in ["Europe", "UK", "Germany", "India", "EMEA"]:
            self.assertFalse(sources.remote_open_to_us(bad), bad)


# ---------------------------------------------------------------------------
# End-to-end with canned API responses
# ---------------------------------------------------------------------------

def iso(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def rfc822(hours_ago):
    return (NOW - timedelta(hours=hours_ago)).strftime("%a, %d %b %Y %H:%M:%S +0000")


JSEARCH = {"data": [
    {"job_id": "js1", "employer_name": "Penguin Random House", "job_title": "Production Artist",
     "job_city": "Sacramento", "job_state": "CA", "job_is_remote": False,
     "job_posted_at_timestamp": int((NOW - timedelta(hours=3)).timestamp()),
     "job_publisher": "LinkedIn", "job_apply_link": "https://www.linkedin.com/jobs/view/123",
     "apply_options": [
         {"publisher": "LinkedIn", "apply_link": "https://www.linkedin.com/jobs/view/123", "is_direct": False},
         {"publisher": "Penguin Random House Careers",
          "apply_link": "https://penguinrandomhouse.wd5.myworkdayjobs.com/job/1", "is_direct": True}],
     "job_min_salary": 60000, "job_max_salary": 75000, "job_salary_period": "YEAR",
     "job_description": "Prepare InDesign files for print and ePub."},
    {"job_id": "js-old", "employer_name": "Old Co", "job_title": "Graphic Designer",
     "job_is_remote": True,
     "job_posted_at_timestamp": int((NOW - timedelta(hours=40)).timestamp()),
     "job_apply_link": "https://www.indeed.com/viewjob?jk=old"},
]}

ADZUNA = {"results": [
    {"id": "az1", "title": "<strong>Production</strong> Artist",
     "company": {"display_name": "Penguin Random House LLC"},
     "location": {"display_name": "Sacramento, Sacramento County"},
     "redirect_url": "https://www.adzuna.com/land/ad/az1", "created": iso(5),
     "description": "InDesign expert"},
]}

REMOTIVE = {"jobs": [
    {"id": 77, "url": "https://remotive.com/remote-jobs/design/ui-designer-77",
     "title": "Senior UI Designer", "company_name": "Figment",
     "candidate_required_location": "USA", "publication_date": iso(30),
     "salary": "$120k - $140k", "description": "<p>Design systems work.</p>"},
    {"id": 78, "url": "https://remotive.com/remote-jobs/design/78", "title": "Graphic Designer",
     "company_name": "EuroCo", "candidate_required_location": "Europe",
     "publication_date": iso(3), "description": ""},
]}

WWR = f"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Figment: Sr. UI Designer</title><region>USA Only</region>
<link>https://weworkremotely.com/remote-jobs/figment-sr-ui-designer</link>
<guid>wwr1</guid><pubDate>{rfc822(4)}</pubDate><description>&lt;p&gt;UI&lt;/p&gt;</description></item>
<item><title>Bookish: Digital Publishing Associate</title><region>Anywhere in the World</region>
<link>https://weworkremotely.com/remote-jobs/bookish</link><guid>wwr2</guid>
<pubDate>{rfc822(6)}</pubDate><description>Convert manuscripts to EPUB 3.</description></item>
</channel></rss>"""

USAJOBS = {"SearchResult": {"SearchResultItems": [
    {"MatchedObjectId": "u1", "MatchedObjectDescriptor": {
        "PositionID": "DOI-1", "PositionTitle": "Visual Information Specialist (Graphic Designer)",
        "PositionURI": "https://www.usajobs.gov/job/1", "ApplyURI": ["https://www.usajobs.gov:443/job/1/apply"],
        "PositionLocationDisplay": "Sacramento, California", "OrganizationName": "Bureau of Reclamation",
        "PublicationStartDate": (NOW - timedelta(hours=20)).strftime("%Y-%m-%dT00:00:00.0000"),
        "PositionRemuneration": [{"MinimumRange": "72553", "MaximumRange": "94317", "Description": "Per Year"}],
    }}]}}


def fake_get(url, params=None, headers=None, **kw):
    if "jsearch" in url:
        body = JSEARCH if "Sacramento" in params["query"] else {"data": []}
    elif "adzuna" in url:
        body = ADZUNA if params.get("what_phrase") == "production artist" and "where" in params \
            else {"results": []}
    elif "remotive" in url:
        body = REMOTIVE
    elif "weworkremotely" in url:
        return WWR.encode()
    elif "usajobs" in url:
        body = USAJOBS if params["Keyword"] == "graphic designer" and "LocationName" in params \
            else {"SearchResult": {"SearchResultItems": []}}
    else:
        raise AssertionError(url)
    return json.dumps(body).encode()


ENV = {"RAPIDAPI_KEY": "k", "ADZUNA_APP_ID": "i", "ADZUNA_APP_KEY": "k",
       "USAJOBS_API_KEY": "k", "USAJOBS_EMAIL": "me@example.com",
       "SMTP_USER": "me@example.com", "SMTP_PASSWORD": "pw"}


class EndToEnd(unittest.TestCase):
    def run_digest(self, state, *extra):
        sent = []
        with mock.patch.dict(os.environ, ENV), \
                mock.patch("jobdigest.http.get", side_effect=fake_get), \
                mock.patch("jobdigest.emailer.send", side_effect=lambda *a: sent.append(a)):
            digest.run(["--state", state, *extra])
        return sent

    def test_full_run_then_nothing_new(self):
        with tempfile.TemporaryDirectory() as d:
            state = f"{d}/seen.json"
            sent = self.run_digest(state)
            self.assertEqual(len(sent), 1)
            subject, html_body, text = sent[0]

            # PRH production artist from JSearch + Adzuna → once, Workday link.
            self.assertEqual(text.count("Production Artist"), 1)
            self.assertIn("myworkdayjobs.com", text)
            # Figment UI designer from Remotive + WWR → once.
            self.assertEqual(text.count("UI Designer"), 1)
            self.assertIn("Digital Publishing Associate", text)  # EPUB in description
            self.assertIn("Visual Information Specialist", text)  # USAJOBS
            self.assertNotIn("Old Co", text)  # older than 24h
            self.assertNotIn("EuroCo", text)  # not open to US
            self.assertIn("4 new jobs", subject)

            # Second run the same day: everything was already sent.
            sent = self.run_digest(state)
            self.assertIn("0 new jobs", sent[0][0])

    def test_missing_keys_skip_source_but_still_send(self):
        with tempfile.TemporaryDirectory() as d:
            env = {k: v for k, v in ENV.items() if not k.startswith(("RAPIDAPI", "ADZUNA"))}
            sent = []
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch("jobdigest.http.get", side_effect=fake_get), \
                    mock.patch("jobdigest.emailer.send", side_effect=lambda *a: sent.append(a)):
                digest.run(["--state", f"{d}/seen.json"])
            text = sent[0][2]
            self.assertIn("JSearch: skipped", text)
            self.assertIn("Adzuna: skipped", text)
            self.assertIn("UI Designer", text)


if __name__ == "__main__":
    unittest.main()
