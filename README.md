# villa-checker

Morning warning when there's an event at Villa Park, so the car gets moved
before the roads shut. Also a small GitHub Pages site showing the next 5
events at the ground.

## How it works

```
GitHub Actions cron (first firing after 06:45 UK, retries hourly)
        │
        ▼
scripts/check_events.py
  ├─ football-data.org ── Aston Villa HOME fixtures (team 58 ⇒ Villa Park)
  ├─ Ticketmaster Discovery API ── other events at the Villa Park venue
  ├─ merge + sort ──▶ docs/events.json  (committed back to the repo)
  └─ ntfy ──▶ push notification:
        · event today  → "AW SHIT! ..." + start time (high priority)
        · nothing on   → "No villa games today 🎉Thank Fuck!!"
```

API keys are only ever used inside the Action. The web page reads the
pre-generated `docs/events.json`, so nothing secret reaches the browser.

### Morning schedule that survives GitHub cron flakiness

GitHub's `schedule` trigger is best-effort: firings are routinely hours late
and sometimes dropped altogether (worst at minute :00). So instead of one
carefully-timed firing, the workflow schedules **four attempts** (05:50,
06:50, 07:50 and 08:50 UTC) and a gate step runs the check on the first
firing that lands after 06:45 UK, skipping the rest. "Already ran today" is
detected from the `updated_utc` date in `docs/events.json`, which every run
commits. This also handles BST/GMT automatically — no cron entry is tied to
a UTC offset.

Manual and external `workflow_dispatch` triggers go through the same gate,
so an external scheduler can coexist with the GitHub crons without double
notifications. Tick the **force** box on a manual run to bypass the gate
(e.g. to re-send today's notification).

### Punctual notifications via an external scheduler

Even with the fallback attempts, GitHub's cron can run hours late. For a
punctual morning notification, have an external scheduler (e.g. the free
cron-job.org) fire the workflow at 06:50 Europe/London daily:

- URL: `https://api.github.com/repos/<user>/<repo>/actions/workflows/villa-park-check.yml/dispatches`
- Method: POST, body `{"ref":"main"}`
- Headers: `Authorization: Bearer <fine-grained PAT>` (repo-scoped,
  Actions read+write only), `Accept: application/vnd.github+json`,
  `Content-Type: application/json`
- A successful trigger returns HTTP 204.

The gate then makes the late-arriving GitHub cron firings no-ops. One
caveat learned the hard way: whenever the workflow file's cron entries are
edited, commit the file once more (and from a real user account) or GitHub
may silently fail to re-register the schedule.

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
