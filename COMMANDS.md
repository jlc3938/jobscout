# jobscout command reference

Every command runs from the project folder:

```
python jobscout.py <command> [options]
```

Use `.venv/bin/python` (macOS/Linux) or `.venv\Scripts\python` (Windows) if you installed into a virtual environment.

| Command | What it does |
|---|---|
| [`setup`](#setup) | Build your `search.toml` from your resume |
| [`check`](#check) | Confirm every company in `search.toml` resolves |
| [`run`](#run) | Fetch postings, filter them, save new matches, refresh the report |
| [`score`](#score) | Rate new jobs against your resumes with Claude |
| [`report`](#report) | Write the HTML report of every tracked job |
| [`digest`](#digest) | Email new jobs you haven't been sent yet |
| [`daily`](#daily) | `run` + `digest`; what the 7 AM schedule calls |
| [`list`](#list) | Print tracked jobs in the terminal |
| [`mark`](#mark) | Change a job's status |

A job moves through `jobscout` like this:

```
setup -> check -> run -> (score) -> report / digest -> you review and apply -> mark
```

---

## setup

```
python jobscout.py setup [--manual]
```

Creates `search.toml`, your personal search, from the resumes in `resumes/`. Every other command
reads it; without one they stop and point you here.

1. Asks which cities you'd work in onsite or hybrid (blank = remote only), whether remote is OK,
   and anything else about what you want (optional, e.g. "people manager only, AI platform teams").
2. Drafts the search:
   - **With `ANTHROPIC_API_KEY` set:** Claude reads your resume(s) and your answers and proposes
     title patterns to match and rule out, nearby locations, Workday search terms, a profile for
     scoring, and 15-25 companies on Greenhouse, Lever, or Ashby. Each company is checked against
     the live boards (trying the other two if the suggested one fails); companies with no board are
     dropped. Invalid title patterns are skipped.
   - **Without a key, or with `--manual`:** asks which job titles to match and which words rule a
     title out, and starts from the company list in `search.example.toml`.
3. Shows the draft and asks before saving. If `search.toml` already exists, asks before replacing it
   and keeps your `[scoring]` model/effort and `[digest]` settings.

| Option | Effect |
|---|---|
| `--manual` | Use the questionnaire even when `ANTHROPIC_API_KEY` is set |

Edit `search.toml` afterwards to fine-tune anything, and add companies by hand (see
[Adding companies](README.md#adding-companies)); Workday companies can only be added by hand.

Reads: `resumes/`, `search.example.toml`, `search.toml` (if present). Writes: `search.toml`.

### Or: create search.toml in Claude chat

No API key? You can have Claude draft the file at [claude.ai](https://claude.ai) instead:

1. Start a new chat and attach your resume.
2. Paste the prompt below, filling in the three lines under "My preferences".
3. Save Claude's answer as `search.toml` in the project folder.
4. Run `python jobscout.py check`. Claude can't see the live job boards from chat, so some company
   tokens may be wrong: fix or delete every `FAIL` row (see
   [Adding companies](README.md#adding-companies)).

````text
I use jobscout, a small tool that pulls postings from company job boards and filters them. Using my
attached resume and my preferences below, write my search.toml file for it.

My preferences:
- Cities I'd work in onsite or hybrid: <e.g. McKinney, Plano, Dallas — or "none, remote only">
- Open to remote: <yes / no>
- Anything else: <e.g. people manager only, AI platform teams — or "nothing">

How the tool uses the file:
- A posting is kept only if its title matches at least one `title_include` regex and no
  `title_exclude` regex (Python `re`, case-insensitive, matched anywhere in the title).
- Its location must contain one of the `locations` substrings, or contain "remote" (if
  `allow_remote = true`) without containing any `remote_exclude` substring.
- `profile` is read by an AI model that scores each job against my resume, so make it specific.

What to write:
- title_include: 4-10 regexes that catch every posting I'd realistically apply to, at my current
  level and one step up, covering common title variants, e.g. "engineering manager",
  "manager,? (of )?(software )?engineering", "(platform|infrastructure) .*manager".
- title_exclude: regexes for roles that would slip past title_include but don't fit me.
- workday_search: 2-4 short search phrases.
- locations: lowercase; each city I named, nearby suburbs in the same metro, and my state as it
  appears in postings (e.g. "texas", ", tx"). Empty list if remote only.
- remote_exclude: lowercase regions that rule out a remote posting for someone in my country.
- profile: 2-4 plain sentences on the roles, level, domain, and location I want.
- 15-25 [[company]] entries likely to hire for these roles, weighted toward my domain and location.
  Only companies whose job board is on Greenhouse, Lever, or Ashby; `token` is the slug in
  boards.greenhouse.io/<token>, jobs.lever.co/<token>, or jobs.ashbyhq.com/<token>. Only include
  companies you're fairly confident about.

Reply with only the file contents in one TOML code block, in exactly this shape (keep the
[scoring] model/effort and [digest] values as shown):

```toml
[filters]
title_include = ["...", "..."]
title_exclude = ["...", "..."]
locations = ["...", "..."]
allow_remote = true
remote_exclude = ["...", "..."]
keep_unknown_location = true
workday_search = ["...", "..."]

[scoring]
model = "claude-opus-5"
effort = "high"
profile = "..."

[digest]
min_score = 0
max_jobs = 15

[[company]]
name = "..."
ats = "greenhouse"
token = "..."
```

In TOML strings, write each regex backslash as two backslashes (e.g. "engineering\\s+manager").
````

---

## check

```
python jobscout.py check
```

Fetches each company's board once and prints `OK` with the posting count, or `FAIL` with the error.
Run it after adding or editing companies. A `FAIL` usually means the token, host, or ATS type is wrong,
or the company moved to a different job board (fix it in `search.toml` or delete the entry).

Reads: `search.toml`. Writes: nothing.

---

## run

```
python jobscout.py run
```

1. Fetches every company in `search.toml` (Greenhouse, Lever, Ashby, Workday).
2. Keeps jobs whose title matches `title_include` and none of `title_exclude`.
3. Keeps jobs in one of your `locations`, or remote (unless tied to a `remote_exclude` region).
4. Saves jobs it hasn't seen before to `jobs.db` with status `new`; updates `last_seen` on known ones.
5. Prints the new matches, exports them to CSV, and rewrites the HTML report.

Companies that fail are listed at the end; the rest of the run still completes. The first run treats
every current match as new.

Reads: `search.toml`. Writes: `jobs.db`, `output/new_jobs_<date>.csv` (only when there are new
matches), `output/jobs.html`.

---

## score

```
python jobscout.py score [--limit N] [--rescore] [--id JOB_ID]
```

Sends each new, unscored job to Claude along with every resume in `resumes/`. For each job, Claude
picks the better-fitting resume and returns:

- **score**: 0-100 fit. 85+ strong, 70-84 good with minor gaps, 50-69 stretch, below 50 poor.
- **matches**: the strongest evidence from the chosen resume.
- **gaps**: requirements the resume doesn't show.
- **summary**: a 2-3 sentence resume summary tailored to the job, using only facts from the resume.

| Option | Effect |
|---|---|
| `--limit N` | Score at most N jobs (try `--limit 3` first) |
| `--rescore` | Also re-score new jobs that already have a score |
| `--id JOB_ID` | Score one job, whatever its status |

Needs `pip install anthropic` and an `ANTHROPIC_API_KEY` environment variable. Model, effort level,
and a plain-words description of what you're looking for are under `[scoring]` in `search.toml`.
Resumes can be `.docx`, `.md`, or `.txt`.

Reads: `search.toml`, `resumes/`, `jobs.db`. Writes: `jobs.db` (score, resume, matches, gaps,
summary, scored_at), `output/scored_jobs_<date>.csv` (best first).

### Or: rank jobs in Claude chat

No API key? Claude chat at [claude.ai](https://claude.ai) can rank the jobs with the same rubric that
`score` uses:

1. Run `python jobscout.py report` (or use the `jobs.html` attached to your digest email). It
   contains every tracked job with its full description.
2. Start a new chat and attach your resume(s) and `output/jobs.html`. To rank a few postings you
   found elsewhere, paste their descriptions into the chat instead.
3. Paste the prompt below, filling in the line under "What I'm looking for" (copy the `profile`
   from your `search.toml`).

Results stay in the chat; they aren't saved to `jobs.db`. Use `mark` to record the ones you skip or
apply to.

````text
Rank these job postings by how well I fit them.

Attached: my resume(s) and the postings, either as jobscout's jobs.html report or pasted below. In
jobs.html, the postings are in the JSON inside <script id="data">, one object per job with company,
title, location, url, status, and description. Rank only jobs whose status is "new".

What I'm looking for: <paste the profile from search.toml, e.g. "Engineering Manager roles on
platform or infrastructure teams, people-manager only. Onsite/hybrid in Dallas-Fort Worth or
US-remote.">

Judge each job as posted. Weigh level and scope (manager vs individual contributor, team size,
seniority), domain, must-have requirements, and location or remote fit against what I'm looking
for. A hard blocker (wrong location with no remote option, a required clearance, an IC-only role)
should pull the score well below 50.

Score each job 0-100: 85+ strong match worth applying to today; 70-84 good match with minor gaps;
50-69 stretch; below 50 poor fit. If I attached more than one resume, pick the one that fits each job
better.

Reply with:
1. A table of every job, best first, with columns: rank, score, company, title, location, resume
   (which one fits better), and the posting link.
2. For each job scoring 70 or more: 3-5 bullets of the strongest evidence from my resume, 0-4
   bullets of requirements my resume doesn't show, and a 2-3 sentence professional summary for the
   top of my resume tailored to that job, using only facts from my resume, with no invented numbers.
3. One line per job below 70 saying why it ranked low.
````

---

## report

```
python jobscout.py report [--open]
```

Writes `output/jobs.html`: one self-contained page with every tracked job, no server needed.

- Search titles, companies, and descriptions.
- Filter by status (defaults to `new`), company, and location (your cities, remote, other).
- Read full descriptions inline.
- See scores, the chosen resume, matches, and gaps once jobs are scored; best scores sort first.
- Copy the `mark ... applied` / `mark ... skipped` command for any job (the page is read-only).

`run` refreshes the report automatically. `--open` opens it in your browser.

Reads: `search.toml`, `jobs.db`, `report_template.html`. Writes: `output/jobs.html`.

---

## digest

```
python jobscout.py digest [--dry-run]
```

Emails jobs that are `new` and haven't been emailed yet, then marks them as emailed so they never
repeat. Jobs are ordered by score (unscored jobs last); the email body lists the top `max_jobs` and
the full `jobs.html` report is attached. If nothing is new, no email is sent.

| Option | Effect |
|---|---|
| `--dry-run` | Write the email to `output/digest.html` without sending or marking anything |

Settings under `[digest]` in `search.toml`:

| Key | Default | Meaning |
|---|---|---|
| `min_score` | `0` | Leave out scored jobs below this. Unscored jobs are always included. |
| `max_jobs` | `15` | Jobs listed in the email body; the rest are counted and are in the attachment. |

Email settings go in `.env` (copy `.env.example`), not `search.toml`:

| Variable | Required | Meaning |
|---|---|---|
| `JOBSCOUT_EMAIL_FROM` | yes | Gmail address that sends the digest |
| `JOBSCOUT_SMTP_PASSWORD` | yes | Gmail app password (not your normal password) |
| `JOBSCOUT_EMAIL_TO` | no | Recipient; defaults to `JOBSCOUT_EMAIL_FROM` |
| `JOBSCOUT_SMTP_HOST` / `JOBSCOUT_SMTP_PORT` | no | Defaults `smtp.gmail.com` / `465` (SSL) |

To create a Gmail app password: Google Account > Security > 2-Step Verification > App passwords.
2-Step Verification must be on.

Reads: `search.toml`, `.env`, `jobs.db`. Writes: `jobs.db` (emailed_at), `output/digest.html`,
`output/jobs.html`.

---

## daily

```
python jobscout.py daily
```

Runs `run`, then `digest`. This is the command to schedule; see
[Run it automatically](README.md#run-it-automatically). Add `score` in between once you want scored
digests.

---

## list

```
python jobscout.py list [--status STATUS] [--min-score N]
```

Prints tracked jobs, best score first, then newest. Scored jobs also show the chosen resume,
matches (`+`), and gaps (`-`).

| Option | Effect |
|---|---|
| `--status STATUS` | `new` (default), any status you've used with `mark`, or `all` |
| `--min-score N` | Only jobs scored N or higher |

---

## mark

```
python jobscout.py mark <job_id> <status>
```

Sets a job's status. Any word works; the usual ones are `applied`, `skipped`, `interviewing`,
`rejected`, `offer`. Only `new` jobs are scored and emailed, so marking a job anything else takes it
out of both. Job ids look like `greenhouse:Stripe:8113337` and are shown by `list` and on each card
in the report.

---

## Files

| Path | Committed | What it is |
|---|---|---|
| `jobscout.py` | yes | The whole tool |
| `search.example.toml` | yes | Example search; `setup --manual` takes its company list |
| `search.toml` | no | Your search: filters, scoring and digest settings, company list |
| `report_template.html` | yes | Layout for `output/jobs.html` |
| `.env.example` | yes | Template for `.env` |
| `resumes/` | no | Your resumes (personal data; git-ignored) |
| `.env` | no | Email address and app password |
| `jobs.db` | no | SQLite database of every tracked job |
| `output/` | no | CSV exports, `jobs.html`, `digest.html`, `daily.log` |
