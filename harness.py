"""Phase 6 multi-AI test harness for AI-citation optimization.

Asks AI answer engines real questions and checks whether a target page's
domain shows up in the sources they cite. This is the "did it actually work"
verification step: run it before and after optimizing a page to see whether
the page starts getting cited.

Providers are pluggable. Claude works today via its web-search tool. Other
engines are registered as drop-in slots that activate once their API keys are
present; Google AI Overviews is registered but cannot be tested (no public API).

Usage:
  python harness.py --target example.com --query "how to descale a coffee maker"
  python harness.py --target example.com --from-rewrite ./output/rewritten/<slug>.json
  python harness.py --target example.com --query "..." --provider claude --dry-run
  python harness.py --list-providers

Needs ANTHROPIC_API_KEY (env or a gitignored .env) for the Claude provider.
Each query costs a small amount (web search + tokens), so use --dry-run to
preview what would run without spending anything.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from common import CLASSIFIER_MODEL, DEFAULT_MODEL, load_dotenv, slugify

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}

# The harness must behave like a real answer engine that consults the web before
# answering. Without this, Claude answers common questions from memory and never
# searches, so there is nothing to check for citation.
SEARCH_SYSTEM_PROMPT = (
    "You are a research assistant answering a user's question. Always use the "
    "web_search tool to find current, authoritative sources before you answer, "
    "even for familiar topics. Base your answer on the pages you find and cite "
    "them."
)


class HarnessError(Exception):
    pass


def normalize_domain(value: str) -> str:
    """Reduce a URL or host to a bare, comparable domain (no scheme/www/path)."""
    value = value.strip().lower()
    if "://" not in value:
        value = "http://" + value
    host = urlparse(value).netloc or ""
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def domains_match(target: str, candidate_url: str) -> bool:
    """True if candidate_url is on the target domain (or a subdomain of it)."""
    cand = normalize_domain(candidate_url)
    if not cand or not target:
        return False
    return cand == target or cand.endswith("." + target)


def load_json(path: str, label: str) -> dict[str, Any]:
    input_path = Path(path)
    if not input_path.is_file():
        raise HarnessError(f"{label} file not found: {path}")
    try:
        return json.loads(input_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HarnessError(f"{label} file is not valid JSON: {exc}") from exc


def queries_from_rewrite(path: str) -> list[str]:
    """Pull the FAQ questions from a Phase 4 rewrite as ready-made test queries."""
    data = load_json(path, "Rewrite")
    faq = data.get("faq")
    questions: list[str] = []
    if isinstance(faq, list):
        for item in faq:
            if isinstance(item, dict) and isinstance(item.get("question"), str):
                questions.append(item["question"].strip())
    return [q for q in questions if q]


# --- Providers ---------------------------------------------------------------

def run_claude(query: str, model: str) -> tuple[list[str], str]:
    """Ask Claude the query with web search on; return (result URLs, answer text)."""
    try:
        import anthropic
    except ImportError as exc:
        raise HarnessError("The 'anthropic' package is not installed. Run: pip install anthropic") from exc

    client = anthropic.Anthropic()
    try:
        message = client.messages.create(
            model=model,
            max_tokens=1500,
            system=SEARCH_SYSTEM_PROMPT,
            tools=[WEB_SEARCH_TOOL],
            messages=[{"role": "user", "content": query}],
        )
    except anthropic.AuthenticationError as exc:
        raise HarnessError("Authentication failed; check ANTHROPIC_API_KEY.") from exc
    except anthropic.APIError as exc:
        raise HarnessError(f"Claude API request failed: {exc}") from exc

    urls: list[str] = []
    answer_parts: list[str] = []
    for block in message.content:
        btype = getattr(block, "type", None)
        if btype == "web_search_tool_result":
            content = getattr(block, "content", None)
            if isinstance(content, list):  # a list is success; an object is an error
                for result in content:
                    url = getattr(result, "url", None)
                    if url:
                        urls.append(url)
        elif btype == "text":
            answer_parts.append(getattr(block, "text", ""))
    return urls, " ".join(answer_parts).strip()


# --- Niche Explorer: suggest customer questions to test -----------------------

NICHE_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["questions"],
    "additionalProperties": False,
}


def clean_queries(raw: list[Any], limit: int = 8) -> list[str]:
    """Normalize model-suggested questions: trim, drop blanks, de-dupe, cap."""
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split()).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def build_niche_prompt(brand: str, keywords: str, industry: str, count: int) -> str:
    """Assemble the instruction that turns a brand/niche into test questions."""
    lines = [
        f"Suggest {count} natural questions that real customers would type into an "
        "AI assistant or search engine when they are looking for products, services, "
        "or advice in this space.",
        "",
        "Rules:",
        "- Write them the way a customer actually searches, not as marketing copy.",
        "- Do NOT mention the brand name in the questions; they are for testing "
        "whether the brand gets cited.",
        "- Prefer questions where several sites would compete to be the answer.",
        "- Keep each question under 15 words.",
        "",
        "Context:",
    ]
    if brand:
        lines.append(f"- Brand or website: {brand}")
    if industry:
        lines.append(f"- Industry / vertical: {industry}")
    if keywords:
        lines.append(f"- Target keywords: {keywords}")
    return "\n".join(lines)


def suggest_queries(
    brand: str,
    keywords: str = "",
    industry: str = "",
    model: str = CLASSIFIER_MODEL,
    count: int = 8,
) -> list[str]:
    """Ask a small model for customer-style test questions for a brand/niche."""
    try:
        import anthropic
    except ImportError as exc:
        raise HarnessError("The 'anthropic' package is not installed. Run: pip install anthropic") from exc

    prompt = build_niche_prompt(brand, keywords, industry, count)
    client = anthropic.Anthropic()
    try:
        message = client.messages.create(
            model=model,
            max_tokens=800,
            output_config={"format": {"type": "json_schema", "schema": NICHE_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.AuthenticationError as exc:
        raise HarnessError("Authentication failed; check ANTHROPIC_API_KEY.") from exc
    except anthropic.APIError as exc:
        raise HarnessError(f"Claude API request failed: {exc}") from exc

    text = next((getattr(b, "text", "") for b in message.content if getattr(b, "type", None) == "text"), None)
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    return clean_queries(payload.get("questions", []), count)


SENTIMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "mentioned": {"type": "boolean"},
        "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative", "not_mentioned"]},
        "evidence": {"type": "string"},
    },
    "required": ["mentioned", "sentiment", "evidence"],
    "additionalProperties": False,
}


def classify_sentiment(brand: str, answer: str, model: str) -> dict[str, Any]:
    """Classify how an AI answer portrays a brand: mentioned? positive/neutral/negative?"""
    try:
        import anthropic
    except ImportError as exc:
        raise HarnessError("The 'anthropic' package is not installed. Run: pip install anthropic") from exc

    prompt = (
        f'An AI assistant gave this answer to a user question:\n\n"""{answer}"""\n\n'
        f'How does this answer portray the brand "{brand}"? If the brand is not '
        f'mentioned at all, set mentioned=false and sentiment="not_mentioned". '
        f'Otherwise judge the sentiment of how it is portrayed (positive, neutral, '
        f'or negative) and quote the relevant phrase as evidence.'
    )
    client = anthropic.Anthropic()
    try:
        message = client.messages.create(
            model=model,
            max_tokens=400,
            output_config={"format": {"type": "json_schema", "schema": SENTIMENT_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        raise HarnessError(f"Claude API request failed: {exc}") from exc

    text = next((getattr(b, "text", "") for b in message.content if getattr(b, "type", None) == "text"), None)
    if not text:
        return {"mentioned": False, "sentiment": "not_mentioned", "evidence": ""}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"mentioned": False, "sentiment": "not_mentioned", "evidence": ""}


def mention_rate(mix: dict[str, Any]) -> int:
    """Share of answers that mention the brand at all, as a 0-100 percentage.

    100 minus the not-mentioned share - a mention counts regardless of how the
    brand is portrayed, which is what separates it from citation rate (was the
    domain linked?) and positive-sentiment share (how it was portrayed). Pure:
    derived from the sentiment mix counts, so it needs no extra model calls.
    """
    total = sum(int(v.get("count", 0)) for v in mix.values())
    if not total:
        return 0
    mentioned = total - int(mix.get("not_mentioned", {}).get("count", 0))
    return round(100 * mentioned / total)


def run_sentiment(brand: str, queries: list[str], model: str) -> dict[str, Any]:
    """For each query: get the AI's answer, then classify brand sentiment. Aggregate a mix."""
    counts = {"positive": 0, "neutral": 0, "negative": 0, "not_mentioned": 0}
    results: list[dict[str, Any]] = []
    for query in queries:
        _urls, answer = run_claude(query, model)
        # Labelling the answer is a simple classification, so use the cheaper
        # model; the answer itself was generated by the representative `model`.
        verdict = classify_sentiment(brand, answer, CLASSIFIER_MODEL)
        sentiment = verdict.get("sentiment", "not_mentioned")
        if sentiment not in counts:
            sentiment = "not_mentioned"
        counts[sentiment] += 1
        results.append({
            "query": query,
            "sentiment": sentiment,
            "evidence": str(verdict.get("evidence", ""))[:200],
        })
    total = len(queries)
    mix = {k: {"count": v, "pct": round(100 * v / total) if total else 0} for k, v in counts.items()}
    return {"brand": brand, "queries": total, "mix": mix, "mention_rate": mention_rate(mix), "results": results}


# --- Persona fan-out ---------------------------------------------------------
#
# The same question, asked as different customers, gets different answers - and
# a brand that wins the citation for one audience can vanish for another. Fan a
# prompt out across a few preset personas and, for each, record whether the
# target is cited and how it's portrayed. Builds on the saved queries in Prompt
# Hub: pick a saved question, see how it lands per audience.

PERSONAS: list[dict[str, str]] = [
    {"key": "beginner", "label": "Budget beginner",
     "desc": "a first-time buyer on a tight budget"},
    {"key": "professional", "label": "Quality-focused pro",
     "desc": "an experienced professional who wants the best quality regardless of price"},
    {"key": "smb", "label": "Small-business owner",
     "desc": "a small-business owner comparing a few options for their team"},
    {"key": "enterprise", "label": "Enterprise buyer",
     "desc": "an enterprise buyer focused on scale, security, and support"},
    {"key": "skeptic", "label": "Skeptical researcher",
     "desc": "a skeptical researcher digging into reviews and downsides"},
]
_PERSONA_BY_KEY = {p["key"]: p for p in PERSONAS}


def persona_query(query: str, desc: str) -> str:
    """Frame a question as a given persona asking it. Pure."""
    return f"I am {desc}. {query.strip()}"


def select_personas(keys: list[str]) -> list[dict[str, str]]:
    """Resolve persona keys to persona dicts, in the canonical order.

    Unknown keys are ignored; an empty selection means "all personas". Pure.
    """
    if not keys:
        return list(PERSONAS)
    wanted = set(keys)
    return [p for p in PERSONAS if p["key"] in wanted]


def summarize_personas(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Roll persona rows into counts: how many cited and mentioned the brand."""
    return {
        "personas": len(rows),
        "cited": sum(1 for r in rows if r.get("cited")),
        "mentioned": sum(1 for r in rows if r.get("mentioned")),
    }


def run_persona_fanout(brand: str, query: str, personas: list[dict[str, str]],
                       model: str) -> dict[str, Any]:
    """Run one question as each persona; record citation and portrayal per row.

    For each persona the question is reframed, sent through the live answer
    engine, then the answer is checked for a citation of the brand's domain and
    classified for whether/how the brand is mentioned.
    """
    target = normalize_domain(brand)
    rows: list[dict[str, Any]] = []
    for persona in personas:
        urls, answer = run_claude(persona_query(query, persona["desc"]), model)
        cited = bool(target) and any(domains_match(target, u) for u in urls)
        verdict = classify_sentiment(brand, answer, CLASSIFIER_MODEL)
        rows.append({
            "key": persona["key"],
            "persona": persona["label"],
            "cited": cited,
            "mentioned": bool(verdict.get("mentioned")),
            "sentiment": verdict.get("sentiment", "not_mentioned"),
            "evidence": str(verdict.get("evidence", ""))[:200],
        })
    return {"brand": brand, "query": query, "rows": rows, "summary": summarize_personas(rows)}


# Each provider: label, the env var its key lives in, and a runner (None = not
# yet wired up). Slots are registered even when unimplemented so the pluggable
# design is explicit and the report can show what's covered vs. not.
PROVIDERS: dict[str, dict[str, Any]] = {
    "claude": {
        "label": "Claude (web search)",
        "env": "ANTHROPIC_API_KEY",
        "runner": run_claude,
        "note": "",
    },
    "openai": {
        "label": "ChatGPT / OpenAI",
        "env": "OPENAI_API_KEY",
        "runner": None,
        "note": "Runner not implemented yet; add an OpenAI module when a key is available.",
    },
    "perplexity": {
        "label": "Perplexity",
        "env": "PERPLEXITY_API_KEY",
        "runner": None,
        "note": "Runner not implemented yet; add a Perplexity module when a key is available.",
    },
    "google": {
        "label": "Google AI Overviews",
        "env": None,
        "runner": None,
        "note": "No public API - AI Overviews cannot be tested programmatically.",
    },
}


def provider_availability(name: str) -> tuple[bool, str]:
    """Return (available, reason). reason explains why an unavailable provider is off."""
    spec = PROVIDERS[name]
    if spec["runner"] is None:
        return False, spec["note"] or "Not implemented."
    env = spec["env"]
    if env and not os.environ.get(env):
        return False, f"Set {env} to enable this provider."
    return True, ""


def run_provider(name: str, queries: list[str], target: str, model: str) -> dict[str, Any]:
    spec = PROVIDERS[name]
    available, reason = provider_availability(name)
    result: dict[str, Any] = {"provider": name, "label": spec["label"], "available": available}
    if not available:
        result["reason"] = reason
        result["results"] = []
        return result

    runner: Callable[[str, str], tuple[list[str], str]] = spec["runner"]
    per_query: list[dict[str, Any]] = []
    cited_count = 0
    for query in queries:
        urls, answer = runner(query, model)
        cited = any(domains_match(target, u) for u in urls)
        if cited:
            cited_count += 1
        cited_domains = sorted({normalize_domain(u) for u in urls if normalize_domain(u)})
        per_query.append({
            "query": query,
            "cited": cited,
            "sources_found": len(urls),
            "cited_domains": cited_domains,
            "answer_excerpt": answer[:240],
        })
    result["results"] = per_query
    result["citation_rate"] = f"{cited_count}/{len(queries)}" if queries else "0/0"
    return result


def write_output(report: dict[str, Any], slug: str) -> Path:
    output_dir = Path.cwd() / "output" / "harness"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{slug}.json"
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 6 multi-AI test harness for AI-citation optimization.")
    parser.add_argument("--target", help="Domain or URL to check for in AI answers, e.g. example.com.")
    parser.add_argument("--query", action="append", default=[], help="A test query (repeatable).")
    parser.add_argument("--from-rewrite", help="Pull test queries from a Phase 4 rewrite's FAQ.")
    parser.add_argument("--provider", action="append", default=[], help="Provider to run (repeatable). Default: claude.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Model for the Claude provider (default: {DEFAULT_MODEL}).")
    parser.add_argument("--list-providers", action="store_true", help="List providers and their availability, then exit.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run without calling any API.")
    return parser.parse_args()


def main() -> int:
    try:
        load_dotenv()
        args = parse_args()

        if args.list_providers:
            listing = []
            for name in PROVIDERS:
                available, reason = provider_availability(name)
                listing.append({
                    "provider": name,
                    "label": PROVIDERS[name]["label"],
                    "available": available,
                    "reason": reason,
                })
            json.dump({"providers": listing}, sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0

        if not args.target:
            raise HarnessError("--target is required (the domain to check for), unless using --list-providers.")
        target = normalize_domain(args.target)

        queries = list(args.query)
        if args.from_rewrite:
            queries.extend(queries_from_rewrite(args.from_rewrite))
        # De-duplicate while preserving order.
        seen: set[str] = set()
        queries = [q for q in queries if not (q in seen or seen.add(q))]
        if not queries:
            raise HarnessError("No test queries provided. Use --query or --from-rewrite.")

        providers = args.provider or ["claude"]
        unknown = [p for p in providers if p not in PROVIDERS]
        if unknown:
            raise HarnessError(f"Unknown provider(s): {', '.join(unknown)}. Known: {', '.join(PROVIDERS)}.")

        if args.dry_run:
            preview = {
                "dry_run": True,
                "target": target,
                "queries": queries,
                "providers": [
                    {"provider": p, "available": provider_availability(p)[0], "reason": provider_availability(p)[1]}
                    for p in providers
                ],
            }
            json.dump(preview, sys.stdout, indent=2, ensure_ascii=False)
            sys.stdout.write("\n")
            return 0

        report = {
            "target": target,
            "model": args.model,
            "queries_tested": len(queries),
            "providers": [run_provider(p, queries, target, args.model) for p in providers],
        }

        write_output(report, slugify(target))
        json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    except HarnessError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Error: interrupted.", file=sys.stderr)
        return 130
    except Exception:
        print("Error: unexpected failure while running the harness.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
