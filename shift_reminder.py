"""
Shift Reminder Bot
-------------------
Reads a weekly shift schedule from a Google Sheet and DMs each person on
Slack ~15 minutes before their shift starts. If they don't react to confirm
they saw it, sends one follow-up DM. If they still don't react, DMs the
manager. Designed to be run on a schedule (every 5 minutes) by GitHub
Actions (or any other cron runner) — each run is stateless except for what
it reads from/writes to the sheet.

Sheet columns (row 1 = header, one row per shift):
  A: Name
  B: Email               <- must match the person's Slack account email
  C: Date                <- YYYY-MM-DD
  D: Shift Start Time    <- 24-hour HH:MM
  E: Timezone            <- IANA tz name, e.g. America/Los_Angeles.
                             Optional — leave blank to use DEFAULT_TIMEZONE.
  F: Status              <- script-managed, leave blank.
                             "" -> REMINDED -> REMINDED_2 -> CONFIRMED or ESCALATED
  G: Last Message TS     <- script-managed, leave blank. Slack ts of latest DM.
  H: Last Sent At        <- script-managed, leave blank. ISO timestamp of latest DM.

Escalation flow per shift:
  T-15min  Reminder 1 DM sent.
  +5min    If no emoji reaction on it yet -> Reminder 2 DM sent ("just checking").
  +5min    If still no reaction -> DM sent to the manager flagging it.
  (Any point) If they react to either DM -> marked CONFIRMED, flow stops.

Required environment variables (set as GitHub Actions secrets):
  SLACK_BOT_TOKEN              Bot token for the Slack app (xoxb-...)
  GOOGLE_SERVICE_ACCOUNT_JSON  Full JSON key content for a Google service account
  SHEET_ID                     The spreadsheet ID (from the sheet's URL)
  MANAGER_EMAIL                Slack email of the manager to escalate to
  SHEET_RANGE (optional)       Defaults to 'Sheet1!A2:H'
  DEFAULT_TIMEZONE (optional)  Used when a row's Timezone cell is blank.
                                Defaults to 'America/Los_Angeles'.
"""

import os
import sys
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
SHEET_ID = os.environ["SHEET_ID"]
MANAGER_EMAIL = os.environ["MANAGER_EMAIL"]
SHEET_RANGE = os.environ.get("SHEET_RANGE", "Sheet1!A2:H")
DEFAULT_TIMEZONE = os.environ.get("DEFAULT_TIMEZONE") or "America/Los_Angeles"

# How far ahead (in minutes) we look for upcoming shifts to send the first
# reminder. Since this runs every 5 minutes, a 10-20 minute window
# guarantees each shift is caught at least once, roughly ~15 min ahead.
# All of these can be overridden by env var for fast local testing, e.g.
# FOLLOWUP_AFTER_MINUTES=1, without touching code or waiting on production
# timing. Leave them unset in GitHub Actions to use the real defaults.
WINDOW_MIN_MINUTES = int(os.environ.get("WINDOW_MIN_MINUTES", 10))
WINDOW_MAX_MINUTES = int(os.environ.get("WINDOW_MAX_MINUTES", 20))

# How long to wait for a reaction before escalating to the next step.
FOLLOWUP_AFTER_MINUTES = int(os.environ.get("FOLLOWUP_AFTER_MINUTES", 5))

# Stop chasing a shift once it started this many minutes ago (avoids
# indefinitely re-processing stale rows if something never gets resolved).
GIVE_UP_AFTER_SHIFT_START_MINUTES = int(os.environ.get("GIVE_UP_AFTER_SHIFT_START_MINUTES", 30))

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

_user_id_cache = {}


def get_sheet_rows():
    creds_info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    service = build("sheets", "v4", credentials=creds)
    sheet = service.spreadsheets()
    result = sheet.values().get(spreadsheetId=SHEET_ID, range=SHEET_RANGE).execute()
    return service, result.get("values", [])


def update_row(service, row_index, status, message_ts, sent_at_iso):
    """row_index is the 0-based index into the data rows (row 2 in the sheet = index 0)."""
    sheet_row_number = row_index + 2  # account for header row
    range_ = f"Sheet1!F{sheet_row_number}:H{sheet_row_number}"
    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range=range_,
        valueInputOption="RAW",
        body={"values": [[status, message_ts or "", sent_at_iso or ""]]},
    ).execute()


def slack_lookup_user_id(email):
    email = email.strip().lower()
    if email in _user_id_cache:
        return _user_id_cache[email]
    resp = requests.get(
        "https://slack.com/api/users.lookupByEmail",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        params={"email": email},
        timeout=10,
    ).json()
    if not resp.get("ok"):
        print(f"  [warn] could not find Slack user for {email}: {resp.get('error')}")
        return None
    user_id = resp["user"]["id"]
    _user_id_cache[email] = user_id
    return user_id


def slack_open_dm(user_id):
    resp = requests.post(
        "https://slack.com/api/conversations.open",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"users": user_id},
        timeout=10,
    ).json()
    if not resp.get("ok"):
        print(f"  [warn] could not open DM with {user_id}: {resp.get('error')}")
        return None
    return resp["channel"]["id"]


def slack_send_message(channel_id, text):
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"channel": channel_id, "text": text},
        timeout=10,
    ).json()
    if not resp.get("ok"):
        print(f"  [warn] could not send message to {channel_id}: {resp.get('error')}")
        return None
    return resp["message"]["ts"]


def slack_has_reaction(channel_id, ts):
    resp = requests.get(
        "https://slack.com/api/reactions.get",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        params={"channel": channel_id, "timestamp": ts},
        timeout=10,
    ).json()
    if not resp.get("ok"):
        # e.g. message_not_found is possible right after sending; treat as no reaction yet
        return False
    return bool(resp.get("message", {}).get("reactions"))


def send_dm_to_person(email, text):
    user_id = slack_lookup_user_id(email)
    if not user_id:
        return None, None
    channel_id = slack_open_dm(user_id)
    if not channel_id:
        return None, None
    ts = slack_send_message(channel_id, text)
    return channel_id, ts


def escalate_to_manager(name, email, time_str, tz_name):
    manager_id = slack_lookup_user_id(MANAGER_EMAIL)
    if not manager_id:
        print("  [warn] could not find manager's Slack account, cannot escalate")
        return
    channel_id = slack_open_dm(manager_id)
    if not channel_id:
        return
    text = (
        f":warning: {name} ({email}) hasn't confirmed their {time_str} ({tz_name}) "
        f"shift reminder after two DMs. You may want to follow up directly."
    )
    slack_send_message(channel_id, text)


def main():
    service, rows = get_sheet_rows()
    if not rows:
        print("No schedule rows found.")
        return

    now_utc = datetime.now(ZoneInfo("UTC"))

    for i, row in enumerate(rows):
        row = row + [""] * (8 - len(row))  # pad missing trailing cells
        name, email, date_str, time_str, tz_name, status, last_ts, last_sent_at = row[:8]
        name, email = name.strip(), email.strip()
        tz_name = tz_name.strip() or DEFAULT_TIMEZONE
        status = status.strip().upper()

        if not (name and email and date_str and time_str):
            continue
        if status in ("CONFIRMED", "ESCALATED"):
            continue

        try:
            tz = ZoneInfo(tz_name.strip())
            shift_start = datetime.strptime(f"{date_str.strip()} {time_str.strip()}", "%Y-%m-%d %H:%M")
            shift_start = shift_start.replace(tzinfo=tz)
        except Exception as e:
            print(f"  [warn] row {i}: could not parse date/time/timezone ({e})")
            continue

        minutes_until = (shift_start - now_utc).total_seconds() / 60

        # --- Step 1: no reminder sent yet ---
        if status == "":
            if WINDOW_MIN_MINUTES <= minutes_until <= WINDOW_MAX_MINUTES:
                print(f"Row {i}: sending first reminder to {name} <{email}>")
                text = (
                    f"Hey {name.split()[0]}! Your shift starts at {time_str} "
                    f"({tz_name}) today — about {round(minutes_until)} minutes from now. "
                    f"React to this message to confirm you saw it :+1:"
                )
                _, ts = send_dm_to_person(email, text)
                if ts:
                    update_row(service, i, "REMINDED", ts, now_utc.isoformat())
            continue

        # Beyond this point we're following up on a reminder already sent.
        # Give up chasing very stale shifts so a stuck row doesn't loop forever.
        if minutes_until < -GIVE_UP_AFTER_SHIFT_START_MINUTES:
            continue

        try:
            last_sent_dt = datetime.fromisoformat(last_sent_at)
        except Exception:
            continue
        minutes_since_last = (now_utc - last_sent_dt).total_seconds() / 60

        user_id = slack_lookup_user_id(email)
        channel_id = slack_open_dm(user_id) if user_id else None
        reacted = slack_has_reaction(channel_id, last_ts) if channel_id and last_ts else False

        if reacted:
            print(f"Row {i}: {name} confirmed (reacted)")
            update_row(service, i, "CONFIRMED", last_ts, last_sent_at)
            continue

        if minutes_since_last < FOLLOWUP_AFTER_MINUTES:
            continue  # still waiting on the reaction window

        # --- Step 2: reminder 1 sent, no reaction after the wait -> send reminder 2 ---
        if status == "REMINDED":
            print(f"Row {i}: no reaction from {name}, sending follow-up DM")
            text = (
                f"Just checking you saw this, {name.split()[0]} — your shift starts at "
                f"{time_str} ({tz_name}). Please react to confirm :+1:"
            )
            _, ts = send_dm_to_person(email, text)
            if ts:
                update_row(service, i, "REMINDED_2", ts, now_utc.isoformat())
            continue

        # --- Step 3: reminder 2 sent, still no reaction -> escalate to manager ---
        if status == "REMINDED_2":
            print(f"Row {i}: still no reaction from {name}, escalating to manager")
            escalate_to_manager(name, email, time_str, tz_name)
            update_row(service, i, "ESCALATED", last_ts, last_sent_at)
            continue

    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Fatal error: {exc}", file=sys.stderr)
        sys.exit(1)
