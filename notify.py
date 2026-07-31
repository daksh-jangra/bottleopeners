"""Outbound notifications for regression alerts (Slack + email).

Opt-in and best-effort. A pulse always records an alert in the DB; this module
fans that alert out to whatever channels are configured via environment
variables. With nothing configured, notify() is a no-op. Every delivery is
wrapped so a flaky webhook or mail server logs and is skipped rather than
breaking the pulse that raised the alert.

Channels & their env vars
--------------------------
Slack   CITEPILOT_SLACK_WEBHOOK   an incoming-webhook URL
Email   CITEPILOT_SMTP_HOST       mail server host           (required)
        CITEPILOT_ALERT_TO        recipient address          (required)
        CITEPILOT_SMTP_PORT       default 587
        CITEPILOT_SMTP_USER       login user (optional)
        CITEPILOT_SMTP_PASS       login password (optional)
        CITEPILOT_ALERT_FROM      From address (defaults to the user/host)
        CITEPILOT_SMTP_TLS        "0" to disable STARTTLS (default on)

The message formatting and channel detection are pure functions with no I/O, so
they unit-test without a network or mail server.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

Env = Mapping[str, str]


def format_alert(
    target: str, metric: str, previous_score: int, current_score: int, delta: int
) -> dict[str, str]:
    """Build the subject/body text for a regression alert. Pure - no I/O."""
    subject = f"CitePilot alert: {target} dropped {delta} pts"
    body = (
        f"{target} regressed on {metric}: {previous_score} -> {current_score} "
        f"(down {delta} points).\n\n"
        "Open the Monitoring tab in CitePilot to review and clear this alert."
    )
    return {"subject": subject, "body": body}


def _email_config(env: Env) -> Optional[dict[str, Any]]:
    """Parse the email channel config from env, or None if not configured.

    Email needs at least a host and a recipient; everything else has a sane
    default so an unauthenticated local relay works with just those two.
    """
    host = env.get("CITEPILOT_SMTP_HOST", "").strip()
    to = env.get("CITEPILOT_ALERT_TO", "").strip()
    if not host or not to:
        return None
    user = env.get("CITEPILOT_SMTP_USER", "").strip()
    try:
        port = int(env.get("CITEPILOT_SMTP_PORT", "587") or 587)
    except ValueError:
        port = 587
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": env.get("CITEPILOT_SMTP_PASS", ""),
        "from": env.get("CITEPILOT_ALERT_FROM", "").strip() or user or "citepilot@localhost",
        "to": to,
        "use_tls": env.get("CITEPILOT_SMTP_TLS", "1").strip().lower() not in ("0", "false", "no"),
    }


def configured_channels(env: Env = os.environ) -> list[str]:
    """Which notification channels are configured, in delivery order. Pure."""
    channels: list[str] = []
    if env.get("CITEPILOT_SLACK_WEBHOOK", "").strip():
        channels.append("slack")
    if _email_config(env):
        channels.append("email")
    return channels


def _send_slack(webhook: str, text: str) -> None:
    import requests  # local import so a Slack-less setup needn't have it loaded

    resp = requests.post(webhook, json={"text": text}, timeout=10)
    resp.raise_for_status()


def _send_email(cfg: dict[str, Any], subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg.set_content(body)
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as server:
        if cfg["use_tls"]:
            server.starttls()
        if cfg["user"]:
            server.login(cfg["user"], cfg["password"])
        server.send_message(msg)


def notify(
    target: str,
    metric: str,
    previous_score: int,
    current_score: int,
    delta: int,
    env: Env = os.environ,
) -> list[str]:
    """Fan a regression alert out to every configured channel, best-effort.

    Returns the channels a message was actually delivered to (empty when none
    are configured, or when every configured channel failed). A per-channel
    failure is logged and skipped so one bad channel never blocks the others -
    or the pulse.
    """
    channels = configured_channels(env)
    if not channels:
        return []

    msg = format_alert(target, metric, previous_score, current_score, delta)
    sent: list[str] = []
    for channel in channels:
        try:
            if channel == "slack":
                _send_slack(
                    env["CITEPILOT_SLACK_WEBHOOK"].strip(),
                    f":warning: {msg['subject']}\n{msg['body']}",
                )
            elif channel == "email":
                _send_email(_email_config(env), msg["subject"], msg["body"])  # type: ignore[arg-type]
            sent.append(channel)
        except Exception:  # noqa: BLE001 - a failed ping must never break a pulse
            logger.exception("Failed to send %s alert for %s", channel, target)
    return sent
