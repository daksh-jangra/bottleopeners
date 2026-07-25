# Content Ingestion Phase 1

This module extracts clean, structured JSON from a URL, a local HTML file, or a local Markdown/plain text file. It is intentionally limited to ingestion only so later phases can consume the normalized output without needing to know the source format.

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
python ingest.py --url https://example.com/article
python ingest.py --file ./sample.html
python ingest.py --text ./notes.md
```

Each run writes a JSON file to `./output/<slugified-title>.json` and also prints the same JSON object to stdout.

`body_text` is flattened to plain text, which discards list and table markup. So ingestion also records `list_count` and `table_count` while parsing, letting later phases score structure identically whether the source arrived as HTML or Markdown. Both fields are optional on input — payloads produced before they existed still analyze correctly.

## Example Runs

URL input:

```bash
python ingest.py --url https://example.com/blog/post
```

Local HTML input:

```bash
python ingest.py --file ./fixtures/article.html
```

Markdown input:

```bash
python ingest.py --text ./fixtures/article.md
```

## Phase 4: LLM Rewriter

`rewriter.py` rewrites Phase 1 content so AI answer engines are more likely to quote it (answer-first openings, fact-dense sentences, explicit list/table structure, specific headers). It emits a Phase 1-shaped payload, so Phase 2 and Phase 3 can run on the rewrite unchanged to prove the score lift.

This is the first phase that calls a model, so it needs an API key at run time:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
python rewriter.py --input ./output/<slug>.json
python rewriter.py --input ./output/<slug>.json --analysis ./output/analyzed/<slug>.json
```

Passing `--analysis` (a Phase 2 output) makes the rewrite target that page's specific weaknesses. Output is written to `./output/rewritten/<slug>.json`.

Until the key is set, `--dry-run` builds and prints the exact request that would be sent — the whole pipeline can be verified without a key or any spend:

```bash
python rewriter.py --input ./output/<slug>.json --dry-run
```

The default model is `claude-opus-4-8`; override with `--model`.

## Known Limitations

- JavaScript-rendered pages will not be fully scraped unless the content is already present in the initial HTML response.
- The parser uses common structural tags and metadata, so unusually structured pages may require later-phase cleanup.
- Markdown/plain text ingestion is heuristic and does not attempt a full CommonMark parse. List and table blocks are counted, but nested lists count as separate blocks.
- Markdown/plain text input carries no author, meta description, or date signals, so byline and recency scores will be lower than the same content ingested as HTML.
