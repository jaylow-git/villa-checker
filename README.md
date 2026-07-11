# villa-checker

Morning warning when there's an event at Villa Park, so the car gets moved
before the roads shut. Also a small GitHub Pages site showing the next 5
events at the ground.

## How it works

```
GitHub Actions cron (07:00 UK, DST-proof)
        │
        ▼
scripts/check_events.py
  ├─ football-data.org ── Aston Villa HOME fixtures (team 58 ⇒ Villa Park)
  ├─ Ticketmaster Discovery API ── other events at the Villa Park venue
  ├─ merge + sort ──▶ docs/events.json  (committed back to the repo)
  └─ ntfy ──▶ push notification:
        · event today  → name + start time (high priority)
        · nothing on   → "No events at Villa park today"
```

API keys are only ever used inside the Action. The web page reads the
pre-generated `docs/events.json`, so nothing secret reaches the browser.

### 07:00 UK without DST drift

GitHub cron runs in UTC and UK clocks flip between GMT and BST. The workflow
fires at **both** 06:00 and 07:00 UTC, and a gate step keeps exactly one run:
the 06:00 UTC firing when the UK is on BST (+0100), the 07:00 UTC firing when
on GMT (+0000). The gate checks which cron entry fired rather than the wall
clock, so late cron starts (common on GitHub) don't cause a missed day.

### Ticketmaster venue ID

The Discovery API venue ID for Villa Park isn't published, so the script
resolves it at runtime by searching venues for "Villa Park" in Birmingham, GB.
The resolved ID is written into `events.json` (`ticketmaster_venue_id`). To
skip the lookup, set it as an environment variable / repo variable:
`TICKETMASTER_VENUE_ID=<id from events.json>`.

## Setup

1. Repo secrets (already set): `FOOTBALL_DATA_API_KEY`, `TICKETMASTER_API_KEY`,
   `NTFY_TOPIC` (topic name, or a full URL for self-hosted ntfy).
2. Subscribe to your ntfy topic in the ntfy app (or `ntfy subscribe <topic>`).
3. Enable GitHub Pages: repo **Settings → Pages → Deploy from a branch**,
   pick the default branch and the `/docs` folder. The page appears at
   `https://<user>.github.io/<repo>/`.

## Testing that a notification fires

1. **Plumbing only** — confirm your phone is subscribed to the topic:
   ```bash
   curl -d "test from curl" https://ntfy.sh/<your-topic>
   ```
2. **Full end-to-end** — GitHub → **Actions → Villa Park daily check →
   Run workflow** (leave "Send the ntfy notification" ticked). Within a
   minute you should get either the event alert or
   "No events at Villa park today", and `docs/events.json` gets a fresh
   commit. The run log shows how many events each API returned.
3. **Refresh the page without notifying** — run the workflow with the
   notification box unticked; only `events.json` is updated.
4. **Locally**:
   ```bash
   FOOTBALL_DATA_API_KEY=... TICKETMASTER_API_KEY=... NTFY_TOPIC=... \
     python3 scripts/check_events.py
   ```
   Add `--no-notify` to test the data fetch without pinging your phone.

## Failure behaviour

- One API down → the other still works; the notification/page carry a note.
- Both APIs down → a high-priority "check FAILED" notification is sent
  (better than silence when you're deciding whether to move the car) and the
  workflow run is marked failed.
