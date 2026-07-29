"""SQLite persistence for citation-readiness runs.

This is the foundation for "monitoring over time": every analyze/sentiment run
is stored with a timestamp, so the dashboard can show history and trends and a
"pulse" can re-check tracked targets on demand. Kept dependency-free (stdlib
sqlite3) and local - a single citepilot.db file.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

DB_PATH = Path(os.environ.get("CITEPILOT_DB", "citepilot.db"))


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,          -- 'analyze' | 'sentiment' | 'competitors'
                target TEXT NOT NULL,        -- a URL or a brand name
                score INTEGER,               -- headline number for the trend line
                detail TEXT,                 -- JSON blob of the full result
                created_at TEXT NOT NULL     -- ISO 8601 UTC
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_runs ON runs (kind, target, created_at)")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_run(kind: str, target: str, score: Optional[int], detail: Any) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO runs (kind, target, score, detail, created_at) VALUES (?, ?, ?, ?, ?)",
            (kind, target, score, json.dumps(detail), now_iso()),
        )


def history(kind: str, target: str, limit: int = 200) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT score, detail, created_at FROM runs WHERE kind = ? AND target = ? "
            "ORDER BY created_at ASC LIMIT ?",
            (kind, target, limit),
        ).fetchall()
    return [
        {
            "score": r["score"],
            "created_at": r["created_at"],
            "detail": json.loads(r["detail"]) if r["detail"] else None,
        }
        for r in rows
    ]


def runs_by_kind(kind: str, limit: int = 1000) -> list[dict[str, Any]]:
    """Every run of one kind, newest first - used to group runs by domain."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT target, score, created_at FROM runs WHERE kind = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (kind, limit),
        ).fetchall()
    return [
        {"target": r["target"], "score": r["score"], "created_at": r["created_at"]}
        for r in rows
    ]


def tracked_targets(kind: Optional[str] = None) -> list[dict[str, Any]]:
    """One row per (kind, target): run count, latest score/time, and the first score."""
    query = (
        "SELECT kind, target, COUNT(*) AS runs, MAX(created_at) AS latest, "
        "MIN(created_at) AS first FROM runs "
    )
    params: tuple = ()
    if kind:
        query += "WHERE kind = ? "
        params = (kind,)
    query += "GROUP BY kind, target ORDER BY latest DESC"
    with _conn() as conn:
        groups = conn.execute(query, params).fetchall()
        out = []
        for g in groups:
            latest_score = conn.execute(
                "SELECT score FROM runs WHERE kind = ? AND target = ? ORDER BY created_at DESC LIMIT 1",
                (g["kind"], g["target"]),
            ).fetchone()
            first_score = conn.execute(
                "SELECT score FROM runs WHERE kind = ? AND target = ? ORDER BY created_at ASC LIMIT 1",
                (g["kind"], g["target"]),
            ).fetchone()
            out.append({
                "kind": g["kind"],
                "target": g["target"],
                "runs": g["runs"],
                "latest": g["latest"],
                "first": g["first"],
                "latest_score": latest_score["score"] if latest_score else None,
                "first_score": first_score["score"] if first_score else None,
            })
    return out
