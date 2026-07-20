# Shift Reminder Bot — Setup Guide

A standalone Slack bot with its own token that DMs people ~15 minutes before
their shift or queue watch starts, based on a weekly Google Sheet schedule.
Runs on GitHub Actions, not tied to any personal Slack/Google login — only
the credentials you generate for it.

**Reminder flow:** the first DM @mentions the person so it reliably
notifies them, and asks them to react with any emoji to confirm they saw
it. If they don't react within 5 minutes, they get one follow-up DM. If
they still don't react after another 5 minutes, nobody gets messaged — it's
just recorded in the sheet (Status = `NO_RESPONSE`) so a manager can review
it later without being paged in real time.

## What you're setting up

1. A Slack App with its own bot token (the bot's identity — not your account)
2. A Google service account (a robot identity that can read/write the sheet)
3. A GitHub repo that runs `shift_reminder.py` every 5 minutes for free

## 1. Create the Slack bot

1. Go to https://api.slack.com/apps → **Create New App** → **From scratch**.
2. Name it (e.g. "Shift Reminder Bot") and pick your workspace.
3. Left sidebar → **OAuth & Permissions**. Under **Scopes → Bot Token Scopes**, add:
   - `chat:write`
   - `im:write`
   - `users:read.email`
   - `users:read`
   - `reactions:read`
4. Scroll up → **Install to Workspace** → Allow.
5. Copy the **Bot User OAuth Token** (starts with `xoxb-`). Keep it secret —
   this is the bot's password.

## 2. Create the Google service account

1. Go to https://console.cloud.google.com/ → create a project (or use an existing one).
2. **APIs & Services → Library** → enable the **Google Sheets API**.
3. **APIs & Services → Credentials → Create Credentials → Service Account**.
   Give it any name, no roles needed.
4. Open the new service account → **Keys** tab → **Add Key → Create new key → JSON**.
   A `.json` file downloads — keep it safe.
5. Open that JSON file and copy the `client_email` value
   (looks like `something@project-id.iam.gserviceaccount.com`).
6. Open the schedule Google Sheet → **Share** → paste that email in → give it
   **Editor** access (the bot needs to write status back to the sheet).
7. Copy the **Sheet ID** from the sheet's URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`
8. Make sure the sheet has these column headers, in this order: **Name,
   Email, Type, Date, Start Time, Timezone, Status, Last Message TS, Last
   Sent At**. The last three are script-managed — leave the cells under
   them blank, the bot fills them in. See `shift_schedule_import.csv` for
   a ready-made example you can import.

## 3. Set up the GitHub repo

1. Create a new **private** GitHub repo.
2. Add these files to the repo root:
   - `shift_reminder.py`
   - `requirements.txt`
   - `.github/workflows/shift-reminder.yml`  (the provided `shift-reminder.yml` goes in this exact folder path)
3. Repo → **Settings → Secrets and variables → Actions → New repository secret**.
   Add these secrets:
   - `SLACK_BOT_TOKEN` → the `xoxb-...` token from step 1
   - `GOOGLE_SERVICE_ACCOUNT_JSON` → paste the *entire contents* of the JSON key file from step 2
   - `SHEET_ID` → the sheet ID from step 2
   - `DEFAULT_TIMEZONE` (optional) → e.g. `America/Los_Angeles`. Only needed
     if you want a timezone other than Pacific to be the fallback — see
     the Timezones note below.
4. Push the files. GitHub Actions will start running automatically every 5
   minutes (check the **Actions** tab to see runs / logs).
5. To test immediately without waiting: **Actions tab → Shift Reminder Bot →
   Run workflow** (manual trigger button).

## Weekly use

One row per person per shift or queue watch (Name, Email, Type, Date,
Start Time, Timezone). A person can have both a shift and a queue watch on
the same day; give each one its own row (e.g. shift 1:00-2:00, then queue
watch 2:00-3:30 — two separate rows with different Start Times). Leave
"Type" blank to default to "Shift", or type "Queue Watch" for the other
kind. Leave the "Status", "Last Message TS", and "Last Sent At" columns
blank on any new row; the bot manages them automatically. Only people
you've given Editor access to the sheet can change the schedule; everyone
else can be given Viewer or no access at all via the Sheet's Share
settings.

**Recommended weekly routine — fill a template and replace:**

1. Open `shift_schedule_import.csv` (blank headers) in Excel, Google
   Sheets, or Numbers.
2. Fill in that week's rows: Name, Email, Type, Date, Start Time, Timezone.
   Leave the last three columns empty. (`shift_schedule_example_filled.csv`
   shows what a filled-in week looks like if you want a reference.)
3. Save it.
4. In the live Google Sheet — the one already shared with the bot's
   service account — go to **File → Import → Upload**, select your filled
   file, choose **"Replace current sheet"**, and import.

That's it — no need to hunt down and delete last week's rows by hand, the
replace wipes them for you, and every new row starts blank so the bot
treats it as fresh automatically.

**Alternative — edit the live sheet in place:** if you'd rather just
overwrite the Date/Start Time cells directly on the existing sheet instead
of re-importing each week, that also works — the bot detects when a row's
date has been pushed to a new occurrence and automatically resets it, even
if the Status column still shows last week's `CONFIRMED`/`NO_RESPONSE`. No
manual clearing needed either way.

Each shift/queue watch goes through this automatically:
1. **T-15 min:** first reminder DM sent (@mentions them so it reliably
   notifies), asking them to react to confirm.
2. **+5 min, still no reaction:** a follow-up "just checking" DM is sent.
3. **+5 min, still no reaction:** no more messages are sent. The row is
   marked `NO_RESPONSE` in the sheet so it's there for the record if a
   manager wants to review it — nobody gets paged.
4. **Reacts at any point:** marked `CONFIRMED`, flow stops — no more DMs.

## Timezones

The GitHub Actions server itself doesn't have a "home" timezone that
matters here — it just wakes up every 5 minutes in UTC, 24/7, regardless of
where it physically runs. What actually determines correctness is the
"Timezone" column in the sheet: the script converts each shift's date/time
using that exact IANA timezone name (e.g. `America/Los_Angeles` for
Pacific) and compares it against the current UTC time. As long as that
column has the right value, the reminder fires at the right real-world
moment no matter where the code executes.

Since your team is West Coast, the Timezone column is optional — leave it
blank and the bot uses `America/Los_Angeles` by default (or whatever you
set `DEFAULT_TIMEZONE` to). Only fill it in for a row if that particular
shift is in a different timezone.

## Notes

- The bot only DMs people whose Slack account email matches the "Email"
  column exactly.
- Any emoji reaction on either reminder counts as confirmation — it doesn't
  have to be a specific one.
- Reminder DMs include a real `@name` mention so they reliably trigger a
  Slack notification, even for people with stricter notification settings.
- If GitHub Actions runs are delayed under load, the lookahead windows in
  the script still catch each step on the next run.
- Nothing here depends on your personal Slack login, your computer, or your
  Cowork account — it's fully self-contained once the secrets are set.
