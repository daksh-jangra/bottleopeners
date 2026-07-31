"""Tests for outbound alert notifications in notify.py.

The message formatting and channel detection are pure, so they're checked
directly. The fan-out is exercised with the actual senders stubbed so no
network or SMTP is touched, covering: no-op when nothing's configured, delivery
to each configured channel, and the best-effort promise that one failing
channel neither raises nor blocks the others.
"""

import notify


# --- format_alert -----------------------------------------------------------

def test_format_alert_mentions_target_delta_and_scores():
    msg = notify.format_alert("https://example.com/x", "analyze", 88, 71, 17)
    assert "17 pts" in msg["subject"]
    assert "https://example.com/x" in msg["subject"]
    assert "88 -> 71" in msg["body"]
    assert "17 points" in msg["body"]


# --- configured_channels / _email_config ------------------------------------

def test_no_channels_when_env_empty():
    assert notify.configured_channels({}) == []


def test_slack_channel_detected():
    assert notify.configured_channels({"CITEPILOT_SLACK_WEBHOOK": "https://hooks/x"}) == ["slack"]


def test_email_needs_both_host_and_recipient():
    assert notify.configured_channels({"CITEPILOT_SMTP_HOST": "mail.x"}) == []  # no recipient
    assert notify.configured_channels({"CITEPILOT_ALERT_TO": "me@x"}) == []      # no host
    assert notify.configured_channels({
        "CITEPILOT_SMTP_HOST": "mail.x", "CITEPILOT_ALERT_TO": "me@x",
    }) == ["email"]


def test_both_channels_in_order():
    env = {
        "CITEPILOT_SLACK_WEBHOOK": "https://hooks/x",
        "CITEPILOT_SMTP_HOST": "mail.x", "CITEPILOT_ALERT_TO": "me@x",
    }
    assert notify.configured_channels(env) == ["slack", "email"]


def test_email_config_defaults():
    cfg = notify._email_config({"CITEPILOT_SMTP_HOST": "mail.x", "CITEPILOT_ALERT_TO": "me@x"})
    assert cfg["port"] == 587
    assert cfg["use_tls"] is True
    assert cfg["from"] == "citepilot@localhost"  # falls back when no user/from


def test_email_config_tls_can_be_disabled():
    cfg = notify._email_config({
        "CITEPILOT_SMTP_HOST": "mail.x", "CITEPILOT_ALERT_TO": "me@x", "CITEPILOT_SMTP_TLS": "0",
    })
    assert cfg["use_tls"] is False


# --- notify: best-effort fan-out --------------------------------------------

def test_notify_is_noop_without_channels():
    assert notify.notify("t", "analyze", 80, 60, 20, env={}) == []


def test_notify_sends_to_slack(monkeypatch):
    calls = []
    monkeypatch.setattr(notify, "_send_slack", lambda webhook, text: calls.append((webhook, text)))
    sent = notify.notify("t", "analyze", 80, 60, 20, env={"CITEPILOT_SLACK_WEBHOOK": "https://hook"})
    assert sent == ["slack"]
    assert calls and calls[0][0] == "https://hook" and "80 -> 60" in calls[0][1]


def test_notify_continues_when_one_channel_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("slack down")
    email_calls = []
    monkeypatch.setattr(notify, "_send_slack", boom)
    monkeypatch.setattr(notify, "_send_email", lambda cfg, subject, body: email_calls.append(subject))
    env = {
        "CITEPILOT_SLACK_WEBHOOK": "https://hook",
        "CITEPILOT_SMTP_HOST": "mail.x", "CITEPILOT_ALERT_TO": "me@x",
    }
    # slack raises internally but notify swallows it and still delivers email
    sent = notify.notify("t", "analyze", 80, 60, 20, env=env)
    assert sent == ["email"]
    assert len(email_calls) == 1
