#!/usr/bin/env python3
"""
jobscout: pull job postings from company career boards, filter them for
target roles/locations, and store them in SQLite so each run surfaces only
new matches.

Supported boards: Greenhouse, Lever, Ashby (official public APIs) and
Workday (the JSON endpoint its career sites call).

Usage:
  python jobscout.py run              # fetch, filter, save, export new matches to CSV
  python jobscout.py check            # verify every company in companies.toml resolves
  python jobscout.py score [--limit N] [--rescore] [--id JOB_ID]
                                      # rate new jobs against your resumes with Claude
  python jobscout.py report [--open]  # write output/jobs.html (run also refreshes it)
  python jobscout.py digest [--dry-run]   # email new jobs you haven't been sent yet
  python jobscout.py daily            # run + digest; what the 7 AM schedule calls
  python jobscout.py list [--status new|applied|skipped|all] [--min-score N]
  python jobscout.py mark <job_id> <status>   # e.g. applied, skipped, interviewing
"""
import argparse
import csv
import html
import json
import os
import re
import smtplib
import sqlite3
import sys
import time
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from email.message import EmailMessage
from html import escape
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # pip install tomli

import requests

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "companies.toml"
DB = HERE / "jobs.db"
OUT_DIR = HERE / "output"
RESUME_DIR = HERE / "resumes"
REPORT_TEMPLATE = HERE / "report_template.html"
REPORT = OUT_DIR / "jobs.html"
DIGEST_PREVIEW = OUT_DIR / "digest.html"
ENV_FILE = HERE / ".env"
UA = {"User-Agent": "jobscout/1.0 (personal job search)"}
TIMEOUT = 30


# ---------------------------------------------------------------- helpers
def strip_html(text):
    if not text:
        return ""
    text = html.unescape(text)          # Greenhouse double-encodes HTML
    text = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def job(company, board, job_id, title, location, url, posted="", description=""):
    return {
        "id": f"{board}:{company}:{job_id}",
        "company": company,
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": url,
        "posted": posted or "",
        "description": description or "",
    }


# ---------------------------------------------------------------- fetchers
def fetch_greenhouse(c):
    url = f"https://boards-api.greenhouse.io/v1/boards/{c['token']}/jobs?content=true"
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return [
        job(c["name"], "greenhouse", j["id"], j.get("title"),
            (j.get("location") or {}).get("name", ""), j.get("absolute_url"),
            j.get("updated_at", ""), strip_html(j.get("content", "")))
        for j in r.json().get("jobs", [])
    ]


def fetch_lever(c):
    url = f"https://api.lever.co/v0/postings/{c['token']}?mode=json"
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        cats = j.get("categories") or {}
        loc = cats.get("location", "")
        if j.get("workplaceType") == "remote" and "remote" not in loc.lower():
            loc = f"{loc} (Remote)".strip()
        posted = ""
        if j.get("createdAt"):
            posted = datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc).date().isoformat()
        desc = j.get("descriptionPlain", "") + "\n\n" + "\n".join(
            f"{l.get('text', '')}\n{strip_html(l.get('content', ''))}" for l in j.get("lists", []))
        out.append(job(c["name"], "lever", j["id"], j.get("text"), loc,
                       j.get("hostedUrl"), posted, desc.strip()))
    return out


def fetch_ashby(c):
    url = f"https://api.ashbyhq.com/posting-api/job-board/{c['token']}?includeCompensation=true"
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        locs = [j.get("location", "")] + [
            s.get("location", "") for s in j.get("secondaryLocations", []) or []]
        loc = " / ".join(l for l in locs if l)
        if j.get("isRemote") and "remote" not in loc.lower():
            loc = f"{loc} (Remote)".strip()
        out.append(job(c["name"], "ashby", j.get("id"), j.get("title"), loc,
                       j.get("jobUrl"), j.get("publishedAt", ""),
                       j.get("descriptionPlain", "")))
    return out


def fetch_workday(c, search_terms):
    """Workday career sites call a JSON endpoint. We search with each term
    (Workday returns everything otherwise) and page through results."""
    base = f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}"
    seen, out = set(), []
    for term in search_terms:
        offset = 0
        while offset < 200:  # cap per term
            body = {"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term}
            r = requests.post(f"{base}/jobs", json=body, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            postings = r.json().get("jobPostings", [])
            if not postings:
                break
            for p in postings:
                path = p.get("externalPath", "")
                if path in seen:
                    continue
                seen.add(path)
                out.append(job(c["name"], "workday", path.rsplit("_", 1)[-1] or path,
                               p.get("title"), p.get("locationsText", ""),
                               f"https://{c['host']}/{c['site']}{path}",
                               p.get("postedOn", "")) | {"_wd_path": path})
            offset += 20
            time.sleep(0.5)
    return out


def workday_description(c, path):
    url = f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}{path}"
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        info = r.json().get("jobPostingInfo", {})
        return strip_html(info.get("jobDescription", "")), info.get("location", "")
    except Exception:
        return "", ""


def fetch(c, filters):
    board = c["ats"]
    if board == "greenhouse":
        return fetch_greenhouse(c)
    if board == "lever":
        return fetch_lever(c)
    if board == "ashby":
        return fetch_ashby(c)
    if board == "workday":
        return fetch_workday(c, filters.get("workday_search", ["engineering manager"]))
    raise ValueError(f"unknown ats '{board}' for {c['name']}")


# ---------------------------------------------------------------- filtering
def compile_filters(f):
    rx = lambda pats: [re.compile(p, re.I) for p in pats]
    return {
        "include": rx(f["title_include"]),
        "exclude": rx(f.get("title_exclude", [])),
        "locations": [l.lower() for l in f.get("locations", [])],
        "allow_remote": f.get("allow_remote", True),
        "remote_exclude": [l.lower() for l in f.get("remote_exclude", [])],
        "keep_unknown_location": f.get("keep_unknown_location", True),
    }


def title_ok(title, cf):
    return any(p.search(title) for p in cf["include"]) and not any(
        p.search(title) for p in cf["exclude"])


def location_ok(loc, cf):
    l = loc.lower()
    if not l or re.search(r"\d+ locations", l):
        return cf["keep_unknown_location"]  # ambiguous: review by hand
    if any(k in l for k in cf["locations"]):
        return True
    if cf["allow_remote"] and "remote" in l:
        return not any(x in l for x in cf["remote_exclude"])
    return False


# ---------------------------------------------------------------- storage
SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, company TEXT, title TEXT, location TEXT, url TEXT,
  posted TEXT, description TEXT, first_seen TEXT, last_seen TEXT,
  status TEXT DEFAULT 'new', score INTEGER, notes TEXT
);
"""
# Columns added after the first release; db() adds any that are missing.
EXTRA_COLUMNS = {"resume": "TEXT", "matches": "TEXT", "gaps": "TEXT",
                 "summary": "TEXT", "scored_at": "TEXT", "emailed_at": "TEXT"}


def db():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    have = {row[1] for row in con.execute("PRAGMA table_info(jobs)")}
    for col, typ in EXTRA_COLUMNS.items():
        if col not in have:
            con.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")
    return con


def upsert(con, j, now):
    cur = con.execute("SELECT 1 FROM jobs WHERE id = ?", (j["id"],))
    if cur.fetchone():
        con.execute("UPDATE jobs SET last_seen = ? WHERE id = ?", (now, j["id"]))
        return False
    con.execute(
        "INSERT INTO jobs (id, company, title, location, url, posted, description, first_seen, last_seen)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (j["id"], j["company"], j["title"], j["location"], j["url"], j["posted"],
         j["description"], now, now))
    return True


# ---------------------------------------------------------------- scoring
DEFAULT_MODEL = "claude-opus-5"
W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

SCORING_INSTRUCTIONS = """You screen job postings for one candidate. You are given the
candidate's resumes (several variants of the same person, tailored to different role
types) and one job posting. Judge how well the candidate fits the job as posted.

Return:
- resume: the name of the resume variant that fits this job better.
- score: 0-100 overall fit. 85+ = strong match worth applying to today; 70-84 = good
  match with minor gaps; 50-69 = stretch; below 50 = poor fit. Weigh level/scope
  (manager vs IC, team size, seniority), domain (platform, infrastructure, developer
  experience, AI/ML platform), must-have requirements, and location/remote fit against
  the candidate's preferences. A hard blocker (wrong location with no remote option,
  required clearance, IC-only role) should pull the score well below 50.
- matches: 3-5 short bullets, the strongest evidence from the chosen resume for this job.
- gaps: 0-4 short bullets, requirements the resume doesn't show. Empty if none.
- summary: a 2-3 sentence professional summary for the top of the chosen resume,
  tailored to this job, using only facts from the resume. No invented numbers."""


def resume_text(path):
    if path.suffix.lower() == ".docx":
        root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
        paras = ["".join(t.text or "" for t in p.iter(W_NS + "t")) for p in root.iter(W_NS + "p")]
        return "\n".join(p for p in paras if p.strip())
    return path.read_text(encoding="utf-8")


def load_resumes():
    files = sorted(p for p in RESUME_DIR.glob("*")
                   if p.suffix.lower() in (".docx", ".md", ".txt"))
    if not files:
        sys.exit(f"No resumes found. Put .docx, .md, or .txt resumes in {RESUME_DIR}")
    return {p.stem: resume_text(p) for p in files}


def scoring_system(resumes, profile):
    """System prompt: instructions + every resume. Identical for every job so it
    stays in the prompt cache across a scoring run."""
    parts = [SCORING_INSTRUCTIONS]
    if profile:
        parts.append(f"<candidate_preferences>\n{profile.strip()}\n</candidate_preferences>")
    for name, text in resumes.items():
        parts.append(f'<resume name="{name}">\n{text}\n</resume>')
    return [{"type": "text", "text": "\n\n".join(parts), "cache_control": {"type": "ephemeral"}}]


def job_prompt(row):
    company, title, location, url, description = row
    return (f"<job>\nCompany: {company}\nTitle: {title}\nLocation: {location}\nURL: {url}\n\n"
            f"{description or '(no description available)'}\n</job>")


# ---------------------------------------------------------------- report
def where(location, filters):
    l = location.lower()
    if any(k in l for k in filters.get("locations", [])):
        return "dfw"
    return "remote" if "remote" in l else "other"


def write_report(cfg):
    con = db()
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM jobs ORDER BY score IS NULL, score DESC, first_seen DESC").fetchall()
    jobs = []
    for r in rows:
        posted = r["posted"] or ""
        jobs.append({
            "id": r["id"], "company": r["company"], "title": r["title"],
            "location": r["location"], "where": where(r["location"] or "", cfg["filters"]),
            "url": r["url"], "status": r["status"], "first_seen": (r["first_seen"] or "")[:10],
            "posted": posted[:10] if re.match(r"\d{4}-\d{2}-\d{2}", posted) else posted,
            "score": r["score"], "resume": r["resume"], "summary": r["summary"],
            "matches": json.loads(r["matches"] or "[]"), "gaps": json.loads(r["gaps"] or "[]"),
            "description": r["description"],
        })
    payload = json.dumps({"generated": datetime.now().strftime("%Y-%m-%d %H:%M"), "jobs": jobs})
    payload = payload.replace("</", "<\\/")  # keep "</script>" in descriptions from ending the tag
    OUT_DIR.mkdir(exist_ok=True)
    REPORT.write_text(REPORT_TEMPLATE.read_text(encoding="utf-8").replace("__JOBS_JSON__", payload),
                      encoding="utf-8")
    return len(jobs)


# ---------------------------------------------------------------- email digest
def load_env():
    """Read KEY=VALUE lines from .env into os.environ (real env vars win)."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def digest_html(jobs, extra, generated):
    rows = []
    for j in jobs:
        score = "" if j["score"] is None else (
            f'<span style="display:inline-block;min-width:28px;padding:2px 6px;border-radius:6px;'
            f'background:#eef1f6;font-weight:700;text-align:center">{j["score"]}</span> ')
        fit = f'<div style="color:#444;margin-top:4px">{escape(j["summary"])}</div>' if j["summary"] else ""
        rows.append(
            f'<tr><td style="padding:12px 0;border-bottom:1px solid #e2e5ea">'
            f'<div style="color:#5d6677;font-size:13px;font-weight:600">{escape(j["company"])}</div>'
            f'<div style="font-size:16px;margin:2px 0">{score}'
            f'<a href="{escape(j["url"])}" style="color:#2f5bd3;text-decoration:none">{escape(j["title"])}</a></div>'
            f'<div style="color:#5d6677;font-size:13px">{escape(j["location"] or "Location not listed")}</div>'
            f'{fit}</td></tr>')
    more = (f'<p style="color:#5d6677">+ {extra} more in the attached report.</p>' if extra else "")
    return (f'<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:640px;color:#1d2330">'
            f'<h2 style="margin:0 0 4px">{len(jobs) + extra} new job{"s" if len(jobs) + extra != 1 else ""}</h2>'
            f'<p style="color:#5d6677;margin:0 0 8px;font-size:13px">jobscout · {generated}</p>'
            f'<table style="width:100%;border-collapse:collapse">{"".join(rows)}</table>{more}'
            f'<p style="color:#5d6677;font-size:12px">Open the attached jobs.html for search, filters, '
            f'and full descriptions.</p></div>')


def digest_text(jobs, extra):
    lines = []
    for j in jobs:
        score = "" if j["score"] is None else f"[{j['score']}] "
        lines.append(f"{score}{j['company']}: {j['title']}\n  {j['location']}\n  {j['url']}")
    if extra:
        lines.append(f"+ {extra} more in the attached report.")
    return "\n\n".join(lines)


def send_digest(cfg, dry_run=False):
    """Email new jobs that haven't been emailed yet. Returns how many were included."""
    opts = cfg.get("digest", {})
    min_score, max_jobs = opts.get("min_score", 0), opts.get("max_jobs", 15)
    con = db()
    con.row_factory = sqlite3.Row
    # Unscored jobs always pass min_score, so the digest still works with scoring off.
    rows = con.execute(
        "SELECT * FROM jobs WHERE status = 'new' AND emailed_at IS NULL"
        " AND (score IS NULL OR score >= ?)"
        " ORDER BY score IS NULL, score DESC, first_seen DESC", (min_score,)).fetchall()
    if not rows:
        print("Digest: nothing new to send.")
        return 0
    shown, extra = rows[:max_jobs], max(0, len(rows) - max_jobs)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    html_body = digest_html(shown, extra, generated)
    OUT_DIR.mkdir(exist_ok=True)
    DIGEST_PREVIEW.write_text(html_body, encoding="utf-8")
    if dry_run:
        print(f"Digest (dry run): {len(rows)} job(s) -> {DIGEST_PREVIEW}. Nothing sent or marked.")
        return len(rows)

    load_env()
    sender = os.environ.get("JOBSCOUT_EMAIL_FROM")
    password = os.environ.get("JOBSCOUT_SMTP_PASSWORD")
    if not sender or not password:
        sys.exit("Digest: set JOBSCOUT_EMAIL_FROM and JOBSCOUT_SMTP_PASSWORD in .env (see README).")
    to = os.environ.get("JOBSCOUT_EMAIL_TO", sender)

    msg = EmailMessage()
    msg["Subject"] = f"jobscout: {len(rows)} new job{'s' if len(rows) != 1 else ''} ({generated[:10]})"
    msg["From"], msg["To"] = sender, to
    msg.set_content(digest_text(shown, extra))
    msg.add_alternative(html_body, subtype="html")
    write_report(cfg)
    msg.add_attachment(REPORT.read_bytes(), maintype="text", subtype="html", filename="jobs.html")

    host = os.environ.get("JOBSCOUT_SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("JOBSCOUT_SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, timeout=TIMEOUT) as smtp:
        smtp.login(sender, password)
        smtp.send_message(msg)

    now = datetime.now().isoformat(timespec="seconds")
    con.executemany("UPDATE jobs SET emailed_at = ? WHERE id = ?", [(now, r["id"]) for r in rows])
    con.commit()
    print(f"Digest: emailed {len(rows)} job(s) to {to}.")
    return len(rows)


# ---------------------------------------------------------------- commands
def load_config():
    with open(CONFIG, "rb") as f:
        return tomllib.load(f)


def cmd_run(_):
    cfg = load_config()
    cf = compile_filters(cfg["filters"])
    con = db()
    now = datetime.now().isoformat(timespec="seconds")
    new, scanned, failures = [], 0, []

    for c in cfg["company"]:
        try:
            postings = fetch(c, cfg["filters"])
        except Exception as e:
            failures.append(f"{c['name']}: {e}")
            continue
        scanned += len(postings)
        for j in postings:
            if not title_ok(j["title"], cf):
                continue
            if c["ats"] == "workday" and not j["description"]:
                j["description"], full_loc = workday_description(c, j["_wd_path"])
                if full_loc and re.search(r"\d+ locations", j["location"], re.I):
                    j["location"] = full_loc
            if not location_ok(j["location"], cf):
                continue
            if upsert(con, j, now):
                new.append(j)
        con.commit()
        time.sleep(0.3)

    OUT_DIR.mkdir(exist_ok=True)
    print(f"Scanned {scanned} postings across {len(cfg['company'])} companies.")
    if new:
        path = OUT_DIR / f"new_jobs_{datetime.now():%Y-%m-%d_%H%M}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["id", "company", "title", "location", "posted", "url"])
            for j in new:
                w.writerow([j["id"], j["company"], j["title"], j["location"], j["posted"], j["url"]])
        print(f"{len(new)} new matching jobs -> {path}")
        for j in new:
            print(f"  [{j['company']}] {j['title']} | {j['location']}\n    {j['url']}")
    else:
        print("No new matching jobs.")
    if failures:
        print("\nCompanies that failed (check token/host in companies.toml):")
        for f_ in failures:
            print("  " + f_)
    write_report(cfg)
    print(f"\nReport: {REPORT}")


def cmd_check(_):
    cfg = load_config()
    for c in cfg["company"]:
        try:
            n = len(fetch(c, {"workday_search": ["manager"]}))
            print(f"OK    {c['name']:<28} {c['ats']:<10} {n} postings")
        except Exception as e:
            print(f"FAIL  {c['name']:<28} {c['ats']:<10} {e}")


def cmd_score(a):
    # Imported here so run/check/list work without the anthropic package installed.
    from typing import Literal
    import anthropic
    from pydantic import create_model

    cfg = load_config().get("scoring", {})
    model = cfg.get("model", DEFAULT_MODEL)
    resumes = load_resumes()
    system = scoring_system(resumes, cfg.get("profile", ""))
    Fit = create_model(
        "Fit", resume=(Literal[tuple(resumes)], ...), score=(int, ...),
        matches=(list[str], ...), gaps=(list[str], ...), summary=(str, ...))

    con = db()
    q = "SELECT id, company, title, location, url, description FROM jobs"
    if a.id:
        q, args = q + " WHERE id = ?", (a.id,)
    else:
        q += " WHERE status = 'new'" + ("" if a.rescore else " AND score IS NULL")
        q, args = q + " ORDER BY first_seen DESC", ()
    rows = con.execute(q, args).fetchall()
    if a.limit:
        rows = rows[:a.limit]
    if not rows:
        print("Nothing to score.")
        return
    print(f"Scoring {len(rows)} job(s) with {model} against {len(resumes)} resume(s): "
          + ", ".join(resumes))

    client = anthropic.Anthropic()
    done = []
    for i, (job_id, *job_fields) in enumerate(rows, 1):
        label = f"[{job_fields[0]}] {job_fields[1]}"
        try:
            resp = client.beta.messages.parse(
                model=model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": job_prompt(job_fields)}],
                thinking={"type": "adaptive"},
                output_config={"effort": cfg.get("effort", "high")},
                output_format=Fit,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            sys.exit("Authentication failed: set ANTHROPIC_API_KEY to a valid key.")
        except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
            print(f"  {i}/{len(rows)} FAILED {label}: {e}")
            continue
        fit = resp.parsed_output
        if resp.stop_reason == "refusal" or fit is None:
            print(f"  {i}/{len(rows)} SKIPPED {label}: no usable answer ({resp.stop_reason})")
            continue
        score = max(0, min(100, fit.score))
        con.execute(
            "UPDATE jobs SET score = ?, resume = ?, matches = ?, gaps = ?, summary = ?,"
            " scored_at = ? WHERE id = ?",
            (score, fit.resume, json.dumps(fit.matches), json.dumps(fit.gaps), fit.summary,
             datetime.now().isoformat(timespec="seconds"), job_id))
        con.commit()
        done.append((score, fit.resume, job_id, *job_fields[:4], fit.summary))
        print(f"  {i}/{len(rows)} {score:>3}  {label}  ({fit.resume})")

    if not done:
        return
    done.sort(reverse=True)
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"scored_jobs_{datetime.now():%Y-%m-%d_%H%M}.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["score", "resume", "id", "company", "title", "location", "url", "summary"])
        w.writerows(done)
    print(f"\nScored {len(done)} job(s) -> {path}")


def cmd_digest(a):
    send_digest(load_config(), dry_run=a.dry_run)


def cmd_daily(_):
    cmd_run(None)
    print()
    send_digest(load_config())


def cmd_report(a):
    n = write_report(load_config())
    print(f"Wrote {n} jobs -> {REPORT}")
    if a.open:
        webbrowser.open(REPORT.as_uri())


def cmd_list(a):
    con = db()
    q = ("SELECT id, company, title, location, status, first_seen, url, score, resume,"
         " matches, gaps FROM jobs WHERE 1=1")
    args = []
    if a.status != "all":
        q += " AND status = ?"
        args.append(a.status)
    if a.min_score is not None:
        q += " AND score >= ?"
        args.append(a.min_score)
    q += " ORDER BY score IS NULL, score DESC, first_seen DESC"
    for row in con.execute(q, args):
        score = "--" if row[7] is None else row[7]
        print(f"{row[4]:<12} {score:>3}  {row[5][:10]}  [{row[1]}] {row[2]} | {row[3]}\n"
              f"{'':30}{row[6]}\n{'':30}id={row[0]}")
        if row[7] is not None:
            print(f"{'':30}resume: {row[8]}")
            for m in json.loads(row[9] or "[]"):
                print(f"{'':30}+ {m}")
            for g in json.loads(row[10] or "[]"):
                print(f"{'':30}- {g}")


def cmd_mark(a):
    con = db()
    cur = con.execute("UPDATE jobs SET status = ? WHERE id = ?", (a.status, a.job_id))
    con.commit()
    print("Updated." if cur.rowcount else "No job with that id.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sc = sub.add_parser("score")
    sc.add_argument("--limit", type=int, help="score at most N jobs")
    sc.add_argument("--rescore", action="store_true", help="re-score new jobs that already have a score")
    sc.add_argument("--id", help="score one job by id (any status)")
    sc.set_defaults(fn=cmd_score)
    rp = sub.add_parser("report")
    rp.add_argument("--open", action="store_true", help="open the report in your browser")
    rp.set_defaults(fn=cmd_report)
    dg = sub.add_parser("digest")
    dg.add_argument("--dry-run", action="store_true",
                    help="write output/digest.html without sending or marking jobs as emailed")
    dg.set_defaults(fn=cmd_digest)
    sub.add_parser("daily").set_defaults(fn=cmd_daily)
    ls = sub.add_parser("list")
    ls.add_argument("--status", default="new")
    ls.add_argument("--min-score", type=int)
    ls.set_defaults(fn=cmd_list)
    mk = sub.add_parser("mark")
    mk.add_argument("job_id")
    mk.add_argument("status")
    mk.set_defaults(fn=cmd_mark)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
