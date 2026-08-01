# Facebook Scheduler

Playwright-based automation that reads text posts from a Google Sheet queue
and schedules them as Facebook background posts on the **Momma Circle** page.

---

## Project Structure

```
facebook_scheduler/
├── __init__.py
├── config.py                 All tunable constants (timeouts, paths, intervals)
├── logger.py                 Centralised logging + screenshot helper
├── human_behavior.py         Randomised click / type / scroll / pause wrappers
├── background_selector.py    ISOLATED background-preset selector (stub)
├── sheet.py                  Google Sheets queue reader/writer
├── facebook_scheduler.py     Core Playwright engine + selector constants
├── main.py                   CLI entry point
├── record_mode.py            Playwright Codegen launcher for recording selectors
├── screenshots/              Auto-saved screenshots on errors
├── logs/                     Daily log files
└── README.md
```

---

## Quick Start

### 1. Prerequisites

```bash
pip install playwright gspread
playwright install chromium
```

### 2. Google Sheets service account

1. Create a project in [Google Cloud Console](https://console.cloud.google.com).
2. Enable the **Google Sheets API** and **Google Drive API**.
3. Create a **Service Account** → download the JSON key.
4. Place the JSON at: `credentials/gsheet_service_account.json`
5. Share your spreadsheet with the service account email (Editor access).

### 3. Configure `.env` (optional overrides)

```env
GSHEET_ID=your_spreadsheet_id_here
GOOGLE_SERVICE_ACCOUNT_JSON=credentials/gsheet_service_account.json
CDP_ENDPOINT=http://localhost:9222
DRY_RUN=           # set to "1" to enable dry-run globally
```

### 4. Launch Chrome with remote debugging

```bat
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --no-first-run ^
  --no-default-browser-check
```

Log in to Facebook and navigate to the Momma Circle page in that browser.

---

## Running the Scheduler

### Dry-run (simulation — safe to run anytime)

```bash
python facebook_scheduler/main.py --dry-run
```

Prints a table of all pending posts and their scheduled times.
No browser actions. No sheet writes. Safe to run anytime.

### Fill empty schedule times only

```bash
python facebook_scheduler/main.py --fill-times-only
```

Reads the sheet and writes a scheduled datetime (3-hour increments from now)
into every row where Column B is empty. Does not post anything.

### Full live run

```bash
python facebook_scheduler/main.py
```

Connects to the running Chrome via CDP, reads pending rows from the sheet,
and schedules each post via Playwright automation.

### With a specific background preset

```bash
python facebook_scheduler/main.py --background gradient_warm
```

---

## Google Sheet Column Format

| Column | Header         | Example Value          |
|--------|----------------|------------------------|
| A      | Post Text      | "What ruins a burger?" |
| B      | Scheduled Time | 2026-07-25 15:00       |
| C      | Status         | PENDING / DONE / FAILED|

- Rows with empty Column A are skipped.
- Rows where Column C is `DONE` are skipped.
- When Column B is empty, a datetime is auto-generated (3-hour intervals).
- Column B is NEVER overwritten if it already contains a value.

---

## Record Mode: Capturing Selectors

Facebook changes its UI frequently. All selectors are stored in one place and
can be replaced in minutes using Playwright Codegen.

### Step 1 — Launch the recorder

```bash
python facebook_scheduler/main.py --record
# or directly:
python facebook_scheduler/record_mode.py
```

A Chrome window and the **Playwright Inspector** open simultaneously.

### Step 2 — Perform the workflow

In the browser, perform the full scheduling workflow:

1. Click the **"What's on your mind"** button.
2. Type some text in the composer.
3. Click the **background colour** button.
4. Click a colour tile.
5. Click **"Schedule"** or **"More options"**.
6. Fill in the **date** and **time** fields.
7. Click the final **"Schedule"** confirmation button.
8. Observe the **success banner**.

### Step 3 — Copy selectors

The Playwright Inspector generates Python code for every action.
Map each generated locator to the corresponding constant:

| Workflow step         | File                      | Constant / method              |
|-----------------------|---------------------------|--------------------------------|
| Open composer button  | `facebook_scheduler.py`   | `_SEL_COMPOSER_TRIGGER`        |
| Composer textarea     | `facebook_scheduler.py`   | `_SEL_COMPOSER_TEXTAREA`       |
| Background picker btn | `background_selector.py`  | `_open_background_picker()`    |
| Colour tile           | `background_selector.py`  | `_BACKGROUNDS` dict            |
| Schedule button       | `facebook_scheduler.py`   | `_SEL_SCHEDULE_BUTTON`         |
| Date input            | `facebook_scheduler.py`   | `_SEL_SCHEDULE_DATE_INPUT`     |
| Time input            | `facebook_scheduler.py`   | `_SEL_SCHEDULE_TIME_INPUT`     |
| Confirm button        | `facebook_scheduler.py`   | `_SEL_SCHEDULE_CONFIRM`        |
| Success banner        | `facebook_scheduler.py`   | `_SEL_SUCCESS_BANNER`          |

### Step 4 — Paste and test

Replace the placeholder values in the SELECTORS section of
`facebook_scheduler.py` with the recorded locators, then run:

```bash
python facebook_scheduler/main.py --dry-run
```

followed by a single live test post:

```bash
python facebook_scheduler/main.py --url https://www.facebook.com/MommaCircle
```

---

## Error Handling

When any step fails:

1. A full-page screenshot is saved to `facebook_scheduler/screenshots/`.
2. The error is logged to `facebook_scheduler/logs/fb_scheduler_YYYY-MM-DD.log`.
3. The row is marked `FAILED: <reason>` in the sheet.
4. **Execution stops immediately** — no further rows are processed.

This is intentional: never continue blindly after an unexpected state.

---

## Replacing Selectors After a Facebook UI Update

1. Run `python facebook_scheduler/main.py --record`.
2. Re-record the affected steps.
3. Paste the new locators into **only** `facebook_scheduler.py` (SELECTORS block)
   and/or `background_selector.py` (`_BACKGROUNDS` dict).
4. No other file needs to change.

---

## Human Behaviour Timings

All click/type delays are randomised to avoid bot detection:

| Action              | Range                              |
|---------------------|------------------------------------|
| Before each click   | 1.0 – 3.0 seconds                 |
| Per-character typing| 40 – 120 ms                       |
| Between posts       | 8 – 20 seconds                    |
| Scroll amount       | 80 – 220 pixels                   |

Adjust in `config.py` (`CLICK_PAUSE_MIN/MAX`, `TYPE_DELAY_*_MS`, etc.).

---

## All CLI Options

```
python facebook_scheduler/main.py [OPTIONS]

  --dry-run          Simulate all actions (safe mode, no browser/sheet writes)
  --fill-times-only  Only fill empty Column B datetimes, then exit
  --record           Launch Playwright Codegen for recording
  --sheet-id ID      Override Google Sheet ID
  --worksheet NAME   Override worksheet tab name
  --background NAME  Background preset (see background_selector.py)
  --cdp URL          Override CDP endpoint (default: http://localhost:9222)
  --url URL          Navigate to this URL before posting
```
