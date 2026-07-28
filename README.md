# CitePilot

CitePilot reads a web page, scores how quotable it is to AI answer engines
(ChatGPT, Perplexity, Gemini, Google AI Overviews, Claude), fixes the weak
parts, and verifies the improvement - either from the command line or through a
browser dashboard.

The engine is a pipeline of small, single-purpose phases. Each phase is a
standalone CLI that reads and writes normalized JSON, so you can run any stage
on its own; the dashboard (`app.py`) simply orchestrates them behind a UI.

## Install

```bash
pip install -r requirements.txt
```

Model-backed features (rewrite, citation test, sentiment) need an API key:

```bash
# either export it, or put it in a gitignored .env file as ANTHROPIC_API_KEY=...
export ANTHROPIC_API_KEY=sk-ant-...
```

## The dashboard

```bash
python app.py        # then open http://127.0.0.1:8760
```

Tabs:

| Tab | What it does | Cost |
| --- | --- | --- |
| **Analyze** | Scores a page 0-100 on six citation-readiness signals, with fixes ranked by impact. | Free |
| **AEO Audit** | The same signals as a pass / warn / fail checklist - schema, Open Graph, canonical, HTTPS/HSTS, JS-free rendering - grouped by category and exportable as CSV. | Free |
| **Rewrite** | Claude rewrites the weak parts, then we re-score to prove the lift. | API |
| **Schema** | Generates ready-to-paste JSON-LD (Article / FAQ / HowTo). | Free |
| **Report** | A shareable one-page client report (score, grade, fixes, schema) as HTML. | Free |
| **Competitors** | Asks your customers' questions with live web search and shows which domains win the citations. | API |
| **Sentiment** | Gets each AI answer and classifies how your brand is portrayed. | API |
| **History** | Every analyze run is saved with a timestamp; "Pulse" re-checks tracked pages to build a trend. | Free |

"Free" tabs are rule-based and call no model. "API" tabs call Claude and consume
credit.

## The pipeline (CLI)

Each phase writes to `./output/<...>/` and prints the same JSON to stdout.

**Phase 1 - Ingest** (`ingest.py`): extract clean, structured JSON from a URL,
a local HTML file, or Markdown/plain text.

```bash
python ingest.py --url https://example.com/article
python ingest.py --file ./page.html
python ingest.py --text ./notes.md
```

`body_text` is flattened to plain text, so ingestion also records `list_count`
and `table_count` while parsing - letting later phases score structure
identically whether the source was HTML or Markdown. Both fields are optional;
payloads produced before they existed still analyze correctly.

**Phase 2 - Analyze** (`analyzer.py`): rule-based scoring across six factors
(header quality, answer-first structure, lists & tables, factual specificity,
byline authority, recency). No model, no key.

```bash
python analyzer.py --input ./output/<slug>.json
```

**Phase 3 - Schema** (`generator.py`): detect the best schema type and emit
paste-ready JSON-LD.

```bash
python generator.py --input ./output/<slug>.json          # auto-detects article/faq/howto
python generator.py --input ./output/<slug>.json --type faq
```

**Phase 4 - Rewrite** (`rewriter.py`): rewrite content so answer engines are
more likely to quote it (answer-first openings, fact density, explicit
structure). Emits a Phase 1-shaped payload, so Phase 2/3 run on the rewrite
unchanged to prove the score lift. Needs `ANTHROPIC_API_KEY`.

```bash
python rewriter.py --input ./output/<slug>.json
python rewriter.py --input ./output/<slug>.json --analysis ./output/analyzed/<slug>.json
python rewriter.py --input ./output/<slug>.json --dry-run   # build the request without spending
```

The default model is `claude-opus-4-8`; override with `--model`.

**Phase 5 - Report** (`rubric.py`): turn a Phase 2 analysis into a client-facing
report - overall grade, fixes ranked by points, strengths, paste-ready schema,
and before/after proof. Rule-based and free.

```bash
python rubric.py --input ./output/analyzed/<slug>.json --html
python rubric.py --input ./output/analyzed/<slug>.json --rewrite ./output/rewritten/<slug>.json --html
```

**Phase 6 - Citation harness** (`harness.py`): ask AI answer engines real
questions and check whether a target domain shows up in the sources they cite -
the "did it actually work" step. Claude works today via its web-search tool;
other engines are pluggable slots that activate once their keys exist.

```bash
python harness.py --target example.com --query "how to descale a coffee maker"
python harness.py --target example.com --from-rewrite ./output/rewritten/<slug>.json
python harness.py --list-providers
```

**Phase 7 - Dashboard** (`app.py`): the Flask UI over all of the above, plus
`audit.py` (the AEO Audit checklist) and `db.py` (SQLite history in
`citepilot.db`).

## Architecture

Ingestion normalizes any source into one payload; every downstream phase reads
that payload (and, where useful, the Phase 2 analysis) and writes its own JSON.
The dashboard wires them together; each phase is also a standalone CLI.

```
 URL / HTML / Markdown
          │
        Ingest ──────────────► payload (title, headers, body, dates, list/table counts)
          │
          ├─► Analyze ─────────► score + six-factor breakdown
          │       │
          │       ├─► Report ──► grade, ranked fixes, HTML  (+ schema, + rewrite proof)
          │       └─► AEO Audit► pass / warn / fail checklist (+ OG, canonical, HTTPS, SSR)
          │
          ├─► Schema ──────────► paste-ready JSON-LD (Article / FAQ / HowTo)
          │
          └─► Rewrite ↻ ───────► rewritten payload ──► re-Analyze ──► proof of lift

 Citation Harness (independent): ask AI engines a question ──► is the target domain cited?
```

| Phase | Responsibility | Code |
| --- | --- | --- |
| **1. Ingest** | Fetch a URL/HTML/Markdown source and normalize it to structured JSON; count real lists/tables before the text is flattened | `ingest.py` |
| **2. Analyze** | Rule-based scoring across six citation-readiness factors | `analyzer.py`, `rules.py` |
| **3. Schema** | Detect the best schema type and emit paste-ready JSON-LD | `generator.py`, `templates.py` |
| **4. Rewrite** | Claude rewrites the weak parts, then the result is re-scored to prove the lift | `rewriter.py` |
| **5. Report** | Turn an analysis into a client report - grade, ranked fixes, strengths, before/after | `rubric.py` |
| **6. Citation harness** | Ask AI answer engines real questions and check whether the target domain is cited | `harness.py` |
| **AEO Audit** | Present the same signals as a category-grouped pass/warn/fail checklist plus cheap technical checks | `audit.py` |
| **7. Dashboard** | Flask UI over every phase, with SQLite history and a manual "pulse" | `app.py`, `db.py` |

## Project structure

```
.
├── app.py            # Phase 7 - Flask dashboard that orchestrates every phase
├── ingest.py         # Phase 1 - URL / HTML / Markdown → normalized JSON payload
├── analyzer.py       # Phase 2 - validate the payload, run the scorers, total the score
├── rules.py          # Phase 2 - the six citation-readiness scorers
├── generator.py      # Phase 3 - detect schema type, build & merge JSON-LD
├── templates.py      # Phase 3 - Article / FAQ / HowTo JSON-LD builders
├── rewriter.py       # Phase 4 - Claude rewrite for citation-readiness
├── rubric.py         # Phase 5 - analysis → client report (JSON + shareable HTML)
├── harness.py        # Phase 6 - multi-AI citation test harness (Claude live today)
├── audit.py          # AEO Audit - pass/warn/fail checklist over the signals
├── db.py             # SQLite persistence for History / trends
├── templates/
│   └── dashboard.html  # Single-page dashboard UI (all tabs)
├── requirements.txt
├── citepilot.db      # Local run history (gitignored)
├── output/           # Generated pipeline artifacts (gitignored)
└── README.md
```

## Known limitations

- JavaScript-rendered pages are not fully scraped unless the content is already
  present in the initial HTML response.
- The parser uses common structural tags and metadata, so unusually structured
  pages may need later-phase cleanup.
- Markdown/plain-text ingestion is heuristic, not a full CommonMark parse; nested
  lists count as separate blocks, and there is no author/meta/date signal, so
  byline and recency scores will be lower than the same content as HTML.
- Recency needs a real published/updated date - the tool never invents one.
- Google AI Overviews has no public API, so it cannot be citation-tested.
- Scores estimate on-page content signals, not real-world ranking or domain
  authority - use them to make content more quotable, not to predict rankings.
