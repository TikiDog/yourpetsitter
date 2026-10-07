"""Builds the digest email (HTML + plain text) and sends it over SMTP."""

from __future__ import annotations

import html
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage

from .dedupe import best_link, is_company_page, link_score
from .models import Job

# Palette and type kept to inline styles: most mail clients ignore <style>.
INK = "#1d1d1f"
MUTED = "#6e6e73"
LINE = "#e5e5ea"
ACCENT = "#0b5cad"
BG = "#f5f5f7"
CARD = "#ffffff"
BADGE_BG = "#e6f4ea"
BADGE_INK = "#1e6b34"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _ago(dt: datetime | None, now: datetime) -> str:
    if not dt:
        return "Date not listed"
    hours = int((now - dt).total_seconds() // 3600)
    if hours < 1:
        return "Just posted"
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


def _snippet(text: str, limit: int = 180) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + "…"


def _other_links(job: Job, primary) -> list:
    others = [l for l in job.links if l is not primary]
    seen, out = set(), []
    for l in sorted(others, key=lambda l: -link_score(l, job.company)):
        if l.label.lower() not in seen:
            seen.add(l.label.lower())
            out.append(l)
    return out[:4]


def _card_html(job: Job, now: datetime) -> str:
    e = html.escape
    primary = best_link(job)
    title = e(job.title)
    if primary:
        title = (f'<a href="{e(primary.url)}" style="color:{ACCENT};text-decoration:none;">'
                 f"{title}</a>")

    meta = " &nbsp;·&nbsp; ".join(e(p) for p in
                                 (job.company or "Company not listed", job.location,
                                  _ago(job.posted_at, now)) if p)

    badge = ""
    if is_company_page(job):
        badge = (f'<span style="display:inline-block;margin-left:6px;padding:1px 7px;'
                 f'border-radius:10px;background:{BADGE_BG};color:{BADGE_INK};'
                 f'font-size:11px;font-weight:600;vertical-align:middle;">Company site</span>')

    salary = (f'<div style="margin-top:4px;font-size:13px;color:{INK};">{e(job.salary)}</div>'
              if job.salary else "")
    snippet = (f'<div style="margin-top:8px;font-size:13px;line-height:1.45;color:{MUTED};">'
               f"{e(_snippet(job.description))}</div>" if job.description else "")

    others = _other_links(job, primary)
    via = ""
    if others:
        links = ", ".join(f'<a href="{e(l.url)}" style="color:{MUTED};">{e(l.label)}</a>'
                          for l in others)
        via = f'<div style="margin-top:8px;font-size:12px;color:{MUTED};">Also listed on: {links}</div>'

    return f"""
<tr><td style="padding:0 0 12px 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:{CARD};border:1px solid {LINE};border-radius:10px;">
    <tr><td style="padding:16px 18px;font-family:{FONT};">
      <div style="font-size:16px;font-weight:600;line-height:1.3;">{title}{badge}</div>
      <div style="margin-top:4px;font-size:13px;color:{MUTED};">{meta}</div>
      {salary}{snippet}{via}
    </td></tr>
  </table>
</td></tr>"""


def _section_html(heading: str, jobs: list[Job], now: datetime) -> str:
    if not jobs:
        return ""
    cards = "".join(_card_html(j, now) for j in jobs)
    return f"""
<tr><td style="padding:20px 0 10px 0;font-family:{FONT};font-size:13px;font-weight:700;
               letter-spacing:.06em;text-transform:uppercase;color:{MUTED};">
  {html.escape(heading)} <span style="font-weight:400;">({len(jobs)})</span>
</td></tr>{cards}"""


def build_html(sections: list[tuple[str, list[Job]]], date_label: str,
               source_notes: list[str], now: datetime) -> str:
    total = sum(len(j) for _, j in sections)
    body = "".join(_section_html(h, j, now) for h, j in sections)
    if not total:
        body = (f'<tr><td style="padding:24px 0;font-family:{FONT};font-size:15px;color:{MUTED};">'
                "No new matching jobs since the last digest.</td></tr>")
    notes = "<br>".join(html.escape(n) for n in source_notes)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta name="color-scheme" content="light"><title>Job digest</title></head>
<body style="margin:0;padding:0;background:{BG};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{BG};">
<tr><td align="center" style="padding:24px 12px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:640px;">
    <tr><td style="font-family:{FONT};color:{INK};">
      <div style="font-size:24px;font-weight:700;">Job digest</div>
      <div style="margin-top:2px;font-size:14px;color:{MUTED};">
        {html.escape(date_label)} &nbsp;·&nbsp; {total} new {"job" if total == 1 else "jobs"}
      </div>
    </td></tr>
    {body}
    <tr><td style="padding:16px 0 0 0;border-top:1px solid {LINE};font-family:{FONT};
                   font-size:11px;line-height:1.6;color:{MUTED};">
      {notes}<br>Remote listings from Remotive and We Work Remotely link back to those sites.
    </td></tr>
  </table>
</td></tr></table></body></html>"""


def build_text(sections: list[tuple[str, list[Job]]], date_label: str,
               source_notes: list[str], now: datetime) -> str:
    lines = [f"Job digest — {date_label}", ""]
    for heading, jobs in sections:
        if not jobs:
            continue
        lines += [f"== {heading} ({len(jobs)}) ==", ""]
        for j in jobs:
            link = best_link(j)
            tag = " [company site]" if is_company_page(j) else ""
            lines.append(f"{j.title}{tag}")
            lines.append(f"  {j.company or 'Company not listed'} · {j.location} · "
                         f"{_ago(j.posted_at, now)}")
            if j.salary:
                lines.append(f"  {j.salary}")
            if link:
                lines.append(f"  {link.url}")
            lines.append("")
    if not any(jobs for _, jobs in sections):
        lines += ["No new matching jobs since the last digest.", ""]
    lines += ["--", *source_notes]
    return "\n".join(lines)


def send(subject: str, html_body: str, text_body: str) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]
    to = os.environ.get("EMAIL_TO") or user

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ.get("EMAIL_FROM") or user
    msg["To"] = to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as s:
            s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=context)
            s.login(user, password)
            s.send_message(msg)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
