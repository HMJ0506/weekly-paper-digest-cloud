# Weekly Paper Digest — cloud pipeline

Weekly scan of 16 high-impact journals for newly published papers on metal
batteries, anode-free cells, SEI / electrode–electrolyte interface engineering,
current-collector engineering, and MOF materials applied to those problems.
The report is posted every Monday 09:00 KST as a sub-page of the Notion page
"Weekly Paper Digest" by a scheduled Claude Code cloud routine.

This repository holds the **discovery stage** (a GitHub Actions job that
queries Crossref and commits `candidates.json`) and the **routine prompt**
(`routine_prompt.md`) that the cloud agent executes.

## Why it is split

The cloud agent runs in a sandbox whose egress proxy blocks `api.crossref.org`
and `www.nature.com` outright (curl, Python urllib and tool-level fetches
alike). It can reach GitHub. So discovery runs here, where egress is open, and
the agent reads the result over HTTPS from
`https://raw.githubusercontent.com/HMJ0506/weekly-paper-digest-cloud/main/candidates.json`.
The repository must stay **public** for that URL to be readable without a token.

| Stage | Where | When |
|---|---|---|
| Discovery | GitHub Actions (this repo) | Sun 20:10 UTC and 22:10 UTC = Mon 05:10 / 07:10 KST (two attempts; see workflow comment) |
| Writing + publishing | Claude Code cloud routine "Weekly Paper Digest (cloud)" | Mon 00:00 UTC = Mon 09:00 KST |

If GitHub starts the job late, the routine waits and re-downloads the file
(up to 6 × 10 min) before falling back to a clearly labelled stale digest.

## Files

- `discover.py` — the discovery stage. Standard library only.
  `python discover.py --run-date 2026-08-19 --out candidates.json`
- `.github/workflows/weekly-discovery.yml` — the scheduled job
- `routine_prompt.md` — the prompt the cloud routine runs (single source of truth)
- `candidates.json` — latest output
- `data/candidates-YYYY-MM-DD.json` — dated snapshots

## Configuration (repository secrets, both optional)

- `CROSSREF_MAILTO` — contact address for Crossref's polite pool.
- `SPRINGER_API_KEY` — free key from https://dev.springernature.com/ . Without
  it, abstracts for subscription Nature titles cannot be retrieved from CI
  (nature.com answers datacenter IPs with a bot challenge).

## Updating the routine

1. Edit `routine_prompt.md` and commit.
2. In Claude Code, run `/schedule` → Update, pick "Weekly Paper Digest (cloud)",
   and replace the prompt with the file contents. Routines are listed at
   https://claude.ai/code/routines .

The routine needs the claude.ai **Notion** connector attached and posts under
Notion page id `342e998e-0380-802d-9c8b-ecaae70db509`.

## Relation to `HMJ0506/weekly-paper-digest`

That repository carries the same `discover.py` plus a **local** pipeline
(`/weekly-digest` Claude Code skill) that publishes to a separate Notion page,
"Weekly Paper Digest (Local)". The two pipelines are kept independent on purpose
so they can be cross-validated; neither writes to the other's page.
