"""Phase 5 citation-readiness rubric for AI-citation optimization.

Turns a Phase 2 analysis into a client-facing report: an overall score and
grade, a fix list ranked by how many points each fix is worth, the strengths
to keep, the paste-ready schema (if provided), and before/after proof (if a
Phase 4 rewrite is provided). This phase is rule-based and free — it calls no
model and reuses the Phase 2 scorer.

Usage:
  python rubric.py --input ./output/analyzed/<slug>.json
  python rubric.py --input ./output/analyzed/<slug>.json --schema ./output/schema/<slug>.json
  python rubric.py --input ./output/analyzed/<slug>.json --rewrite ./output/rewritten/<slug>.json --html

Writes ./output/report/<slug>.json (and <slug>.html with --html) and prints the
JSON report to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from analyzer import build_analysis
from common import slugify

FACTOR_LABELS = {
    "header_quality": "Header quality",
    "answer_first_structure": "Answer-first structure",
    "list_table_presence": "Lists & tables",
    "factual_specificity": "Factual specificity",
    "byline_authority": "Author & publisher signals",
    "recency_signals": "Freshness / date signals",
}

RECOMMENDATIONS = {
    "header_quality": "Make headings specific and question-shaped; avoid generic labels like 'Steps', 'Tips', or 'Overview'.",
    "answer_first_structure": "Open the page and each section with a direct, quotable answer or definition in the first sentence.",
    "list_table_presence": "Present steps as numbered lists and comparisons as tables so AI can extract discrete points.",
    "factual_specificity": "Add concrete numbers, dates, and named entities; replace vague phrasing with verifiable facts.",
    "byline_authority": "Add a visible author byline, the publisher name, and a meta description that summarizes the page.",
    "recency_signals": "Add the page's real published or 'last updated' date — use the actual date; never invent one.",
}


class RubricError(Exception):
    pass


def load_json(path: str, label: str) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.exists():
        raise RubricError(f"{label} file not found: {path}")
    if not input_path.is_file():
        raise RubricError(f"{label} path is not a file: {path}")
    try:
        raw = input_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raw = input_path.read_text(encoding="utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RubricError(f"{label} file is not valid JSON: {exc}") from exc


def validate_analysis(analysis: Any) -> dict[str, Any]:
    if not isinstance(analysis, dict):
        raise RubricError("Analysis JSON must be an object.")
    for field in ("source", "total_score", "breakdown"):
        if field not in analysis:
            raise RubricError(f"Analysis JSON is missing required field '{field}'.")
    if not isinstance(analysis["breakdown"], dict) or not analysis["breakdown"]:
        raise RubricError("Analysis 'breakdown' must be a non-empty object.")
    for name, section in analysis["breakdown"].items():
        if not isinstance(section, dict) or "score" not in section or "max" not in section:
            raise RubricError(f"Breakdown section '{name}' must have 'score' and 'max'.")
    return analysis


def grade_for(total: int) -> str:
    if total >= 90:
        return "A"
    if total >= 80:
        return "B"
    if total >= 70:
        return "C"
    if total >= 60:
        return "D"
    return "F"


def status_for(total: int) -> str:
    if total >= 85:
        return "Strong — citation-ready"
    if total >= 70:
        return "Good — a few targeted fixes"
    if total >= 55:
        return "Needs work — several gaps to close"
    return "Poor — major gaps to close"


def build_report(
    analysis: dict[str, Any],
    schema: Optional[dict[str, Any]],
    rewrite_analysis: Optional[dict[str, Any]],
) -> dict[str, Any]:
    total = int(analysis["total_score"])
    breakdown = analysis["breakdown"]

    priorities: list[dict[str, Any]] = []
    strengths: list[dict[str, Any]] = []
    for name, section in breakdown.items():
        score = int(section["score"])
        max_score = int(section["max"])
        label = FACTOR_LABELS.get(name, name.replace("_", " ").title())
        if score >= max_score:
            strengths.append({"factor": name, "label": label, "score": score, "max": max_score})
            continue
        specifics = [i for i in section.get("issues", []) if isinstance(i, str)]
        priorities.append({
            "factor": name,
            "label": label,
            "current_score": score,
            "max": max_score,
            "potential_gain": max_score - score,
            "action": RECOMMENDATIONS.get(name, "Improve this factor."),
            "specifics": specifics,
        })

    # Biggest point opportunities first, then alphabetically for stable ordering.
    priorities.sort(key=lambda p: (-p["potential_gain"], p["label"]))

    report: dict[str, Any] = {
        "source": analysis["source"],
        "score": total,
        "grade": grade_for(total),
        "status": status_for(total),
        "priorities": priorities,
        "strengths": strengths,
        "schema": None,
        "improvement": None,
    }

    if schema is not None:
        report["schema"] = {
            "detected_type": schema.get("detected_type"),
            "ready_to_paste": schema.get("ready_to_paste"),
        }

    if rewrite_analysis is not None:
        after = int(rewrite_analysis["total_score"])
        report["improvement"] = {"before": total, "after": after, "gain": after - total}

    return report


def render_html(report: dict[str, Any]) -> str:
    from html import escape

    def bar(score: int, max_score: int) -> str:
        pct = int(100 * score / max_score) if max_score else 0
        color = "#16a34a" if pct >= 75 else ("#d97706" if pct >= 40 else "#dc2626")
        return f'<div class="bar"><i style="width:{pct}%;background:{color}"></i></div>'

    prio_html = ""
    for i, p in enumerate(report["priorities"], 1):
        specifics = "".join(f"<li>{escape(s)}</li>" for s in p["specifics"])
        prio_html += f"""<div class="item">
          <div class="item-head"><span><b>{i}.</b> {escape(p['label'])}</span>
          <span class="gain">+{p['potential_gain']} pts</span></div>
          <div class="cur">Currently {p['current_score']}/{p['max']}</div>
          {bar(p['current_score'], p['max'])}
          <p class="action">{escape(p['action'])}</p>
          {'<ul class="spec">'+specifics+'</ul>' if specifics else ''}
        </div>"""

    strengths_html = "".join(
        f'<li>{escape(s["label"])} <b>{s["score"]}/{s["max"]}</b></li>'
        for s in report["strengths"]
    ) or "<li>None yet — every factor has room to improve.</li>"

    imp = report.get("improvement")
    imp_html = ""
    if imp:
        imp_html = f"""<div class="card imp">
          <h2>Proof it works</h2>
          <p>After our rewrite, this page's score moves from
          <b>{imp['before']}</b> to <b class="up">{imp['after']}</b>
          <span class="gain">+{imp['gain']} pts</span>.</p></div>"""

    schema = report.get("schema")
    schema_html = ""
    if schema and schema.get("ready_to_paste"):
        schema_html = f"""<div class="card">
          <h2>Paste this into your page</h2>
          <p class="cur">Structured-data markup ({escape(str(schema.get('detected_type')))}) helps Google understand the page.</p>
          <pre>{escape(schema['ready_to_paste'])}</pre></div>"""

    grade = report["grade"]
    gcolor = {"A": "#16a34a", "B": "#65a30d", "C": "#d97706", "D": "#ea580c", "F": "#dc2626"}.get(grade, "#666")

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Citation-Readiness Report</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;font:15px/1.6 -apple-system,Segoe UI,Roboto,sans-serif;background:#f6f7f9;color:#1a1d23}}
.wrap{{max-width:820px;margin:0 auto;padding:36px 22px}}
h1{{font-size:24px;margin:0 0 2px}} .src{{color:#6b7280;margin:0 0 24px;word-break:break-all}}
.hero{{display:flex;align-items:center;gap:22px;background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:24px;margin-bottom:20px}}
.gradebox{{width:96px;height:96px;border-radius:14px;display:flex;align-items:center;justify-content:center;font-size:52px;font-weight:800;color:#fff;flex:none}}
.score{{font-size:40px;font-weight:800;line-height:1}} .score small{{font-size:17px;color:#6b7280;font-weight:500}}
.status{{color:#374151;font-weight:600;margin-top:4px}}
.card{{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:22px;margin-bottom:18px}}
h2{{font-size:17px;margin:0 0 14px}}
.item{{padding:14px 0;border-top:1px solid #eef0f3}} .item:first-of-type{{border-top:none;padding-top:0}}
.item-head{{display:flex;justify-content:space-between;font-weight:600;font-size:15.5px}}
.gain{{color:#16a34a;font-weight:700}} .cur{{color:#6b7280;font-size:13px;margin:2px 0}}
.bar{{height:7px;background:#eef0f3;border-radius:6px;overflow:hidden;margin:6px 0}} .bar i{{display:block;height:100%}}
.action{{margin:8px 0 0}} ul.spec{{margin:6px 0 0;padding-left:18px;color:#6b7280;font-size:13.5px}}
.imp .up{{color:#16a34a}} ul{{margin:0;padding-left:20px}} li{{margin:3px 0}}
pre{{background:#0b0d12;color:#c9d1d9;border-radius:8px;padding:14px;overflow:auto;font-size:12.5px}}
.foot{{color:#9aa1ac;font-size:12.5px;text-align:center;margin-top:8px}}
</style></head><body><div class="wrap">
<h1>AI Citation-Readiness Report</h1>
<p class="src">{escape(str(report['source']))}</p>

<div class="hero">
  <div class="gradebox" style="background:{gcolor}">{grade}</div>
  <div><div class="score">{report['score']}<small>/100</small></div>
  <div class="status">{escape(report['status'])}</div></div>
</div>

{imp_html}

<div class="card"><h2>Top fixes, ranked by impact</h2>
{prio_html or '<p>No fixes needed — every factor is already at full marks.</p>'}</div>

<div class="card"><h2>Strengths to keep</h2><ul>{strengths_html}</ul></div>

{schema_html}

<p class="foot">Generated by the AI Citation-Readiness pipeline · scores estimate how quotable this page is to AI answer engines and search.</p>
</div></body></html>"""


def write_output(report: dict[str, Any], html: Optional[str], slug: str) -> tuple[Path, Optional[Path]]:
    output_dir = Path.cwd() / "output" / "report"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{slug}.json"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    html_path = None
    if html is not None:
        html_path = output_dir / f"{slug}.html"
        html_path.write_text(html, encoding="utf-8")
    return json_path, html_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 5 citation-readiness rubric for AI-citation optimization.")
    parser.add_argument("--input", required=True, help="Path to a Phase 2 analysis JSON file.")
    parser.add_argument("--schema", help="Optional Phase 3 schema JSON to include the paste-ready markup.")
    parser.add_argument("--rewrite", help="Optional Phase 4 rewrite JSON to show before/after proof.")
    parser.add_argument("--html", action="store_true", help="Also write a shareable HTML report.")
    return parser.parse_args()


def main() -> int:
    try:
        args = parse_args()
        analysis = validate_analysis(load_json(args.input, "Analysis"))

        schema = load_json(args.schema, "Schema") if args.schema else None

        rewrite_analysis = None
        if args.rewrite:
            rewrite = load_json(args.rewrite, "Rewrite")
            payload = rewrite.get("rewritten_payload")
            if not isinstance(payload, dict):
                raise RubricError("Rewrite JSON has no 'rewritten_payload' object to re-score.")
            rewrite_analysis = build_analysis(payload)

        report = build_report(analysis, schema, rewrite_analysis)
        html = render_html(report) if args.html else None

        slug = slugify(Path(str(analysis["source"])).stem or str(analysis["source"]))
        write_output(report, html, slug)

        json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    except RubricError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Error: interrupted.", file=sys.stderr)
        return 130
    except Exception:
        print("Error: unexpected failure while building the report.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
