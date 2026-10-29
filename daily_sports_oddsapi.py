#!/usr/bin/env python3
"""
Daily sports aggregator (The Odds API) -> Discord webhook.

Setup:
    pip install requests
    export ODDS_API_KEY="your-key-from-the-odds-api.com"
    export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/XXXX/YYYY"
    python daily_sports_oddsapi.py

Optional env vars:
    TIMEZONE      IANA timezone for the "today" window and displayed times
                  (default: America/New_York)
    LEAGUES       Comma-separated keys from the LEAGUES dict below
                  (default: all of them). Example: LEAGUES=nba,nhl,epl
    SHOW_SCORES   Set to 1 to also fetch live scores. This costs 1 credit per
                  league per run. The schedule itself (events endpoint) is free.

Notes:
    - The events and sports endpoints do not count against your quota.
    - Leagues that are out of season are skipped automatically.
    - The Odds API lists games that bookmakers offer lines on, so very minor
      games may not appear.
"""

import os
import sys
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

API = "https://api.the-odds-api.com/v4"

# key -> (display name, Odds API sport key, embed color)
LEAGUES = {
    "nba":   ("NBA",                 "basketball_nba",                 0xC9082A),
    "nfl":   ("NFL",                 "americanfootball_nfl",           0x013369),
    "mlb":   ("MLB",                 "baseball_mlb",                   0x041E42),
    "nhl":   ("NHL",                 "icehockey_nhl",                  0x000000),
    "wnba":  ("WNBA",                "basketball_wnba",                0xF57800),
    "ncaaf": ("College Football",    "americanfootball_ncaaf",         0x7A1F1F),
    "ncaam": ("Men's College Hoops", "basketball_ncaab",               0x1F4E79),
    "epl":   ("Premier League",      "soccer_epl",                     0x3D195B),
    "mls":   ("MLS",                 "soccer_usa_mls",                 0x80A0C0),
    "ucl":   ("Champions League",    "soccer_uefa_champs_league",      0x0A1C5C),
}

# ESPN is used ONLY to look up where each game is broadcast (The Odds API has
# no TV data). Unofficial endpoint; if it fails, games are shown without TV info.
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
ESPN_PATHS = {
    "nba":   "basketball/nba",
    "nfl":   "football/nfl",
    "mlb":   "baseball/mlb",
    "nhl":   "hockey/nhl",
    "wnba":  "basketball/wnba",
    "ncaaf": "football/college-football",
    "ncaam": "basketball/mens-college-basketball",
    "epl":   "soccer/eng.1",
    "mls":   "soccer/usa.1",
    "ucl":   "soccer/uefa.champions",
}
# College scoreboards only return featured games unless a group is requested.
ESPN_EXTRA_PARAMS = {
    "ncaaf": {"groups": "80"},
    "ncaam": {"groups": "50"},
}

EMBED_DESC_LIMIT = 4000   # Discord hard limit is 4096
MAX_EMBEDS_PER_MSG = 10   # Discord limit per webhook message


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_active_sport_keys(api_key: str) -> set:
    """Free endpoint: returns the sports currently in season."""
    r = requests.get(f"{API}/sports", params={"apiKey": api_key}, timeout=15)
    r.raise_for_status()
    return {s["key"] for s in r.json()}


def fetch_events(api_key: str, sport_key: str, start: datetime, end: datetime) -> list:
    """Free endpoint: schedule for the given window."""
    r = requests.get(
        f"{API}/sports/{sport_key}/events",
        params={
            "apiKey": api_key,
            "dateFormat": "iso",
            "commenceTimeFrom": iso_utc(start),
            "commenceTimeTo": iso_utc(end),
        },
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


def fetch_scores(api_key: str, sport_key: str) -> dict:
    """Costs 1 credit. Returns {event_id: score_event} for live/upcoming games."""
    r = requests.get(
        f"{API}/sports/{sport_key}/scores",
        params={"apiKey": api_key, "dateFormat": "iso"},
        timeout=15,
    )
    r.raise_for_status()
    return {e["id"]: e for e in r.json()}


def normalize(name: str) -> str:
    return "".join(ch for ch in name.lower().replace("&", "and") if ch.isalnum())


def get_broadcasts(comp: dict, limit: int = 4) -> str:
    """Return a short 'where to watch' string, e.g. 'ESPN, Peacock'."""
    names = []
    for b in comp.get("broadcasts", []):
        names.extend(b.get("names", []))
    for g in comp.get("geoBroadcasts", []):
        media = g.get("media", {}).get("shortName")
        if media:
            names.append(media)

    unique = []
    for n in names:
        if n and n not in unique:
            unique.append(n)
    return ", ".join(unique[:limit])


def fetch_espn_broadcasts(league_key: str, dates: list) -> dict:
    """Return {(normalized_away, normalized_home): 'ESPN, Peacock'} from ESPN."""
    path = ESPN_PATHS.get(league_key)
    if not path:
        return {}

    lookup = {}
    for d in dates:
        params = {"dates": d, "limit": "300", **ESPN_EXTRA_PARAMS.get(league_key, {})}
        try:
            r = requests.get(ESPN_URL.format(path=path), params=params, timeout=15)
            r.raise_for_status()
            espn_events = r.json().get("events", [])
        except (requests.RequestException, ValueError):
            continue  # TV info is a bonus; never fail the whole post over it

        for ev in espn_events:
            try:
                comp = ev["competitions"][0]
                teams = {c["homeAway"]: c["team"].get("displayName", "") for c in comp["competitors"]}
                tv = get_broadcasts(comp)
                away, home = normalize(teams.get("away", "")), normalize(teams.get("home", ""))
                if tv and away and home:
                    lookup[(away, home)] = tv
            except (KeyError, IndexError):
                continue
    return lookup


def find_broadcast(lookup: dict, away_team: str, home_team: str) -> str:
    """Match an Odds API game to ESPN data by team name."""
    a, h = normalize(away_team), normalize(home_team)
    if (a, h) in lookup:
        return lookup[(a, h)]
    # Fallback for naming differences (e.g. "Brighton and Hove Albion" vs "Brighton")
    for (la, lh), tv in lookup.items():
        if (la in a or a in la) and (lh in h or h in lh):
            return tv
    return ""


def format_event(event: dict, tz: ZoneInfo, score_event: dict = None, tv: str = "") -> str:
    away, home = event["away_team"], event["home_team"]
    tv_suffix = f" — 📺 {tv}" if tv else ""
    start = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00")).astimezone(tz)
    when = start.strftime("%I:%M %p %Z").lstrip("0")

    scores = (score_event or {}).get("scores")
    if scores:
        by_team = {s["name"]: s["score"] for s in scores}
        score = f"{by_team.get(away, '0')}–{by_team.get(home, '0')}"
        if score_event.get("completed"):
            return f"✅ {away} @ {home} — **{score}** (Final)"
        return f"🔴 {away} @ {home} — **{score}** (Live){tv_suffix}"

    return f"🕒 **{when}** — {away} @ {home}{tv_suffix}"


def build_embeds(name: str, color: int, lines: list, date_label: str) -> list:
    """Return one or more embeds (split if the description is too long)."""
    chunks, current = [], ""
    for line in lines:
        if len(current) + len(line) + 1 > EMBED_DESC_LIMIT:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current:
        chunks.append(current)

    out = []
    for i, desc in enumerate(chunks):
        title = f"{name} — {date_label}" + (f" (cont. {i + 1})" if i else "")
        out.append({"title": title, "description": desc.strip(), "color": color})
    return out


def post_to_discord(webhook_url: str, content: str, embeds: list) -> None:
    if not embeds:
        r = requests.post(webhook_url, json={"content": content}, timeout=15)
        r.raise_for_status()
        return
    for i in range(0, len(embeds), MAX_EMBEDS_PER_MSG):
        payload = {"embeds": embeds[i:i + MAX_EMBEDS_PER_MSG]}
        if i == 0:
            payload["content"] = content
        r = requests.post(webhook_url, json=payload, timeout=15)
        r.raise_for_status()


def main() -> int:
    api_key = os.environ.get("ODDS_API_KEY")
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not api_key or not webhook_url:
        print("Set ODDS_API_KEY and DISCORD_WEBHOOK_URL environment variables.", file=sys.stderr)
        return 1

    tz = ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))
    show_scores = os.environ.get("SHOW_SCORES") == "1"
    show_tv = os.environ.get("SHOW_TV", "1") != "0"  # set SHOW_TV=0 to turn off
    selected = os.environ.get("LEAGUES")
    keys = [k.strip().lower() for k in selected.split(",")] if selected else list(LEAGUES)

    now = datetime.now(tz)
    day_start = datetime.combine(now.date(), time.min, tzinfo=tz)
    day_end = day_start + timedelta(days=1) - timedelta(seconds=1)
    date_label = now.strftime("%a, %b %d")
    # ESPN buckets games by US date, so check tomorrow too for late games
    espn_dates = [now.strftime("%Y%m%d"), (now + timedelta(days=1)).strftime("%Y%m%d")]

    try:
        active = get_active_sport_keys(api_key)
    except requests.RequestException as e:
        print(f"Could not reach The Odds API: {e}", file=sys.stderr)
        return 1

    embeds = []
    for key in keys:
        if key not in LEAGUES:
            print(f"Unknown league '{key}', skipping.", file=sys.stderr)
            continue
        name, sport_key, color = LEAGUES[key]
        if sport_key not in active:
            continue  # out of season

        try:
            events = fetch_events(api_key, sport_key, day_start, day_end)
            scores = fetch_scores(api_key, sport_key) if (show_scores and events) else {}
        except requests.RequestException as e:
            print(f"Failed to fetch {name}: {e}", file=sys.stderr)
            continue
        if not events:
            continue

        events.sort(key=lambda e: e["commence_time"])
        tv_lookup = fetch_espn_broadcasts(key, espn_dates) if show_tv else {}
        lines = [
            format_event(
                e, tz, scores.get(e["id"]),
                find_broadcast(tv_lookup, e["away_team"], e["home_team"]),
            )
            for e in events
        ]
        embeds.extend(build_embeds(name, color, lines, date_label))

    if not embeds:
        post_to_discord(webhook_url, f"📅 No games found for {date_label}.", [])
        return 0

    post_to_discord(webhook_url, f"🏟️ **Today's games — {date_label}**", embeds)
    print(f"Posted {len(embeds)} embed(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
