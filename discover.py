#!/usr/bin/env python3
"""
Weekly Paper Digest - discovery stage.

Fetches candidate papers from Crossref across 16 target journals for a
1-week coverage window, filters by topic keywords (recall-first), enriches
Nature-family entries with abstracts from nature.com meta tags, and writes
candidates.json.

The model consumes candidates.json and does precision filtering + summaries.

Usage:  python discover.py [--run-date YYYY-MM-DD] [--out candidates.json]
"""
import argparse, json, os, re, sys, time, unicodedata
import urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

# Crossref asks API clients to identify themselves; supplying a contact address
# puts requests in its faster "polite pool". Kept out of the source so this repo
# can be public -- CI passes it in from a secret. Requests still work without it.
MAILTO = os.environ.get("CROSSREF_MAILTO", "").strip()
UA = ("WeeklyPaperDigest/1.0 (mailto:%s)" % MAILTO) if MAILTO else "WeeklyPaperDigest/1.0"
# nature.com rejects non-browser agents from datacenter IPs with a 200-OK
# challenge page. Crossref is fine with (and asks for) the polite UA above.
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

# Springer Nature's own metadata API is the sanctioned way to get abstracts for
# 10.1038 DOIs. Free key from https://dev.springernature.com/ (5000 req/day);
# our use is a handful per week. Without a key we fall back to scraping
# nature.com, which works from a residential IP but not from CI.
SPRINGER_KEY = os.environ.get("SPRINGER_API_KEY", "").strip()

# (display name, ISSN, date-filter strategy)
#
# "online"  -> publisher deposits day-precision published-online.
# "created" -> publisher deposits year-only or no published-online, so the
#              online-date filter silently returns ZERO. Verified 2026-08:
#              EES/ChemSocRev -> [[2026]]; Joule/Science -> None.
#              Crossref registration date (created) tracks first publication
#              within ~1 day and is the only usable day-precision field.
JOURNALS = [
    ("Nature",                          "1476-4687", "online"),
    ("Nature Energy",                   "2058-7546", "online"),
    ("Nature Materials",                "1476-4660", "online"),
    ("Nature Chemistry",                "1755-4349", "online"),
    ("Nature Communications",           "2041-1723", "online"),
    ("Nature Reviews Materials",        "2058-8437", "online"),
    ("Nature Nanotechnology",           "1748-3395", "online"),
    ("Nature Synthesis",                "2731-0582", "online"),
    ("Science",                         "0036-8075", "created"),
    ("Joule",                           "2542-4351", "created"),
    ("Energy & Environmental Science",  "1754-5706", "created"),
    ("JACS",                            "1520-5126", "online"),
    ("Chemical Society Reviews",        "1460-4744", "created"),
    ("Advanced Materials",              "1521-4095", "online"),
    ("Advanced Energy Materials",       "1614-6840", "online"),
    ("ACS Energy Letters",              "2380-8195", "online"),
]

# Recall-first. Precision is the model's job downstream.
DASH = r"\s‐-―-"
TOPIC_PATTERNS = [
    ("1-metal-anode",
     r"(metal anode|anode-?free|anodeless|"
     r"(?:lithium|sodium|potassium|zinc|magnesium|calcium|Li|Na|K|Zn|Mg|Ca)[" + DASH + r"]metal\b|"
     r"dendrites?\b|dendritic (?:growth|deposit|lithium|sodium|zinc|potassium|"
     r"magnesium|calcium|Li|Na|Zn|K|Mg|Ca)|"
     r"plating(?:/| and | ?-? ?)stripping|coulombic efficiency|"
     r"metal batter)"),
    ("2-anode-free",
     r"(anode-?free|anode-?less|anode free)"),
    ("3-sei-interface",
     r"(\bSEI\b|\bCEI\b|interphase|"
     r"solid[" + DASH + r"]electrolyte[" + DASH + r"]interphase|"
     r"cathode[" + DASH + r"]electrolyte[" + DASH + r"]interphase|"
     r"electrode[" + DASH + r"]electrolyte interface|"
     r"artificial (?:SEI|interphase|layer)|"
     r"electrolyte (?:design|engineering|additive)|"
     r"interfacial (?:layer|engineering|chemistry))"),
    ("4-current-collector",
     r"(current collector|lithiophil|sodiophil|zincophil|potassiophil|"
     r"3D host|porous host|nucleation seed)"),
    ("5-mof",
     r"(metal[" + DASH + r"]organic framework|\bMOFs?\b|MOF[" + DASH + r"]derived|"
     r"zeolitic imidazolate|\bZIF-?\d*\b|"
     r"(?:porous )?coordination polymer|conductive MOF)"),
]
TOPIC_RE = [(name, re.compile(pat, re.I)) for name, pat in TOPIC_PATTERNS]

# Topic 5 needs a battery/interface anchor -- bare "MOF" is far too noisy
# (drug delivery, catalysis, gas storage all match otherwise).
MOF_ANCHOR = re.compile(
    r"(anode|cathode|batter|electrolyte|interphase|\bSEI\b|\bCEI\b|separator|interlayer|"
    r"dendrit|lithiophil|sodiophil|zincophil|current collector|"
    r"plating|stripping|single[\s-]ion|solid[\s-]state|quasi[\s-]solid|host|"
    r"\bLi\b|\bNa\b|\bZn\b|lithium|sodium|potassium|zinc|magnesium|calcium|"
    r"energy storage)", re.I)

# Every candidate must sit in the battery / electrochemistry domain. Without
# this gate, cross-domain homographs slip through: "dendritic cell" (immunology)
# pulled a melanoma immunotherapy paper into Topic 1 during validation.
DOMAIN_GATE = re.compile(
    r"(batter|anode|cathode|electrolyt|electrochemi|electrode|"
    r"energy storage|\bcell(?:s)? cycl|cycling stability|"
    r"interphase|\bSEI\b|\bCEI\b|current collector|"
    r"ion (?:transport|conduct|storage)|charge[\s-]discharge|"
    r"plating|stripping|areal capacity|\bmAh\b|\bAh g|specific capacity)", re.I)

# Excluded from Topic 5 per spec; recorded for Search Notes instead.
COF_PBA = re.compile(
    r"(covalent organic framework|\bCOFs?\b|prussian blue|\bPBAs?\b)", re.I)

# Front matter / editorial noise (Science and RSC register these as journal-article).
JUNK_TITLE = re.compile(
    r"^(contents list|in science journals|editorial board|front cover|back cover|"
    r"inside front cover|inside back cover|correction|erratum|retraction|"
    r"expression of concern|news at a glance|author correction|publisher correction)",
    re.I)


def clean(text):
    """Strip JATS/HTML tags, collapse whitespace, normalize unicode."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                 ("&quot;", '"'), ("&#38;", "&"), ("&nbsp;", " ")):
        text = text.replace(a, b)
    text = unicodedata.normalize("NFKC", text)
    # Wiley deposits U+2010 HYPHEN in titles/abstracts ("Anode‐Free"); NFKC keeps
    # it, so the ASCII "anode-?free|anode-?less" patterns silently miss those
    # papers (verified 2026-09-03 on 10.1002/adma.74820, 10.1002/aenm.71042).
    text = re.sub(r"[‐‑‒–—―−]", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def get(url, timeout=45, retries=3, ua=UA):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as exc:          # noqa: BLE001 - report, don't kill the run
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise last


def date_parts(item, key):
    dp = (item.get(key) or {}).get("date-parts") or [[]]
    return dp[0] if dp and dp[0] else []


def fmt_date(parts):
    if len(parts) >= 3:
        return "%04d-%02d-%02d" % tuple(parts[:3])
    if len(parts) == 2:
        return "%04d-%02d" % tuple(parts[:2])
    if len(parts) == 1:
        return "%04d" % parts[0]
    return ""


ROWS = 1000   # Crossref maximum per request; lowered only by tests


def fetch_journal(issn, strategy, start, end):
    """Return (items, total_results, error_or_None).

    Pages with Crossref deep-paging cursors so a journal with more than ROWS
    records in the window (Nature Communications is at ~290/week) is still
    scanned completely instead of silently truncated at the first page."""
    if strategy == "online":
        filt = "from-online-pub-date:%s,until-online-pub-date:%s" % (start, end)
    else:
        filt = "from-created-date:%s,until-created-date:%s" % (start, end)
    base = ("https://api.crossref.org/journals/%s/works"
            "?filter=%s&rows=%d"
            "&select=DOI,title,published-online,created,abstract,author,URL"
            % (issn, filt, ROWS))
    if MAILTO:
        base += "&mailto=%s" % MAILTO
    items, total, cursor = [], 0, "*"
    while True:
        try:
            msg = json.loads(get(base + "&cursor=" + urllib.parse.quote(cursor)))["message"]
        except Exception as exc:          # noqa: BLE001
            return [], 0, "%s: %s" % (type(exc).__name__, exc)
        page = msg.get("items", [])
        total = msg.get("total-results", 0)
        items.extend(page)
        cursor = msg.get("next-cursor")
        if not page or len(items) >= total or not cursor:
            return items, total, None
        time.sleep(0.4)


def classify(title, abstract):
    """Return matched topic keys, honoring the MOF anchor rule."""
    blob = title + " " + abstract
    if not DOMAIN_GATE.search(blob):
        return []
    hits = []
    for key, rx in TOPIC_RE:
        if not rx.search(blob):
            continue
        if key == "5-mof" and not MOF_ANCHOR.search(blob):
            continue        # bare-MOF noise: drug delivery, catalysis, gas sorption
        hits.append(key)
    return hits


def authors_of(item):
    au = item.get("author") or []
    if not au:
        return ""
    first = au[0]
    nm = ("%s %s" % (first.get("given", ""), first.get("family", ""))).strip()
    if not nm:
        nm = first.get("name", "")
    return (nm + " et al.") if len(au) > 1 else nm


ABS_RE = re.compile(r'<meta[^>]*name="dc\.description"[^>]*content="([^"]*)"', re.I)
ONLINE_RE = re.compile(r'<meta[^>]*name="citation_online_date"[^>]*content="([^"]*)"', re.I)


def springer_abstract(cand):
    """Ask Springer Nature's metadata API for the abstract. Returns True on
    success. No-op when no API key is configured."""
    if not SPRINGER_KEY:
        return False
    url = ("https://api.springernature.com/meta/v2/json?q=doi:%s&api_key=%s"
           % (urllib.parse.quote(cand["doi"]), urllib.parse.quote(SPRINGER_KEY)))
    try:
        payload = json.loads(get(url, timeout=40, retries=2))
    except Exception as exc:              # noqa: BLE001
        cand["enrich_error"] = "springer api: %s: %s" % (type(exc).__name__, exc)
        return False

    records = payload.get("records") or []
    if not records:
        cand["enrich_error"] = "springer api: DOI not in index"
        return False

    rec = records[0]
    abstract = rec.get("abstract")
    if isinstance(abstract, dict):        # some records nest it under {"p": ...}
        abstract = abstract.get("p") or abstract.get("#text") or ""
    if isinstance(abstract, list):
        abstract = " ".join(str(x) for x in abstract)
    abstract = clean(abstract or "")
    if not abstract:
        cand["enrich_error"] = "springer api: record has no abstract"
        return False

    cand["abstract"] = abstract
    cand["abstract_source"] = "springer nature meta api"
    cand.pop("enrich_error", None)
    online = rec.get("onlineDate") or rec.get("publicationDate")
    if online:
        cand["online_date_verified"] = str(online)[:10]
    return True


def enrich_nature(cand):
    """Springer Nature deposits abstracts to Crossref only for its open-access
    titles (Nature Communications). For the subscription titles the abstract
    has to come from the article page's dc.description meta tag -- meta tags
    only, never the ~350KB body.

    nature.com serves a challenge/interstitial page to datacenter IPs carrying
    a non-browser User-Agent: HTTP 200, no exception, and no meta tags. That
    made GitHub Actions runs lose every subscription-Nature abstract silently.
    Hence the browser User-Agent, the retries, and -- critically -- recording
    an explicit error when the page comes back without the tags."""
    # Publisher API first when a key is configured -- authenticated, allowed
    # from CI, and not subject to the bot challenge below.
    if springer_abstract(cand):
        return

    try:
        suffix = cand["doi"].split("/", 1)[1]
    except IndexError:
        return
    url = "https://www.nature.com/articles/%s" % suffix

    for attempt in range(3):
        try:
            html = get(url, timeout=45, retries=1, ua=BROWSER_UA)
        except Exception as exc:          # noqa: BLE001
            cand["enrich_error"] = "%s: %s" % (type(exc).__name__, exc)
            time.sleep(2.0 * (attempt + 1))
            continue

        head = html[:250000]
        m = ABS_RE.search(head)
        if m:
            cand["abstract"] = clean(m.group(1))
            cand["abstract_source"] = "nature.com dc.description"
            cand.pop("enrich_error", None)
            m = ONLINE_RE.search(head)
            if m:
                cand["online_date_verified"] = m.group(1).replace("/", "-")
            return

        # Fetched something, but not the article page we expected.
        cand["enrich_error"] = ("no dc.description in %d-byte response "
                                "(attempt %d) - likely a bot challenge"
                                % (len(html), attempt + 1))
        time.sleep(2.0 * (attempt + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-date", help="YYYY-MM-DD (KST). Default: today in KST.")
    ap.add_argument("--out", default="candidates.json")
    ap.add_argument("--no-enrich", action="store_true",
                    help="skip nature.com abstract enrichment")
    args = ap.parse_args()

    # tzdata-independent KST: UTC + 9h. Do NOT rely on TZ=Asia/Seoul -- Git Bash
    # on Windows has no zoneinfo and silently falls back to UTC, which rolls
    # RUN_DATE back a day for runs near 00:00 UTC.
    if args.run_date:
        run = datetime.strptime(args.run_date, "%Y-%m-%d").date()
    else:
        run = (datetime.now(timezone.utc) + timedelta(hours=9)).date()
    start, end = run - timedelta(days=8), run - timedelta(days=1)
    S, E = start.isoformat(), end.isoformat()

    if not MAILTO:
        print("NOTE: CROSSREF_MAILTO not set - using Crossref's public pool "
              "(slower, no failure notifications).")
    print("RUN_DATE      : %s (KST)" % run)
    print("COVERAGE      : %s .. %s (inclusive)" % (S, E))
    print("=" * 78)

    report, candidates, errors, cof_pba, truncated = [], [], [], [], []

    for name, issn, strategy in JOURNALS:
        items, total, err = fetch_journal(issn, strategy, S, E)
        if err:
            errors.append({"journal": name, "error": err})
            print("%-32s FETCH FAILED  %s" % (name, err))
            continue

        kept = 0
        for it in items:
            title = clean((it.get("title") or [""])[0])
            if not title or JUNK_TITLE.match(title):
                continue
            abstract = clean(it.get("abstract", ""))
            topics = classify(title, abstract)
            if not topics:
                continue

            if strategy == "online":
                dparts = date_parts(it, "published-online")
                dkind = "published-online (Crossref)"
            else:
                dparts = date_parts(it, "created")
                dkind = "Crossref registration date (~online)"
            if len(dparts) < 3:        # refuse to guess a day
                continue
            dstr = fmt_date(dparts)
            if not (S <= dstr <= E):
                continue

            cand = {
                "doi": it["DOI"],
                "title": title,
                "journal": name,
                "authors": authors_of(it),
                "date": dstr,
                "date_kind": dkind,
                "url": it.get("URL") or ("https://doi.org/" + it["DOI"]),
                "abstract": abstract,
                "abstract_source": "crossref" if abstract else "",
                "topics": topics,
            }
            if COF_PBA.search(title + " " + abstract):
                cand["cof_pba_flag"] = True
                cof_pba.append(cand["doi"])
            candidates.append(cand)
            kept += 1

        report.append((name, strategy, total, len(items), kept))
        print("%-32s %-8s scanned=%4d  fetched=%4d  candidates=%3d"
              % (name, strategy, total, len(items), kept))
        if total > len(items):
            truncated.append(name)
            print("   !! TRUNCATED: total-results %d > returned %d -- add cursor paging"
                  % (total, len(items)))
        time.sleep(0.4)

    if not args.no_enrich:
        need = [c for c in candidates
                if not c["abstract"] and c["doi"].startswith("10.1038/")]
        if need:
            print("-" * 78)
            print("Enriching %d Nature-family abstracts from nature.com ..." % len(need))
            for c in need:
                enrich_nature(c)
                time.sleep(0.5)

    no_abs = [c for c in candidates if not c["abstract"]]

    out = {
        "run_date": run.isoformat(),
        "window_start": S,
        "window_end": E,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "discovery_mode": "Crossref REST API (single path)",
        "journal_stats": [
            {"journal": n, "date_field": s, "scanned": t,
             "fetched": f, "candidates": k}
            for n, s, t, f, k in report
        ],
        "unreachable": errors,
        "truncated_journals": truncated,
        "cof_pba_flagged": cof_pba,
        "no_abstract_dois": [c["doi"] for c in no_abs],
        "enrich_failures": [{"doi": c["doi"], "journal": c["journal"],
                             "error": c["enrich_error"]}
                            for c in candidates if c.get("enrich_error")],
        "candidates": candidates,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print("=" * 78)
    print("candidates: %d   journals ok: %d   unreachable: %d"
          % (len(candidates), len(report), len(errors)))
    print("without abstract (apply access rule): %d %s"
          % (len(no_abs), [c["doi"] for c in no_abs]))
    for c in candidates:
        if c.get("enrich_error"):
            print("  enrich FAILED %s (%s): %s"
                  % (c["doi"], c["journal"], c["enrich_error"]))
    print("written -> %s" % args.out)
    print("-" * 78)
    for c in candidates:
        print("[%s] %-30s %s" % (c["date"], c["journal"][:30], c["doi"]))
        print("    %s" % c["title"][:110])
        print("    abstract=%-3s (%s)   topics: %s"
              % ("yes" if c["abstract"] else "NO",
                 c["abstract_source"] or "-", ", ".join(c["topics"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
