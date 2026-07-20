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

import shift_reminder as sr

now = datetime.now(ZoneInfo("UTC"))


def row(name, email, minutes_from_now, status="", last_ts="", minutes_since_sent=None, tz="UTC", rtype=""):
    # tz="" simulates a blank Timezone cell, which should fall back to DEFAULT_TIMEZONE.
    effective_tz = tz or sr.DEFAULT_TIMEZONE
    t = (now + timedelta(minutes=minutes_from_now)).astimezone(ZoneInfo(effective_tz))
    last_sent_at = (
        (now - timedelta(minutes=minutes_since_sent)).isoformat()
        if minutes_since_sent is not None
        else ""
    )
    return [name, email, rtype, t.strftime("%Y-%m-%d"), t.strftime("%H:%M"), tz, status, last_ts, last_sent_at]


def sheets_style_row(name, email, minutes_from_now, tz="UTC", rtype=""):
    # Mimics how Google Sheets actually returns Date/Start Time cells once it
    # auto-formats them: "7/20/2026" and "12:35 PM", not ISO/24-hour. This is
    # what real sheets produce once a manager types a date/time in normally.
    effective_tz = tz or sr.DEFAULT_TIMEZONE
    t = (now + timedelta(minutes=minutes_from_now)).astimezone(ZoneInfo(effective_tz))
    date_str = f"{t.month}/{t.day}/{t.year}"
    time_str = t.strftime("%I:%M %p").lstrip("0")
    return [name, email, rtype, date_str, time_str, tz, "", "", ""]


TEST_ROWS = [
    row("Alice New", "alice@example.com", 15),  # 0: fresh shift, in window -> REMINDED
    row("Bob Waiting", "bob@example.com", 5, status="REMINDED", last_ts="ts_b", minutes_since_sent=2),  # 1: too soon -> no action
    row("Carla Silent", "carla@example.com", 5, status="REMINDED", last_ts="ts_c", minutes_since_sent=6),  # 2: overdue -> REMINDED_2
    row("Dave Ghost", "dave@example.com", 5, status="REMINDED_2", last_ts="ts_d", minutes_since_sent=6),  # 3: overdue -> NO_RESPONSE, no DM sent
    row("Erin Confirmed", "erin@example.com", 5, status="REMINDED", last_ts="ts_e", minutes_since_sent=0),  # 4: reacted -> CONFIRMED
    row("Frank Stale", "frank@example.com", -40, status="REMINDED", last_ts="ts_f", minutes_since_sent=45),  # 5: too old -> give up
    row("Grace Done", "grace@example.com", 5, status="CONFIRMED", last_ts="ts_g", minutes_since_sent=6),  # 6: already terminal
    row("Hank Default", "hank@example.com", 15, tz=""),  # 7: blank timezone -> uses DEFAULT_TIMEZONE -> REMINDED
    row("Ivy Queue", "ivy@example.com", 15, rtype="Queue Watch"),  # 8: queue watch -> REMINDED, message says "queue watch"
    row("Reused Row", "reused@example.com", 15, status="CONFIRMED", last_ts="ts_old", minutes_since_sent=60 * 24 * 6),  # 9: row reused for a new week - old CONFIRMED from 6 days ago, Date/Time overwritten for a fresh shift -> must auto-reset and send a new reminder, not stay stuck as CONFIRMED forever
    sheets_style_row("Sheets Format", "sheetsfmt@example.com", 15),  # 10: Google Sheets' natural "7/20/2026" / "12:35 PM" format -> must still parse and REMIND
    row("Just Missed Old Window", "latecatch@example.com", 3),  # 11: only 3 min out - the OLD 10-20 window would have missed this entirely; new logic must still REMIND
    row("Very Late Run", "verylate@example.com", -25),  # 12: shift started 25 min ago, never reminded (simulates a badly delayed/skipped scheduled run) -> must still REMIND late rather than never
    row("Too Late Now", "toolate@example.com", -35),  # 13: 35 min past start, beyond give-up -> must NOT send (correctly gives up eventually)
    row("Late Reactor Gaming It", "lategamer@example.com", -15, status="REMINDED_2", last_ts="ts_gamer", minutes_since_sent=6),  # 14: shift started 15 min ago (past the 10-min late-reaction cutoff), reacts AFTER the fact -> must resolve NO_RESPONSE, reaction must NOT count
    row("Reacts In Time", "intime@example.com", -8, status="REMINDED_2", last_ts="ts_intime", minutes_since_sent=6),  # 15: only 8 min past start (within the 10-min cutoff), reacts -> should still count as CONFIRMED
]

updates = []
sent = []
REACTED_TS = {"ts_e", "ts_gamer", "ts_intime"}


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
    (3, "NO_RESPONSE (no DM)", updates_by_row.get(3) == "NO_RESPONSE"),
    (4, "CONFIRMED", updates_by_row.get(4) == "CONFIRMED"),
    (5, "no action (gave up)", 5 not in updates_by_row),
    (6, "no action (terminal)", 6 not in updates_by_row),
    (7, "REMINDED (default tz)", updates_by_row.get(7) == "REMINDED"),
    (8, "REMINDED (queue watch)", updates_by_row.get(8) == "REMINDED"),
    (9, "REMINDED (reused row auto-reset)", updates_by_row.get(9) == "REMINDED"),
    (10, "REMINDED (Sheets-formatted date/time)", updates_by_row.get(10) == "REMINDED"),
    (11, "REMINDED (would've missed old narrow window)", updates_by_row.get(11) == "REMINDED"),
    (12, "REMINDED (late catch-up, run was delayed)", updates_by_row.get(12) == "REMINDED"),
    (13, "no action (past give-up point)", 13 not in updates_by_row),
    (14, "NO_RESPONSE (late reaction doesn't count)", updates_by_row.get(14) == "NO_RESPONSE"),
    (15, "CONFIRMED (reacted within cutoff)", updates_by_row.get(15) == "CONFIRMED"),
]

print("\n--- Results ---")
all_pass = True
for idx, expected, ok in checks:
    status = "PASS" if ok else "FAIL"
    if not ok:
        all_pass = False
    print(f"[{status}] row {idx}: expected {expected}")

# The NO_RESPONSE row must NOT have triggered any Slack message send.
no_response_silent = not any("Dave" in text for _, text in sent)
print(f"[{'PASS' if no_response_silent else 'FAIL'}] no message sent for NO_RESPONSE row: {no_response_silent}")
if not no_response_silent:
    all_pass = False

# First reminder should contain a real Slack @mention, not just a name.
alice_msg = next((text for _, text in sent if "Alice" in text), "")
has_mention = alice_msg.startswith("<@U_alice@example.com>")
print(f"[{'PASS' if has_mention else 'FAIL'}] reminder DM includes @mention: {has_mention}")
if not has_mention:
    all_pass = False

# Queue watch row's message should say "queue watch", not "shift".
ivy_msg = next((text for _, text in sent if "Ivy" in text), "")
says_queue = "queue watch" in ivy_msg
print(f"[{'PASS' if says_queue else 'FAIL'}] queue watch row message says 'queue watch': {says_queue}")
if not says_queue:
    all_pass = False

print(f"\nTotal messages sent: {len(sent)}")
print("ALL TESTS PASSED" if all_pass else "SOME TESTS FAILED")
raise SystemExit(0 if all_pass else 1)
