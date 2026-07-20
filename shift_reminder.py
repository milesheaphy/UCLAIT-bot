"""
Offline logic test for shift_reminder.py — no Slack/Google credentials needed.

Mocks all network calls (Slack lookups/sends/reactions, sheet read/write) and
feeds synthetic rows covering every state transition, then checks the bot
did the right thing in each case. Run any time you change the script:

    pip install -r requirements.txt
    python test_shift_reminder.py

Expect: "ALL TESTS PASSED" and exit code 0.
"""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Dummy values so the module's required-env-var checks pass on import.
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("GOOGLE_SERVICE_ACCOUNT_JSON", "{}")
os.environ.setdefault("SHEET_ID", "test-sheet-id")
os.environ.setdefault("MANAGER_EMAIL", "manager@example.com")

import shift_reminder as sr

now = datetime.now(ZoneInfo("UTC"))


def row(name, email, minutes_from_now, status="", last_ts="", minutes_since_sent=None, tz="UTC"):
    # tz="" simulates a blank Timezone cell, which should fall back to DEFAULT_TIMEZONE.
    effective_tz = tz or sr.DEFAULT_TIMEZONE
    t = (now + timedelta(minutes=minutes_from_now)).astimezone(ZoneInfo(effective_tz))
    last_sent_at = (
        (now - timedelta(minutes=minutes_since_sent)).isoformat()
        if minutes_since_sent is not None
        else ""
    )
    return [name, email, t.strftime("%Y-%m-%d"), t.strftime("%H:%M"), tz, status, last_ts, last_sent_at]


TEST_ROWS = [
    row("Alice New", "alice@example.com", 15),  # 0: fresh, in window -> REMINDED
    row("Bob Waiting", "bob@example.com", 5, status="REMINDED", last_ts="ts_b", minutes_since_sent=2),  # 1: too soon -> no action
    row("Carla Silent", "carla@example.com", 5, status="REMINDED", last_ts="ts_c", minutes_since_sent=6),  # 2: overdue -> REMINDED_2
    row("Dave Ghost", "dave@example.com", 5, status="REMINDED_2", last_ts="ts_d", minutes_since_sent=6),  # 3: overdue -> ESCALATED
    row("Erin Confirmed", "erin@example.com", 5, status="REMINDED", last_ts="ts_e", minutes_since_sent=0),  # 4: reacted -> CONFIRMED
    row("Frank Stale", "frank@example.com", -40, status="REMINDED", last_ts="ts_f", minutes_since_sent=45),  # 5: too old -> give up
    row("Grace Done", "grace@example.com", 5, status="CONFIRMED", last_ts="ts_g", minutes_since_sent=6),  # 6: already terminal
    row("Hank Default", "hank@example.com", 15, tz=""),  # 7: blank timezone -> uses DEFAULT_TIMEZONE -> REMINDED
]

updates = []
sent = []
REACTED_TS = {"ts_e"}


def fake_get_sheet_rows():
    return "FAKE_SERVICE", TEST_ROWS


def fake_update_row(service, row_index, status, ts, sent_at):
    updates.append((row_index, status))


def fake_lookup(email):
    return f"U_{email}"


def fake_open_dm(user_id):
    return f"D_{user_id}"


def fake_send_message(channel_id, text):
    sent.append((channel_id, text))
    return f"ts_new_{len(sent)}"


def fake_has_reaction(channel_id, ts):
    return ts in REACTED_TS


sr.get_sheet_rows = fake_get_sheet_rows
sr.update_row = fake_update_row
sr.slack_lookup_user_id = fake_lookup
sr.slack_open_dm = fake_open_dm
sr.slack_send_message = fake_send_message
sr.slack_has_reaction = fake_has_reaction

sr.main()

updates_by_row = dict(updates)
checks = [
    (0, "REMINDED", updates_by_row.get(0) == "REMINDED"),
    (1, "no action (too soon)", 1 not in updates_by_row),
    (2, "REMINDED_2", updates_by_row.get(2) == "REMINDED_2"),
    (3, "ESCALATED", updates_by_row.get(3) == "ESCALATED"),
    (4, "CONFIRMED", updates_by_row.get(4) == "CONFIRMED"),
    (5, "no action (gave up)", 5 not in updates_by_row),
    (6, "no action (terminal)", 6 not in updates_by_row),
    (7, "REMINDED (default tz)", updates_by_row.get(7) == "REMINDED"),
]

print("\n--- Results ---")
all_pass = True
for idx, expected, ok in checks:
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"[{status}] row {idx}: expected {expected}")

manager_dm_sent = any(ch == "D_U_manager@example.com" for ch, _ in sent)
print(f"[{'PASS' if manager_dm_sent else 'FAIL'}] manager escalation DM sent: {manager_dm_sent}")
if not manager_dm_sent:
    all_pass = False

print(f"\nTotal messages sent: {len(sent)}")
print("ALL TESTS PASSED" if all_pass else "SOME TESTS FAILED")
raise SystemExit(0 if all_pass else 1)
