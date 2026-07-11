#!/usr/bin/env python3
"""Check upcoming events at Villa Park and send a morning ntfy alert.

Data sources:
  - football-data.org  : Aston Villa HOME fixtures (team id 58 => always Villa Park)
  - Ticketmaster Discovery API : concerts/other events at the Villa Park venue

Outputs:
  - docs/events.json : next 5 events, read by the GitHub Pages site
  - ntfy notification: today's events, or "No events at Villa park today"

Environment variables:
  FOOTBALL_DATA_API_KEY  football-data.org API token
  TICKETMASTER_API_KEY   Ticketmaster Discovery API key
  NTFY_TOPIC             ntfy topic name (or full URL like https://ntfy.sh/mytopic)
  TICKETMASTER_VENUE_ID  optional: pin the venue id and skip the runtime lookup

Uses only the Python standard library (3.9+).
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")
ASTON_VILLA_TEAM_ID = 58
FOOTBALL_API = "https://api.football-data.org/v4"
TICKETMASTER_API = "https://app.ticketmaster.com/discovery/v2"
LOOKAHEAD_DAYS = 180
MAX_EVENTS_ON_PAGE = 5


def http_get_json(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------- football


def fetch_football_events(api_key, today_london):
    """Aston Villa home fixtures in the next LOOKAHEAD_DAYS days."""
    params = urllib.parse.urlencode(
        {
            "dateFrom": today_london.isoformat(),
            "dateTo": (today_london + timedelta(days=LOOKAHEAD_DAYS)).isoformat(),
        }
    )
    url = f"{FOOTBALL_API}/teams/{ASTON_VILLA_TEAM_ID}/matches?{params}"
    data = http_get_json(url, headers={"X-Auth-Token": api_key})

    events = []
    for match in data.get("matches", []):
        if match.get("homeTeam", {}).get("id") != ASTON_VILLA_TEAM_ID:
            continue
        # SCHEDULED = date known / provisional time, TIMED = confirmed kick-off.
        # utcDate is updated by the API when a match is rescheduled, so the
        # times below always reflect the latest kick-off. POSTPONED matches
        # have no reliable date and are excluded.
        if match.get("status") not in ("SCHEDULED", "TIMED"):
            continue
        start_utc = datetime.strptime(match["utcDate"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        away = match.get("awayTeam", {}).get("name", "TBC")
        competition = match.get("competition", {}).get("name")
        name = f"Aston Villa v {away}"
        if competition:
            name += f" ({competition})"
        events.append(
            {
                "name": name,
                "start_utc": start_utc,
                "time_known": True,
                "source": "football-data.org",
            }
        )
    return events


# ------------------------------------------------------------ ticketmaster


def resolve_villa_park_venue_id(api_key):
    """Find the Discovery API venue id for Villa Park, Birmingham (GB)."""
    params = urllib.parse.urlencode(
        {"apikey": api_key, "keyword": "Villa Park", "countryCode": "GB", "size": 20}
    )
    data = http_get_json(f"{TICKETMASTER_API}/venues.json?{params}")
    venues = data.get("_embedded", {}).get("venues", [])

    def is_birmingham(venue):
        return venue.get("city", {}).get("name", "").lower() == "birmingham"

    # Exact name match in Birmingham first, then a looser fallback.
    for venue in venues:
        if venue.get("name", "").strip().lower() == "villa park" and is_birmingham(venue):
            return venue["id"]
    for venue in venues:
        if "villa park" in venue.get("name", "").lower() and is_birmingham(venue):
            return venue["id"]
    raise RuntimeError(
        "Could not find Villa Park (Birmingham) in Ticketmaster venue search; "
        "set the TICKETMASTER_VENUE_ID environment variable to pin it manually."
    )


def fetch_ticketmaster_events(api_key, now_utc):
    """Upcoming events at the Villa Park venue via the Discovery API."""
    venue_id = os.environ.get("TICKETMASTER_VENUE_ID") or resolve_villa_park_venue_id(api_key)
    params = urllib.parse.urlencode(
        {
            "apikey": api_key,
            "venueId": venue_id,
            "sort": "date,asc",
            "size": 100,
            "startDateTime": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    data = http_get_json(f"{TICKETMASTER_API}/events.json?{params}")

    events = []
    for event in data.get("_embedded", {}).get("events", []):
        if event.get("dates", {}).get("status", {}).get("code") == "cancelled":
            continue
        start = event.get("dates", {}).get("start", {})
        record = {
            "name": event.get("name", "Unnamed event"),
            "source": "ticketmaster",
        }
        if start.get("dateTime"):
            record["start_utc"] = datetime.strptime(
                start["dateTime"], "%Y-%m-%dT%H:%M:%SZ"
            ).replace(tzinfo=timezone.utc)
            record["time_known"] = True
        elif start.get("localDate"):
            # Time TBA: treat as start of day in UK time so it still sorts
            # and still triggers a morning warning on the right day.
            local_date = datetime.strptime(start["localDate"], "%Y-%m-%d").date()
            record["start_utc"] = datetime(
                local_date.year, local_date.month, local_date.day, tzinfo=LONDON
            ).astimezone(timezone.utc)
            record["time_known"] = False
        else:
            continue
        events.append(record)
    return events, venue_id


# ----------------------------------------------------------------- helpers


def merge_events(football, ticketmaster):
    """Merge the two sources, dropping Ticketmaster duplicates of fixtures
    (e.g. hospitality listings for the same match)."""
    match_dates = {e["start_utc"].astimezone(LONDON).date() for e in football}
    merged = list(football)
    for event in ticketmaster:
        event_date = event["start_utc"].astimezone(LONDON).date()
        if "aston villa" in event["name"].lower() and event_date in match_dates:
            continue
        merged.append(event)
    merged.sort(key=lambda e: e["start_utc"])
    return merged


def serialise_event(event, today_london):
    local = event["start_utc"].astimezone(LONDON)
    return {
        "name": event["name"],
        "start_utc": event["start_utc"].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "date_local": local.date().isoformat(),
        "date_pretty": local.strftime("%a %d %b %Y"),
        "time_local": local.strftime("%H:%M") if event["time_known"] else "TBC",
        "today": local.date() == today_london,
        "source": event["source"],
    }


def send_ntfy(topic, title, body, priority="default", tags=""):
    url = topic if topic.startswith("http") else f"https://ntfy.sh/{topic}"
    headers = {
        # urllib sends headers latin-1 encoded; keep the title ASCII-safe.
        "Title": title.encode("ascii", "replace").decode("ascii"),
        "Priority": priority,
    }
    if tags:
        headers["Tags"] = tags
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def build_notification(todays_events, warnings):
    if todays_events:
        lines = []
        for event in todays_events:
            when = event["time_local"]
            if when != "TBC":
                lines.append(f"AW SHIT! The fucking Villa's on today at {when}! {event['name']}")
            else:
                lines.append(f"AW SHIT! The fucking Villa's on today! {event['name']} (time TBC)")
        body = "\n".join(lines)
        if warnings:
            body += "\n(" + "; ".join(warnings) + ")"
        return ("Villa Park event TODAY — move the car!", body, "high", "rotating_light,stadium")
    body = "No villa games today \U0001f389Thank Fuck!!"
    if warnings:
        body += "\n(" + "; ".join(warnings) + ")"
    return ("Villa Park", body, "default", "white_check_mark")


# -------------------------------------------------------------------- main


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-notify", action="store_true", help="update events.json only")
    parser.add_argument("--output", default="docs/events.json", help="output JSON path")
    args = parser.parse_args()

    now_utc = datetime.now(timezone.utc)
    today_london = now_utc.astimezone(LONDON).date()

    warnings = []
    football, ticketmaster, venue_id = [], [], None

    football_key = os.environ.get("FOOTBALL_DATA_API_KEY")
    if football_key:
        try:
            football = fetch_football_events(football_key, today_london)
            print(f"football-data.org: {len(football)} upcoming home fixtures")
        except Exception as exc:  # noqa: BLE001 - keep going with the other source
            warnings.append("couldn't check football fixtures")
            print(f"WARNING: football-data.org failed: {exc}", file=sys.stderr)
    else:
        warnings.append("FOOTBALL_DATA_API_KEY not set")
        print("WARNING: FOOTBALL_DATA_API_KEY not set", file=sys.stderr)

    ticketmaster_key = os.environ.get("TICKETMASTER_API_KEY")
    if ticketmaster_key:
        try:
            ticketmaster, venue_id = fetch_ticketmaster_events(ticketmaster_key, now_utc)
            print(f"Ticketmaster: {len(ticketmaster)} upcoming events (venue {venue_id})")
        except Exception as exc:  # noqa: BLE001
            warnings.append("couldn't check Ticketmaster")
            print(f"WARNING: Ticketmaster failed: {exc}", file=sys.stderr)
    else:
        warnings.append("TICKETMASTER_API_KEY not set")
        print("WARNING: TICKETMASTER_API_KEY not set", file=sys.stderr)

    both_sources_failed = len(warnings) >= 2

    merged = merge_events(football, ticketmaster)
    serialised = [serialise_event(e, today_london) for e in merged]
    todays_events = [e for e in serialised if e["today"]]

    output = {
        "updated_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_local": now_utc.astimezone(LONDON).strftime("%a %d %b %Y, %H:%M %Z"),
        "ticketmaster_venue_id": venue_id,
        "warnings": warnings,
        "events": serialised[:MAX_EVENTS_ON_PAGE],
    }
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"Wrote {args.output} ({len(output['events'])} events listed)")

    if not args.no_notify:
        topic = os.environ.get("NTFY_TOPIC")
        if not topic:
            print("ERROR: NTFY_TOPIC not set, cannot notify", file=sys.stderr)
            return 1
        if both_sources_failed:
            title, body, priority, tags = (
                "Villa Park check FAILED",
                "Couldn't reach the event APIs — check manually before parking.",
                "high",
                "warning",
            )
        else:
            title, body, priority, tags = build_notification(todays_events, warnings)
        send_ntfy(topic, title, body, priority, tags)
        print(f"Sent ntfy notification: {title!r}")

    return 1 if both_sources_failed else 0


if __name__ == "__main__":
    sys.exit(main())
