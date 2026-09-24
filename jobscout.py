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
  python jobscout.py list [--status new|applied|skipped|all]
  python jobscout.py mark <job_id> <status>   # e.g. applied, skipped, interviewing
"""
import argparse
import csv
import html
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
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


def db():
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
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


def cmd_check(_):
    cfg = load_config()
    for c in cfg["company"]:
        try:
            n = len(fetch(c, {"workday_search": ["manager"]}))
            print(f"OK    {c['name']:<28} {c['ats']:<10} {n} postings")
        except Exception as e:
            print(f"FAIL  {c['name']:<28} {c['ats']:<10} {e}")


def cmd_list(a):
    con = db()
    q = "SELECT id, company, title, location, status, first_seen, url FROM jobs"
    args = ()
    if a.status != "all":
        q += " WHERE status = ?"
        args = (a.status,)
    for row in con.execute(q + " ORDER BY first_seen DESC", args):
        print(f"{row[4]:<12} {row[5][:10]}  [{row[1]}] {row[2]} | {row[3]}\n{'':25}{row[6]}\n{'':25}id={row[0]}")


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
    ls = sub.add_parser("list")
    ls.add_argument("--status", default="new")
    ls.set_defaults(fn=cmd_list)
    mk = sub.add_parser("mark")
    mk.add_argument("job_id")
    mk.add_argument("status")
    mk.set_defaults(fn=cmd_mark)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
