# jobscout

Personal job-search automation for Jose Coello Enriquez, a Software Engineering Manager
(platform engineering, CI/CD, Kubernetes) based in McKinney, TX, looking for Engineering
Manager roles in platform/infrastructure/developer experience and AI/ML platform teams.
Targets: DFW-area onsite/hybrid (McKinney, Plano, Frisco, Dallas, etc.) or US-remote.

## Goal
Automate everything in the application process except the final submit:
discovery -> scoring -> tailoring -> tracking. A human reviews and submits every application.
Do not add auto-apply, LinkedIn/Indeed scraping, or anything that submits forms.

## Current state
- `jobscout.py`: fetches postings from Greenhouse, Lever, and Ashby public job-board APIs
  and Workday's career-site JSON endpoint; filters by title regex and location; stores
  matches in SQLite (`jobs.db`, table `jobs`) with full descriptions; exports new matches
  to `output/new_jobs_<date>.csv`.
- Commands: `run`, `check`, `score [--limit N] [--rescore] [--id]`, `report [--open]`, `digest [--dry-run]`, `daily`, `list [--status] [--min-score]`, `mark <id> <status>`.
- `report` renders `report_template.html` with the jobs table embedded as JSON into
  `output/jobs.html` (self-contained, no server); `run` regenerates it after each fetch.
- `search.toml` (git-ignored, per user): filters, scoring and digest settings, company list.
  `setup` creates it from the user's resume (Claude drafts it when ANTHROPIC_API_KEY is set,
  otherwise a questionnaire) and verifies suggested companies against live boards.
  `search.example.toml` is the committed example. Run `check` after editing companies.
- `digest` emails new, not-yet-emailed jobs (column `emailed_at`) via Gmail SMTP; addresses and
  the app password come from `.env` (see `.env.example`), never `search.toml`. `daily` = run +
  digest and is what the 7 AM launchd job calls.
- `check` verified against live boards (2026-09-23). `score` tested only with a mocked client so far.

## Scoring
`score` sends each new, unscored job plus every resume in `resumes/` to the Claude API
(Anthropic Python SDK, key from ANTHROPIC_API_KEY) using structured outputs, and stores
score (0-100), resume (chosen variant), matches, gaps (JSON lists), summary, and scored_at
in `jobs.db`. Resumes and instructions form a cached system prompt shared by every job.
Model, effort, and the candidate's preferences live under `[scoring]` in `search.toml`.
Resumes are git-ignored (personal data): never commit them; the repo is public.

## Conventions
- Keep README.md (overview) and COMMANDS.md (full command reference) in sync with jobscout.py.
- Python 3.11+, dependencies kept minimal (`requests`, `anthropic`).
- Never commit `jobs.db`, `output/`, `.env`, or API keys.
- Development happens on Windows.
