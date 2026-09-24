# jobscout

Pulls postings from company career boards, keeps only roles and locations you care about,
and remembers what it has already shown you.

## Setup
```
pip install requests          # Python 3.11+ (3.10 or older: also pip install tomli)
python jobscout.py check      # confirm every company in companies.toml resolves
python jobscout.py run        # first run: every current match is "new"
```

Put your own resume files (`.docx` or `.pdf`) in `resumes/`. They are git-ignored and never committed,
because they contain personal contact details.

New matches print to the console and are saved to `output/new_jobs_<date>.csv`.
Everything is stored in `jobs.db` (SQLite), including the full job description for the scoring step.

## Daily use
```
python jobscout.py list                     # jobs still marked new
python jobscout.py list --status all
python jobscout.py mark <id> applied        # or skipped, interviewing, rejected...
```

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

## Run it automatically (Windows Task Scheduler)
1. Open Task Scheduler and choose **Create Basic Task**.
2. Trigger: **Daily**, e.g. 7:00 AM.
3. Action: **Start a program**. Program: the full path to `python.exe`.
   Arguments: `jobscout.py run`. Start in: the folder holding `jobscout.py`.

On macOS or Linux, use cron: `0 7 * * * cd /path/to/jobscout && python3 jobscout.py run`
