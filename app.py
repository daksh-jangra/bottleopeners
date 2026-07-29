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
import audit
import brandindex
import db
import generator
import harness
import ingest
import rewriter
import rubric
import teardown
from common import CLASSIFIER_MODEL, DEFAULT_MODEL, load_dotenv

app = Flask(__name__)
db.init_db()

# Max questions accepted per Competitors/Sentiment run, to bound cost and time
# (each question is a live web-search + model call). Also surfaced in the UI.
MAX_QUERIES_PER_RUN = 10


def _server_error(exc: Exception):
    """Log the real error server-side, return a generic message to the client."""
    app.logger.exception("Unhandled error in API endpoint: %s", exc)
    return jsonify({"error": "Unexpected server error. Check the server logs."}), 500


def _snapshot_bvi(target: str) -> None:
    """Recompute + snapshot the Brand Visibility Index after a component run.

    Best-effort: a failure here must never break the endpoint that triggered it,
    so any error is logged and swallowed.
    """
    try:
        brandindex.brand_index(target, record=True)
    except Exception:  # noqa: BLE001 - snapshotting is non-critical
        app.logger.exception("Failed to snapshot brand index for %s", target)


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
        db.save_run("analyze", url, report["score"], {
            "grade": report["grade"],
            "title": payload.get("title"),
        })
        _snapshot_bvi(url)
        return jsonify(report)
    except ingest.IngestError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # keep the UI honest about failures
        return _server_error(exc)


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
        return _server_error(exc)


@app.post("/api/competitors")
def api_competitors():
    from collections import Counter

    data = request.json or {}
    target = (data.get("target") or "").strip()
    queries = data.get("queries") or []
    if isinstance(queries, str):
        queries = queries.splitlines()
    queries = [q.strip() for q in queries if isinstance(q, str) and q.strip()][:MAX_QUERIES_PER_RUN]

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
        result = {
            "target": target_norm,
            "queries": len(queries),
            "target_cited": target_cited,
            "competitors": competitors,
        }
        # Headline number for the trend line: citation rate as a percentage.
        rate = round(100 * target_cited / len(queries)) if queries else 0
        db.save_run("competitors", target_norm, rate, result)
        _snapshot_bvi(target_norm)
        return jsonify(result)
    except harness.HarnessError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return _server_error(exc)


@app.post("/api/teardown")
def api_teardown():
    """Diff a competitor's page against yours: where they beat you, and how to fix it."""
    data = request.json or {}
    your_url = (data.get("your_url") or "").strip()
    competitor_url = (data.get("competitor_url") or "").strip()
    if not your_url or not competitor_url:
        return jsonify({"error": "Enter both your page URL and a competitor page URL."}), 400

    try:
        you_payload = _fetch_payload(your_url)
        them_payload = _fetch_payload(competitor_url)
        you = analyzer.build_analysis(you_payload)
        them = analyzer.build_analysis(them_payload)
        result = teardown.compare(you, them)
        result["your"] = {"url": your_url, "title": you_payload.get("title")}
        result["competitor"] = {"url": competitor_url, "title": them_payload.get("title")}
        db.save_run("analyze", your_url, you["total_score"], {"title": you_payload.get("title")})
        _snapshot_bvi(your_url)
        return jsonify(result)
    except ingest.IngestError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return _server_error(exc)


@app.post("/api/niche")
def api_niche():
    data = request.json or {}
    brand = (data.get("brand") or "").strip()
    keywords = (data.get("keywords") or "").strip()
    industry = (data.get("industry") or "").strip()

    if not brand and not keywords:
        return jsonify({"error": "Enter your brand/domain or some target keywords."}), 400
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY is not set; add it to .env to suggest questions."}), 400

    try:
        questions = harness.suggest_queries(brand, keywords, industry, CLASSIFIER_MODEL)
        if not questions:
            return jsonify({"error": "Could not generate questions; try adding keywords or an industry."}), 400
        return jsonify({"brand": brand, "questions": questions})
    except harness.HarnessError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return _server_error(exc)


@app.post("/api/sentiment")
def api_sentiment():
    data = request.json or {}
    brand = (data.get("brand") or "").strip()
    queries = data.get("queries") or []
    if isinstance(queries, str):
        queries = queries.splitlines()
    queries = [q.strip() for q in queries if isinstance(q, str) and q.strip()][:MAX_QUERIES_PER_RUN]

    if not brand:
        return jsonify({"error": "Enter your brand name."}), 400
    if not queries:
        return jsonify({"error": "Enter at least one question."}), 400
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return jsonify({"error": "ANTHROPIC_API_KEY is not set; add it to .env to run sentiment checks."}), 400
    try:
        result = harness.run_sentiment(brand, queries, DEFAULT_MODEL)
        # Headline number for the trend line: share of answers that were positive.
        positive_pct = result.get("mix", {}).get("positive", {}).get("pct", 0)
        db.save_run("sentiment", brand, positive_pct, result)
        _snapshot_bvi(brand)
        return jsonify(result)
    except harness.HarnessError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return _server_error(exc)


@app.post("/api/schema")
def api_schema():
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "Enter a page URL."}), 400
    try:
        payload = _fetch_payload(url)
        schema = _schema_for(payload)
        schema["title"] = payload.get("title")
        return jsonify(schema)
    except ingest.IngestError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return _server_error(exc)


@app.post("/api/audit")
def api_audit():
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "Enter a page URL."}), 400
    try:
        response = ingest.fetch_url_response(url)
        html = response.text
        payload = ingest.extract_html_payload(html, url)
        analysis = analyzer.build_analysis(payload)
        result = audit.build_audit(payload, analysis, html, response.url, dict(response.headers))
        return jsonify(result)
    except ingest.IngestError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return _server_error(exc)


@app.post("/api/report")
def api_report():
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "Enter a page URL."}), 400
    try:
        payload = _fetch_payload(url)
        analysis = analyzer.build_analysis(payload)
        schema = _schema_for(payload)
        report = rubric.build_report(analysis, schema, None)
        return jsonify({
            "title": payload.get("title"),
            "score": report["score"],
            "grade": report["grade"],
            "status": report["status"],
            "html": rubric.render_html(report),
        })
    except ingest.IngestError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return _server_error(exc)


@app.get("/api/tracked")
def api_tracked():
    return jsonify({"targets": db.tracked_targets()})


@app.post("/api/history")
def api_history():
    data = request.json or {}
    target = (data.get("target") or "").strip()
    kind = (data.get("kind") or "analyze").strip()
    if not target:
        return jsonify({"error": "Enter a page URL."}), 400
    return jsonify({"target": target, "kind": kind, "runs": db.history(kind, target)})


@app.post("/api/pulse")
def api_pulse():
    """Re-check every tracked page now and snapshot a fresh score (a manual 'pulse')."""
    targets = [t["target"] for t in db.tracked_targets("analyze")]
    results = []
    for url in targets:
        try:
            payload = _fetch_payload(url)
            analysis = analyzer.build_analysis(payload)
            report = rubric.build_report(analysis, _schema_for(payload), None)
            db.save_run("analyze", url, report["score"], {"grade": report["grade"], "title": payload.get("title")})
            _snapshot_bvi(url)
            results.append({"target": url, "score": report["score"], "ok": True})
        except Exception as exc:
            results.append({"target": url, "ok": False, "error": str(exc)})
    return jsonify({"pulsed": len(results), "results": results})


@app.get("/api/brand-index")
def api_brand_index():
    """Brand Visibility Index for one domain, or the list of known brands.

    GET /api/brand-index            -> {"brands": [...]}
    GET /api/brand-index?domain=... -> full index view for that domain
    """
    domain = (request.args.get("domain") or "").strip()
    if not domain:
        return jsonify({"brands": brandindex.list_brands()})
    result = brandindex.brand_index(domain, record=False)
    if result["index"] is None:
        return jsonify({"error": f"No scored runs yet for {result['domain']}. "
                                 "Analyze a page or run a citation check first."}), 404
    return jsonify(result)


if __name__ == "__main__":
    load_dotenv()
    app.run(host="127.0.0.1", port=8760, debug=False)
