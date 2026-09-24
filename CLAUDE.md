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
- Commands: `run`, `check`, `list [--status]`, `mark <id> <status>`.
- `companies.toml`: filters and the company list. Starter company tokens have not been
  verified against live boards yet; run `python jobscout.py check` and fix FAIL rows.
- Tested only with mocked responses so far.

## Planned next step: scoring
Add a `score` command that sends each new job's description plus Jose's resume to the
Claude API (Anthropic Python SDK, API key from the ANTHROPIC_API_KEY environment variable)
and stores a fit score (0-100), top matching points, gaps, and a tailored summary in `jobs.db`.
Two resume variants live in `resumes/`: Platform EM and AI Platform EM; the scorer should
pick the better fit per job. Resumes are git-ignored (personal data): never commit them;
the repo is public.

## Conventions
- Python 3.11+, dependencies kept minimal (`requests`, `anthropic`).
- Never commit `jobs.db`, `output/`, `.env`, or API keys.
- Development happens on Windows.
