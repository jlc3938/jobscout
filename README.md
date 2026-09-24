# jobscout

Personal job-search automation. It pulls postings from company career boards, keeps only the roles
and locations you care about, remembers what it has already shown you, can rate each job against
your resume with Claude, and emails you a daily digest. You review and apply to every job yourself;
nothing is submitted automatically.

```
check -> run -> (score) -> report / digest -> you review and apply -> mark
```

## Setup
```
python3 -m venv .venv                        # Python 3.11+
.venv/bin/pip install -r requirements.txt    # Windows: .venv\Scripts\pip install -r requirements.txt
# put your resume(s) in resumes/ first
python jobscout.py setup                     # build your search from your resume
python jobscout.py run                       # first run: every current match is "new"
python jobscout.py report --open             # browse what it found
```

Put your own resumes (`.docx`, `.md`, or `.txt`) in `resumes/`. They are git-ignored and never
committed, because they contain personal contact details.

`setup` asks where you'd work and whether remote is OK. With `ANTHROPIC_API_KEY` set, Claude then
reads your resume and drafts the job titles to match, locations, a description of what you want,
and 15-25 companies to watch; every company is checked against its live job board and wrong guesses
are dropped. Without a key (or with `--manual`), it asks for the titles instead and starts from the
example company list. You review the draft before it's saved to `search.toml`.
No API key and rather not answer questions? [COMMANDS.md](COMMANDS.md#or-create-searchtoml-in-claude-chat)
has a prompt you can paste into Claude chat, with your resume attached, to write `search.toml` for you.
It also has a [job-ranking prompt](COMMANDS.md#or-rank-jobs-in-claude-chat): attach your resume and
`output/jobs.html` and Claude chat ranks the new jobs the same way `score` does.

For the email digest, copy `.env.example` to `.env` and fill in your Gmail address and app password.

Everything is stored in `jobs.db` (SQLite) and `output/`, both git-ignored.

## Commands

| Command | What it does |
|---|---|
| `setup [--manual]` | Build your `search.toml` from your resume |
| `check` | Confirm every company in `search.toml` resolves |
| `run` | Fetch postings, filter them, save new matches, refresh the report |
| `score [--limit N] [--rescore] [--id ID]` | Rate new jobs against your resumes with Claude |
| `report [--open]` | Write `output/jobs.html`: search, filters, descriptions, scores |
| `digest [--dry-run]` | Email new jobs you haven't been sent yet |
| `daily` | `run` + `digest`; what the 7 AM schedule calls |
| `list [--status S] [--min-score N]` | Print tracked jobs in the terminal |
| `mark <id> <status>` | Set a job's status: applied, skipped, interviewing... |

**[COMMANDS.md](COMMANDS.md)** explains every command, option, setting, and file in detail.

## Configuration
`search.toml` is your search. `setup` creates it; it's git-ignored, so your preferences stay on your
machine. `search.example.toml` is a filled-in example. It holds everything except secrets:

- `[filters]`: title patterns to include and exclude, target locations, remote rules.
- `[scoring]`: Claude model, effort level, and a plain-words description of what you're looking for.
- `[digest]`: minimum score and how many jobs to list in the email.
- `[[company]]`: one entry per company board (see below).

Secrets go in `.env` (email) or environment variables (`ANTHROPIC_API_KEY`), never in `search.toml`.

## Adding companies
Open a company's careers page and look at where a job link points:

| Link looks like | ats | value to copy |
|---|---|---|
| `boards.greenhouse.io/stripe` or `job-boards.greenhouse.io/stripe` | greenhouse | token = "stripe" |
| `jobs.lever.co/plaid` | lever | token = "plaid" |
| `jobs.ashbyhq.com/ramp` | ashby | token = "ramp" |
| `capitalone.wd12.myworkdayjobs.com/Capital_One/...` | workday | host, tenant (before `.wd`), site (first path segment) |

Run `check` after editing. Companies on other systems (iCIMS, Oracle, SuccessFactors, custom sites) aren't covered;
set up email alerts on those sites instead.

## Run it automatically
**macOS (launchd, runs at 7:00 AM; if the Mac is asleep it runs when it wakes):** save as
`~/Library/LaunchAgents/com.jobscout.daily.plist`, replacing `/path/to/jobscout`, then
`launchctl load ~/Library/LaunchAgents/com.jobscout.daily.plist`.
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.jobscout.daily</string>
  <key>ProgramArguments</key><array>
    <string>/path/to/jobscout/.venv/bin/python</string>
    <string>/path/to/jobscout/jobscout.py</string>
    <string>daily</string>
  </array>
  <key>WorkingDirectory</key><string>/path/to/jobscout</string>
  <key>StartCalendarInterval</key><dict>
    <key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key><string>/path/to/jobscout/output/daily.log</string>
  <key>StandardErrorPath</key><string>/path/to/jobscout/output/daily.log</string>
</dict></plist>
```

**Windows (Task Scheduler):** Create Basic Task, trigger Daily at 7:00 AM, action Start a program:
the full path to `python.exe`, arguments `jobscout.py daily`, start in the folder holding `jobscout.py`.
