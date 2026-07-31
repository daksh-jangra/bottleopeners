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
import threading
import time
from typing import Any

from flask import Flask, jsonify, render_template, request

import analyzer
import audit
import brandindex
import brief
import db
import export
import generator
import harness
import ingest
import monitor
import notify
import rewriter
import rubric
import sitemap
import teardown
from common import CLASSIFIER_MODEL, DEFAULT_MODEL, load_dotenv, slugify

app = Flask(__name__)
db.init_db()

# Max questions accepted per Competitors/Sentiment run, to bound cost and time
# (each question is a live web-search + model call). Also surfaced in the UI.
MAX_QUERIES_PER_RUN = 10

# Max pages analyzed in one site-wide batch run, and how many nested sitemaps a
# sitemap index is followed into. Bounds a large site to a predictable amount of
# work (analysis is free/rule-based, but each page is still a fetch). Surfaced
# in the UI so the cap is never a surprise.
MAX_BATCH_URLS = 25
MAX_NESTED_SITEMAPS = 5

# Opt-in background scheduler: when CITEPILOT_PULSE_MINUTES is set to a positive
# integer, a daemon thread runs a pulse on that cadence so regressions are
# caught without anyone clicking a button. Unset/0 keeps CitePilot fully manual.
_scheduler_state: dict[str, Any] = {"enabled": False, "minutes": 0, "last_run": None}


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


def _analyze_url(url: str) -> dict[str, Any]:
    """Fetch, score, and store one analyze run; return the report.

    Shared by the single-page endpoint and the site-wide batch. Deliberately
    does not snapshot the brand index - callers decide when (once per domain for
    a batch, rather than once per page).
    """
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
    return report


@app.post("/api/analyze")
def api_analyze():
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "Enter a page URL."}), 400
    try:
        report = _analyze_url(url)
        _snapshot_bvi(url)
        return jsonify(report)
    except ingest.IngestError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:  # keep the UI honest about failures
        return _server_error(exc)


def _discover_urls(target: str) -> list[str]:
    """Find a site's page URLs from its sitemap.

    Prefers the sitemaps declared in robots.txt, falling back to the
    conventional /sitemap.xml locations. A sitemap index is followed one level
    (up to MAX_NESTED_SITEMAPS children). All fetches go through ingest's
    SSRF-guarded fetcher; an unreachable or malformed sitemap is skipped, not
    fatal. Returns de-duplicated page URLs in document order.
    """
    origin = sitemap.origin_of(target)
    candidates: list[str] = []
    if origin:
        try:
            robots = ingest.fetch_url(origin + "/robots.txt")
            candidates = sitemap.sitemaps_from_robots(robots)
        except ingest.IngestError:
            candidates = []
    candidates = candidates or sitemap.candidate_sitemap_urls(target)

    seen: set[str] = set()
    urls: list[str] = []
    for sm_url in candidates:
        try:
            parsed = sitemap.parse_sitemap(ingest.fetch_url(sm_url))
        except ingest.IngestError:
            continue
        page_urls = list(parsed["urls"])
        for nested in parsed["sitemaps"][:MAX_NESTED_SITEMAPS]:
            try:
                page_urls.extend(sitemap.parse_sitemap(ingest.fetch_url(nested))["urls"])
            except ingest.IngestError:
                continue
        for url in page_urls:
            if url not in seen:
                seen.add(url)
                urls.append(url)
        if urls:
            break  # first sitemap that yields pages wins
    return urls


@app.post("/api/batch")
def api_batch():
    """Analyze a whole site: discover its sitemap, score every page, track them.

    Each page is stored as an analyze run, so the site's pages immediately show
    up under History and are re-checked by the Monitoring pulse - regression
    watch over a whole site instead of hand-added URLs.
    """
    target = (request.json or {}).get("target", "").strip()
    if not target:
        return jsonify({"error": "Enter your site's domain or a sitemap URL."}), 400
    try:
        discovered = _discover_urls(target)
    except Exception as exc:
        return _server_error(exc)
    if not discovered:
        return jsonify({"error": "Couldn't find a sitemap for that site. "
                                 "Try the full sitemap.xml URL."}), 404

    capped = discovered[:MAX_BATCH_URLS]
    results = []
    domains: set[str] = set()
    for url in capped:
        try:
            report = _analyze_url(url)
            results.append({"target": url, "score": report["score"], "grade": report["grade"], "ok": True})
            domains.add(harness.normalize_domain(url))
        except ingest.IngestError as exc:
            results.append({"target": url, "ok": False, "error": str(exc)})
        except Exception:  # one bad page shouldn't sink the whole batch
            app.logger.exception("Batch analyze failed for %s", url)
            results.append({"target": url, "ok": False, "error": "Could not analyze this page."})

    for domain in domains:  # one brand-index snapshot per site, not per page
        _snapshot_bvi(domain)

    analyzed = sum(1 for r in results if r["ok"])
    return jsonify({
        "target": target,
        "found": len(discovered),
        "analyzed": analyzed,
        "capped": len(discovered) > MAX_BATCH_URLS,
        "cap": MAX_BATCH_URLS,
        "results": results,
    })


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


@app.post("/api/brief")
def api_brief():
    """Turn a target question into a rule-based content brief (free, no AI credit)."""
    question = (request.json or {}).get("question", "").strip()
    if not question:
        return jsonify({"error": "Enter a target question."}), 400
    try:
        return jsonify(brief.build_brief(question))
    except ValueError as exc:
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


@app.post("/api/export")
def api_export():
    """Build a publish kit: the page patched with schema, plus a fix punch-list."""
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "Enter a page URL."}), 400
    try:
        html = ingest.fetch_url(url)
        payload = ingest.extract_html_payload(html, url)
        analysis = analyzer.build_analysis(payload)
        schema = _schema_for(payload)
        report = rubric.build_report(analysis, schema, None)
        injected = export.inject_schema(html, schema["ready_to_paste"])
        title = str(payload.get("title") or "page")
        return jsonify({
            "url": url,
            "title": title,
            "filename": f"{slugify(title)}.html",
            "score": report["score"],
            "grade": report["grade"],
            "detected_type": schema["detected_type"],
            "schema_script": schema["ready_to_paste"],
            "patched_html": injected["patched_html"],
            "location": injected["location"],
            "replaced_existing": injected["replaced_existing"],
            "fixes": report["priorities"],
        })
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


def _run_pulse() -> dict[str, Any]:
    """Re-check every tracked page, snapshot a fresh score, and raise an alert
    when a page's score regresses.

    Shared core behind the manual /api/pulse endpoint and the opt-in background
    scheduler, so both behave identically. Reads each target's previous score
    (before this run) so a drop can be measured, then delegates the "is this a
    regression?" decision to monitor.detect_regression.
    """
    targets = db.tracked_targets("analyze")
    results = []
    alerts_fired = 0
    for t in targets:
        url = t["target"]
        previous_score = t["latest_score"]  # latest score before this pulse
        try:
            payload = _fetch_payload(url)
            analysis = analyzer.build_analysis(payload)
            report = rubric.build_report(analysis, _schema_for(payload), None)
            score = report["score"]
            db.save_run("analyze", url, score, {"grade": report["grade"], "title": payload.get("title")})
            _snapshot_bvi(url)
            regression = monitor.detect_regression(previous_score, score)
            if regression:
                db.save_alert(url, "analyze", previous_score, score)
                alerts_fired += 1
                # Best-effort fan-out to Slack/email; notify() never raises.
                notify.notify(url, "analyze", previous_score, score, regression["delta"])
            results.append({
                "target": url,
                "score": score,
                "previous_score": previous_score,
                "regressed": bool(regression),
                "ok": True,
            })
        except Exception as exc:
            results.append({"target": url, "ok": False, "error": str(exc)})
    return {"pulsed": len(results), "alerts_fired": alerts_fired, "results": results}


@app.post("/api/pulse")
def api_pulse():
    """Re-check every tracked page now and snapshot a fresh score (a manual 'pulse')."""
    return jsonify(_run_pulse())


@app.get("/api/alerts")
def api_alerts():
    """Open regression alerts, the unread count, and scheduler status for the UI."""
    return jsonify({
        "alerts": db.recent_alerts(),
        "count": db.uncleared_alert_count(),
        "scheduler": dict(_scheduler_state),
        "notify": notify.configured_channels(),
    })


@app.post("/api/alerts/clear")
def api_alerts_clear():
    """Mark every open alert as read; returns how many were cleared."""
    return jsonify({"cleared": db.clear_alerts()})


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


def _scheduler_loop(interval_seconds: int) -> None:
    """Run a pulse every `interval_seconds` until the process exits.

    Runs in a daemon thread so it never blocks shutdown. Each pulse is wrapped
    so a transient failure (a page down, a network blip) logs and the loop keeps
    going rather than dying silently.
    """
    while True:
        time.sleep(interval_seconds)
        try:
            summary = _run_pulse()
            _scheduler_state["last_run"] = db.now_iso()
            app.logger.info(
                "Scheduled pulse: %d page(s), %d alert(s) fired",
                summary["pulsed"], summary["alerts_fired"],
            )
        except Exception:  # noqa: BLE001 - a bad pulse must not kill the loop
            app.logger.exception("Scheduled pulse failed")


def _start_scheduler() -> None:
    """Start the background pulse scheduler if CITEPILOT_PULSE_MINUTES opts in."""
    raw = os.environ.get("CITEPILOT_PULSE_MINUTES", "").strip()
    try:
        minutes = int(raw)
    except ValueError:
        minutes = 0
    if minutes <= 0:
        return
    _scheduler_state.update(enabled=True, minutes=minutes)
    thread = threading.Thread(
        target=_scheduler_loop, args=(minutes * 60,), name="citepilot-pulse", daemon=True,
    )
    thread.start()
    app.logger.info("Background pulse scheduler on: every %d minute(s)", minutes)


if __name__ == "__main__":
    load_dotenv()
    _start_scheduler()
    app.run(host="127.0.0.1", port=8760, debug=False)
