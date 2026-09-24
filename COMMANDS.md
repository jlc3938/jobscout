# jobscout command reference

Every command runs from the project folder:

```
python jobscout.py <command> [options]
```

Use `.venv/bin/python` (macOS/Linux) or `.venv\Scripts\python` (Windows) if you installed into a virtual environment.

| Command | What it does |
|---|---|
| [`check`](#check) | Confirm every company in `companies.toml` resolves |
| [`run`](#run) | Fetch postings, filter them, save new matches, refresh the report |
| [`score`](#score) | Rate new jobs against your resumes with Claude |
| [`report`](#report) | Write the HTML report of every tracked job |
| [`digest`](#digest) | Email new jobs you haven't been sent yet |
| [`daily`](#daily) | `run` + `digest`; what the 7 AM schedule calls |
| [`list`](#list) | Print tracked jobs in the terminal |
| [`mark`](#mark) | Change a job's status |

A job moves through `jobscout` like this:

```
check -> run -> (score) -> report / digest -> you review and apply -> mark
```

---

## check

```
python jobscout.py check
```

Fetches each company's board once and prints `OK` with the posting count, or `FAIL` with the error.
Run it after adding or editing companies. A `FAIL` usually means the token, host, or ATS type is wrong,
or the company moved to a different job board (fix it in `companies.toml` or delete the entry).

Reads: `companies.toml`. Writes: nothing.

---

## run

```
python jobscout.py run
```

1. Fetches every company in `companies.toml` (Greenhouse, Lever, Ashby, Workday).
2. Keeps jobs whose title matches `title_include` and none of `title_exclude`.
3. Keeps jobs in one of your `locations`, or remote (unless tied to a `remote_exclude` region).
4. Saves jobs it hasn't seen before to `jobs.db` with status `new`; updates `last_seen` on known ones.
5. Prints the new matches, exports them to CSV, and rewrites the HTML report.

Companies that fail are listed at the end; the rest of the run still completes. The first run treats
every current match as new.

Reads: `companies.toml`. Writes: `jobs.db`, `output/new_jobs_<date>.csv` (only when there are new
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
and a plain-words description of what you're looking for are under `[scoring]` in `companies.toml`.
Resumes can be `.docx`, `.md`, or `.txt`.

Reads: `companies.toml`, `resumes/`, `jobs.db`. Writes: `jobs.db` (score, resume, matches, gaps,
summary, scored_at), `output/scored_jobs_<date>.csv` (best first).

---

## report

```
python jobscout.py report [--open]
```

Writes `output/jobs.html`: one self-contained page with every tracked job, no server needed.

- Search titles, companies, and descriptions.
- Filter by status (defaults to `new`), company, and location (DFW area, remote, other).
- Read full descriptions inline.
- See scores, the chosen resume, matches, and gaps once jobs are scored; best scores sort first.
- Copy the `mark ... applied` / `mark ... skipped` command for any job (the page is read-only).

`run` refreshes the report automatically. `--open` opens it in your browser.

Reads: `companies.toml`, `jobs.db`, `report_template.html`. Writes: `output/jobs.html`.

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

Settings under `[digest]` in `companies.toml`:

| Key | Default | Meaning |
|---|---|---|
| `min_score` | `0` | Leave out scored jobs below this. Unscored jobs are always included. |
| `max_jobs` | `15` | Jobs listed in the email body; the rest are counted and are in the attachment. |

Email settings go in `.env` (copy `.env.example`), never in `companies.toml`, which is committed:

| Variable | Required | Meaning |
|---|---|---|
| `JOBSCOUT_EMAIL_FROM` | yes | Gmail address that sends the digest |
| `JOBSCOUT_SMTP_PASSWORD` | yes | Gmail app password (not your normal password) |
| `JOBSCOUT_EMAIL_TO` | no | Recipient; defaults to `JOBSCOUT_EMAIL_FROM` |
| `JOBSCOUT_SMTP_HOST` / `JOBSCOUT_SMTP_PORT` | no | Defaults `smtp.gmail.com` / `465` (SSL) |

To create a Gmail app password: Google Account > Security > 2-Step Verification > App passwords.
2-Step Verification must be on.

Reads: `companies.toml`, `.env`, `jobs.db`. Writes: `jobs.db` (emailed_at), `output/digest.html`,
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
| `companies.toml` | yes | Filters, scoring and digest settings, company list |
| `report_template.html` | yes | Layout for `output/jobs.html` |
| `.env.example` | yes | Template for `.env` |
| `resumes/` | no | Your resumes (personal data; git-ignored) |
| `.env` | no | Email address and app password |
| `jobs.db` | no | SQLite database of every tracked job |
| `output/` | no | CSV exports, `jobs.html`, `digest.html`, `daily.log` |
