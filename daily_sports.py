#!/usr/bin/env python3
"""
Daily sports aggregator -> Discord webhook.
"""
#Setup:
pip install requests
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/1565352637094699221/IBcKRjqDOeIlOLanX3ASOMYiy6xTcuM5kxegOpkZDUzy9-fDPPOYCEjdFFdHYOzn4qhA"
"""
Optional env vars:
    TIMEZONE   IANA timezone for displayed game times (default: America/New_York)
    LEAGUES    Comma-separated keys from the LEAGUES dict below
               (default: all of them). Example: LEAGUES=nba,nhl,epl

Data source: ESPN's public (unofficial, undocumented) scoreboard endpoints.
They work without a key but may change without notice.
"""

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

# key -> (display name, ESPN sport/league path, embed color)
LEAGUES = {
    "nba":   ("NBA",                 "basketball/nba",                 0xC9082A),
    "nfl":   ("NFL",                 "football/nfl",                   0x013369),
    "mlb":   ("MLB",                 "baseball/mlb",                   0x041E42),
    "nhl":   ("NHL",                 "hockey/nhl",                     0x000000),
    "wnba":  ("WNBA",                "basketball/wnba",                0xF57800),
    "ncaaf": ("College Football",    "football/college-football",      0x7A1F1F),
    "ncaam": ("Men's College Hoops", "basketball/mens-college-basketball", 0x1F4E79),
    "epl":   ("Premier League",      "soccer/eng.1",                   0x3D195B),
    "mls":   ("MLS",                 "soccer/usa.1",                   0x80A0C0),
    "ucl":   ("Champions League",    "soccer/uefa.champions",          0x0A1C5C),
}

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
EMBED_DESC_LIMIT = 4000   # Discord hard limit is 4096
MAX_EMBEDS_PER_MSG = 10   # Discord limit per webhook message


def fetch_events(path: str, date_str: str) -> list:
    resp = requests.get(ESPN_URL.format(path=path), params={"dates": date_str}, timeout=15)
    resp.raise_for_status()
    return resp.json().get("events", [])


def format_event(event: dict, tz: ZoneInfo) -> str:
    comp = event["competitions"][0]
    status = event["status"]["type"]
    state = status.get("state")  # pre / in / post

    teams = {c["homeAway"]: c for c in comp["competitors"]}
    away, home = teams.get("away"), teams.get("home")
    if not away or not home:  # fall back for odd formats
        return f"• {event.get('name', 'Unknown game')}"

    away_name = away["team"].get("displayName", "Away")
    home_name = home["team"].get("displayName", "Home")

    if state == "pre":
        start = datetime.fromisoformat(event["date"].replace("Z", "+00:00")).astimezone(tz)
        when = start.strftime("%-I:%M %p %Z") if os.name != "nt" else start.strftime("%I:%M %p %Z").lstrip("0")
        return f"🕒 **{when}** — {away_name} @ {home_name}"

    score = f"{away.get('score', '0')}–{home.get('score', '0')}"
    detail = status.get("shortDetail", "")
    icon = "🔴" if state == "in" else "✅"
    return f"{icon} {away_name} @ {home_name} — **{score}** ({detail})"


def build_embed(name: str, color: int, lines: list, date_label: str) -> list:
    """Return one or more embeds (split if the description is too long)."""
    embeds, chunk = [], ""
    for line in lines:
        if len(chunk) + len(line) + 1 > EMBED_DESC_LIMIT:
            embeds.append(chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk:
        embeds.append(chunk)

    out = []
    for i, desc in enumerate(embeds):
        title = f"{name} — {date_label}" + (f" (cont. {i + 1})" if i else "")
        out.append({"title": title, "description": desc.strip(), "color": color})
    return out


def post_to_discord(webhook_url: str, content: str, embeds: list) -> None:
    # Discord allows max 10 embeds per message, so batch them.
    first = True
    for i in range(0, len(embeds), MAX_EMBEDS_PER_MSG):
        payload = {"embeds": embeds[i:i + MAX_EMBEDS_PER_MSG]}
        if first:
            payload["content"] = content
            first = False
        r = requests.post(webhook_url, json=payload, timeout=15)
        r.raise_for_status()


def main() -> int:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("Set the DISCORD_WEBHOOK_URL environment variable.", file=sys.stderr)
        return 1

    tz = ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))
    selected = os.environ.get("LEAGUES")
    keys = [k.strip().lower() for k in selected.split(",")] if selected else list(LEAGUES)

    today = datetime.now(tz)
    date_str = today.strftime("%Y%m%d")
    date_label = today.strftime("%a, %b %d")

    embeds = []
    for key in keys:
        if key not in LEAGUES:
            print(f"Unknown league '{key}', skipping.", file=sys.stderr)
            continue
        name, path, color = LEAGUES[key]
        try:
            events = fetch_events(path, date_str)
        except requests.RequestException as e:
            print(f"Failed to fetch {name}: {e}", file=sys.stderr)
            continue
        if not events:
            continue

        # Sort by start time
        events.sort(key=lambda e: e.get("date", ""))
        lines = [format_event(e, tz) for e in events]
        embeds.extend(build_embed(name, color, lines, date_label))

    if not embeds:
        post_to_discord(webhook_url, f"📅 No games found for {date_label}.", [])
        return 0

    post_to_discord(webhook_url, f"🏟️ **Today's games — {date_label}**", embeds)
    print(f"Posted {len(embeds)} embed(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
