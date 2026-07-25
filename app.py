"""Phase 7 web dashboard for the AI citation-readiness engine.

A thin Flask app that wraps the existing pipeline (Phases 1-6) in a browser UI.
Enter a URL and it runs ingest -> analyze -> schema -> report and renders the
result; the Rewrite and Citation-test actions call Phases 4 and 6 on demand.

Run it:
  python app.py        # then open http://127.0.0.1:8760

The heavy lifting stays in the phase modules; this file only orchestrates them
and serves JSON to the dashboard.
"""

from __future__ import annotations

import json
import os
from typing import Any

from flask import Flask, jsonify, render_template, request

import analyzer
import generator
import harness
import ingest
import rewriter
import rubric

app = Flask(__name__)

DEFAULT_MODEL = "claude-opus-4-8"


def load_dotenv(path: str = ".env") -> None:
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = value


def _schema_for(payload: dict[str, Any]) -> dict[str, Any]:
    detected_type = generator.detect_type(payload, None)
    generated, _notes, suggested_merge = generator.generate_schema(payload, detected_type)
    ready = generator.choose_ready_to_paste_schema(generated, suggested_merge)
    ready_json = json.dumps(ready, indent=2, ensure_ascii=False)
    return {
        "detected_type": detected_type,
        "ready_to_paste": f'<script type="application/ld+json">{ready_json}</script>',
    }


def _fetch_payload(url: str) -> dict[str, Any]:
    html = ingest.fetch_url(url)
    return ingest.extract_html_payload(html, url)


@app.get("/")
def index():
    return render_template("dashboard.html")


@app.post("/api/analyze")
def api_analyze():
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "Enter a page URL."}), 400
    try:
        payload = _fetch_payload(url)
        analysis = analyzer.build_analysis(payload)
        schema = _schema_for(payload)
        report = rubric.build_report(analysis, schema, None)
        report["page"] = {
            "title": payload.get("title"),
            "word_count": payload.get("word_count"),
            "headers": len(payload.get("headers", [])),
            "lists": payload.get("list_count"),
            "tables": payload.get("table_count"),
        }
        return jsonify(report)
    except ingest.IngestError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # keep the UI honest about failures
        return jsonify({"error": f"Unexpected error: {exc}"}), 500


@app.post("/api/rewrite")
def api_rewrite():
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "Enter a page URL."}), 400
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY is not set; add it to .env to enable rewrites."}), 400
    try:
        payload = _fetch_payload(url)
        before = analyzer.build_analysis(payload)
        weaknesses = rewriter.summarize_weaknesses(before)
        req = rewriter.build_request(payload, weaknesses, DEFAULT_MODEL)
        rewrite = rewriter.call_claude(req)
        new_payload = rewriter.assemble_rewritten_payload(payload, rewrite)
        after = analyzer.build_analysis(new_payload)
        return jsonify({
            "before": before["total_score"],
            "after": after["total_score"],
            "gain": after["total_score"] - before["total_score"],
            "changes": rewrite.get("changes", []),
            "new_title": new_payload.get("title"),
        })
    except rewriter.RewriterError as exc:
        return jsonify({"error": str(exc)}), 400
    except ingest.IngestError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Unexpected error: {exc}"}), 500


@app.post("/api/competitors")
def api_competitors():
    from collections import Counter

    data = request.json or {}
    target = (data.get("target") or "").strip()
    queries = data.get("queries") or []
    if isinstance(queries, str):
        queries = queries.splitlines()
    queries = [q.strip() for q in queries if isinstance(q, str) and q.strip()][:6]  # cap to bound cost/time

    if not target:
        return jsonify({"error": "Enter your domain."}), 400
    if not queries:
        return jsonify({"error": "Enter at least one question."}), 400
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY is not set; add it to .env to run citation checks."}), 400

    try:
        target_norm = harness.normalize_domain(target)
        counts: Counter = Counter()
        target_cited = 0
        for query in queries:
            urls, _answer = harness.run_claude(query, DEFAULT_MODEL)
            for domain in {harness.normalize_domain(u) for u in urls if harness.normalize_domain(u)}:
                counts[domain] += 1
            if any(harness.domains_match(target_norm, u) for u in urls):
                target_cited += 1
        competitors = [
            {"domain": d, "cited_in": c, "is_target": d == target_norm or d.endswith("." + target_norm)}
            for d, c in counts.most_common()
        ]
        return jsonify({
            "target": target_norm,
            "queries": len(queries),
            "target_cited": target_cited,
            "competitors": competitors,
        })
    except harness.HarnessError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": f"Unexpected error: {exc}"}), 500


if __name__ == "__main__":
    load_dotenv()
    app.run(host="127.0.0.1", port=8760, debug=False)
