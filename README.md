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

## Known Limitations

- JavaScript-rendered pages will not be fully scraped unless the content is already present in the initial HTML response.
- The parser uses common structural tags and metadata, so unusually structured pages may require later-phase cleanup.
- Markdown/plain text ingestion is heuristic and does not attempt a full CommonMark parse.
