You are running an autonomous weekly scheduled task called "Weekly Paper Digest". This run has NO memory of previous runs or any prior conversation, so follow these self-contained instructions exactly. The goal: report newly published papers across a fixed set of battery journals on five topics, write an English report, and post it as a new sub-page in Notion.

## 0. Fetch the discovery results

Discovery has already been done for you. A GitHub Actions job is scheduled twice before you start (Sunday 20:10 and 22:10 UTC = Monday 05:10 / 07:10 KST), queries Crossref, and publishes the result. **This sandbox cannot reach api.crossref.org or www.nature.com** — the egress proxy rejects both, for curl, Python urllib and WebFetch alike. Do not try. Everything you need is in that one file.

```
curl -sS --max-time 60 -w '\nHTTP=%{http_code}\n' -o /tmp/candidates.json https://raw.githubusercontent.com/HMJ0506/weekly-paper-digest-cloud/main/candidates.json
python3 -c "
import json;d=json.load(open('/tmp/candidates.json',encoding='utf-8'))
print('run_date  ',d['run_date']);print('window    ',d['window_start'],'..',d['window_end'])
print('generated ',d['generated_utc']);print('candidates',len(d['candidates']))
print('unreachable',d['unreachable']);print('truncated',d.get('truncated_journals'))
print('enrich_failures',d.get('enrich_failures'))
for s in d['journal_stats']: print('  %-32s %-8s scanned=%4d cand=%3d'%(s['journal'],s['date_field'],s['scanned'],s['candidates']))
"
```

Then read the whole file and work from it. It is small — discovery already filtered roughly 750 weekly records down to a few dozen candidates.

### FRESHNESS CHECK — before anything else

Compute today's date in KST as `date -u -d '+9 hours' +%F` (do NOT use `TZ=Asia/Seoul`; hosts without tzdata silently fall back to UTC, rolling the date back a day near 00:00 UTC). Compare with `run_date` in the JSON.

- **Match** → proceed. `run_date` is RUN_DATE; `window_start`/`window_end` are the coverage window.
- **`run_date` older than today** → the Actions job has not finished yet (GitHub's scheduler can start it hours late — on 2026-08-31 it started 2 h 11 min late and this task published an empty digest from the previous week's file) or it failed. Wait and retry before giving up: run `sleep 600`, re-download the file with the same curl command, and re-check `run_date`; repeat up to 6 times (about 60 minutes in total). As soon as `run_date` matches today, proceed normally. Only if it still does not match after the 6th attempt treat the file as stale: do NOT present a stale digest as current. Still create the Notion page, but put "⚠️ Discovery job did not run for RUN_DATE — this page reports the last available discovery from {run_date}" at the top, say so plainly in the Summary (including how many retries were made and the `generated_utc` you saw), and list papers from that older file only if they were not already posted (see §5).
- **Download failed or file missing** → retry the download the same way (up to 6 times, 10 minutes apart). If it still fails, create the page with every section reading "No new papers found this week in the target journals." and a Summary stating discovery produced no output, so coverage is unverified this week.

Also check `unreachable` (journals whose Crossref call failed), `truncated_journals` (result set exceeded the fetch limit, so papers may be missing), and `enrich_failures` (abstract could not be retrieved). All three go in Search Notes.

## 1. Coverage window
- COVERAGE WINDOW = [RUN_DATE − 8 days, RUN_DATE − 1 day], inclusive, as given in the JSON.
- **In-window membership was judged by ONLINE date only.** Never re-derive it from a DOI's year, an issue/volume number, or an issue date — issue dates run weeks to months later. Each candidate carries `date_kind`:
  - `published-online (Crossref)` — publisher deposited a day-precision online date. Report it as the online date.
  - `Crossref registration date (~online)` — used for Energy & Environmental Science, Chemical Society Reviews, Joule and Science, because RSC deposits a year-only online date and Elsevier and AAAS deposit none. Tracks first publication within about a day. Report as e.g. "2026-08-12 (Crossref registration date ≈ online)" — never as a verified online date.
- Some candidates carry `online_date_verified`, from Springer Nature's API or the nature.com `citation_online_date` meta tag. When present, prefer it.

## 2. Target journals — ONLY these 16
Nature; Nature Energy; Nature Materials; Nature Chemistry; Nature Communications; Nature Reviews Materials; Nature Nanotechnology; Nature Synthesis; Science; Joule; Energy & Environmental Science; JACS; Chemical Society Reviews; Advanced Materials; Advanced Energy Materials; ACS Energy Letters.
Discovery queries exactly these by ISSN, so nothing outside the set can appear. Do not add journals of your own.

## 3. Topics (five)
1. **Metal batteries** — Li/Na/K/Zn/Mg/Ca metal anodes and metal-anode battery systems; plating/stripping, Coulombic efficiency, dendrite growth and suppression.
2. **Anode-free batteries** — anode-free / anode-less Li, Na, etc. cells.
3. **SEI and electrode–electrolyte interface engineering** — SEI/CEI composition and structure, artificial interphases, electrolyte design targeting the interface.
4. **Current collector engineering** — 3D/patterned/coated current collectors, lithiophilic/sodiophilic hosts, substrate/host design for metal anodes.
5. **MOF / MOF-derived materials for metal-anode and interface engineering** — MOFs and MOF-derived materials applied to the problems of topics 1–4: MOF or MOF-derived hosts and lithiophilic/sodiophilic current-collector coatings; MOF artificial SEI / interphase layers; MOF-based or MOF-modified separators and interlayers; MOF-based / MOF-derived solid or quasi-solid electrolytes and single-ion conductors — for Li/Na/K/Zn/Mg/Ca metal and anode-free cells. INCLUDE both intact-MOF applications and MOF-derived materials (when the framework is only a precursor, e.g. MOF-derived porous carbon, say so in the summary). EXCLUDE covalent organic frameworks (COFs) and Prussian blue analogues (PBAs) from this section — note them in Search Notes only if highly relevant. The JSON flags them in `cof_pba_flagged`.
- **Placement rule.** A paper that fits more than one topic gets ONE full entry under its best-fitting topic, plus an optional one-line cross-reference (title only) under the others. A MOF or MOF-derived paper that also fits topics 1–4 belongs under Topic 5, the most specific. Non-MOF papers never go in Topic 5.

## 3a. YOUR JOB: precision filtering

The keyword filter that produced the candidates is deliberately **recall-first** — it over-includes so nothing is missed. Each candidate has a `topics` array of the patterns it matched. **Judge each one on its abstract and discard the false positives.** Noise seen in validation:
- Non-battery papers sharing vocabulary — MOF gas-sorption, uranium extraction or catalysis studies; CO2 electroreduction, C–N coupling and ammonia synthesis; water electrolysis; fuel-cell ORR; "dendritic cell" biology; biomedical nanomotors and patches; perovskite photovoltaics.
- Cathode-only, metal–air, or bulk-solid-electrolyte-materials papers touching none of the five topics. If one is genuinely borderline but worth keeping, keep it and add a short italic "Relevance:" note saying why.
- Candidates matching `5-mof` whose MOF content is incidental rather than applied to a metal-anode/interface problem — Search Notes, not Topic 5.

Record what you discarded, with reasons, in Search Notes.

## 4. Abstracts and the access rule
Nearly every candidate arrives with an abstract attached. `abstract_source` says where it came from:
- `crossref` — Wiley, ACS, RSC and AAAS deposit abstracts to Crossref. Note that RSC sometimes deposits only a truncated or graphical abstract; summarise strictly within the text you were given, never extrapolate performance figures, and say so in Search Notes when the deposit was thin.
- `springer nature meta api` — subscription Nature titles (Nature, Nature Energy, Nature Materials, Nature Chemistry, Nature Nanotechnology, Nature Synthesis, Nature Reviews Materials) deposit no abstract to Crossref; only the open-access Nature Communications does. Discovery retrieves theirs from Springer Nature's metadata API, which also supplies `online_date_verified`.
- `nature.com dc.description` — an older fallback, used only when the Springer API returns nothing.
- Candidates listed in `no_abstract_dois` have none available anywhere. In practice this means **Elsevier (Joule)**, which deposits no abstract to Crossref, OpenAlex or Semantic Scholar. Anything in `enrich_failures` also failed retrieval — report it in Search Notes.

**ACCESS RULE:** if a candidate has no abstract, DO NOT fabricate a summary. List it with ONLY Title, Journal, online date and DOI/URL, marked "⚠️ Access-restricted — DOI only", optionally with a one-line "Focus (from title):" paraphrase restating the title (never invented results).

Do not try to fetch publisher pages to fill gaps — they are unreachable from here, and `pubs.rsc.org` blocks automated clients anyway.

## 5. Deduplication
- Load the Notion tools with ToolSearch (`select:mcp__Notion__notion-fetch,mcp__Notion__notion-create-pages`).
- Use `mcp__Notion__notion-fetch` to read the parent page `342e998e-0380-802d-9c8b-ecaae70db509` ("Weekly Paper Digest") and list its existing digest sub-pages, then open every digest whose coverage window could overlap this one — normally the most recent 4–8 — and collect every DOI already covered.
- Exclude any paper whose DOI already appeared. Record which prior digests you checked.

## 6. Build the report (write in ENGLISH)
- Intro line: "Auto-generated weekly paper digest (target high-IF journals only). Topics: Metal Batteries · Anode-Free · SEI/Interface · Current Collector Engineering · MOF for Energy Storage."
- "**Target journals:**" the 16 journals.
- "**Coverage period:**" WINDOW_START – WINDOW_END.
- "**Generated:**" RUN_DATE (automated).
- A divider, then "## Summary": 2–4 sentences — counts per topic, and note any journals with 0 results or that were unreachable.
- A divider, then five H2 sections: "## 1. Metal Batteries", "## 2. Anode-Free Batteries", "## 3. SEI & Electrode–Electrolyte Interface", "## 4. Current Collector Engineering", "## 5. MOF for Energy Storage". Under each, list every qualifying paper:
  - **Title** in bold on its own line.
  - A PLAIN-text metadata line (do NOT italicise the whole line — it breaks the DOI link): Authors (or "et al.") · Journal · Online date · DOI as a clickable Markdown link.
  - With an abstract: a 3–5 sentence summary (key findings, method, significance). Add a short italic "Relevance:" note if borderline. For Topic 5, if the MOF is only a precursor, state "MOF-derived (framework not retained)".
  - Without an abstract: apply the ACCESS RULE.
  - Empty section: "No new papers found this week in the target journals."
- A divider, then "## Search Notes": candidates discarded as false positives with reasons; journals with 0 in-window results; anything in `unreachable`, `truncated_journals` or `enrich_failures`; the per-journal scan counts; the date-precision caveat for EES, Chem Soc Rev, Joule and Science; any COF/PBA papers flagged (excluded from Topic 5 by policy); and the caveat that papers from the last 1–2 days may not yet be indexed by Crossref. State the discovery mode: "Crossref REST API via scheduled GitHub Actions job; per-journal online/registration date-field strategy."
- "## Deduplication": prior digests checked and DOIs excluded.
- A divider, then an italic footer "Generated by scheduled task. Next run: next Monday 9 AM KST." and a `>` blockquote disclaimer that dates come from Crossref (`published-online` where deposited, registration date otherwise) and should be verified against primary sources, and that entries without a retrievable abstract are DOI-only per policy.

If NO qualifying papers survive filtering, STILL create the page with every section reading "No new papers found this week in the target journals." and a Summary saying so.

## 7. Post to Notion
- Create a NEW sub-page UNDER parent page `342e998e-0380-802d-9c8b-ecaae70db509` using `mcp__Notion__notion-create-pages` with parent type `page_id`.
- Page title EXACTLY: "Weekly Paper Digest — YYYY-MM-DD" using RUN_DATE (em dash "—"). Page icon 📄.
- Before creating, confirm no sub-page already exists for RUN_DATE. If one does, do NOT create a second — stop and report it. After creation, capture the new page URL.
- If Notion posting fails, do NOT silently drop the report: write it to `/tmp/digest-RUN_DATE.md`, print it in full in your final message, and state exactly what failed and why.

## 8. Report completion
Output a one-line summary: RUN_DATE, coverage window, papers per topic, and the new Notion page URL.

Never invent DOIs, dates, authors or findings. If something is unverifiable, exclude it or list it under the access rule.
