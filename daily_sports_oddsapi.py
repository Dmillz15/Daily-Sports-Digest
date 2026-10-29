#!/usr/bin/env python3
"""
Daily sports aggregator (The Odds API) -> Discord webhook, as tables.

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
    SHOW_SCORES   Set to 1 to also fetch live scores (1 credit per league per run).
    SHOW_TV       Set to 0 to drop the WATCH column (default: on).
    SHORT_NAMES   Set to 0 to use full team names for NBA/NFL/MLB/NHL/WNBA
                  instead of just the nickname (default: on).

Notes:
    - The events and sports endpoints do not count against your Odds API quota.
    - Leagues that are out of season are skipped automatically.
    - The WATCH column shows ONE network per game (the national broadcaster when
      there is one). It comes from ESPN's unofficial scoreboard; if that fails,
      the column just shows "-".
"""

import os
import sys
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

API = "https://api.the-odds-api.com/v4"

# key -> (display name, Odds API sport key, embed color, icon)
LEAGUES = {
    "nba":   ("NBA",                 "basketball_nba",            0xC9082A, "🏀"),
    "nfl":   ("NFL",                 "americanfootball_nfl",      0x013369, "🏈"),
    "mlb":   ("MLB",                 "baseball_mlb",              0x041E42, "⚾"),
    "nhl":   ("NHL",                 "icehockey_nhl",             0x2B2D31, "🏒"),
    "wnba":  ("WNBA",                "basketball_wnba",           0xF57800, "🏀"),
    "ncaaf": ("College Football",    "americanfootball_ncaaf",    0x7A1F1F, "🏈"),
    "ncaam": ("Men's College Hoops", "basketball_ncaab",          0x1F4E79, "🏀"),
    "epl":   ("Premier League",      "soccer_epl",                0x3D195B, "⚽"),
    "mls":   ("MLS",                 "soccer_usa_mls",            0x80A0C0, "⚽"),
    "ucl":   ("Champions League",    "soccer_uefa_champs_league", 0x0A1C5C, "⚽"),
}

# Leagues where "Los Angeles Lakers" can safely be shortened to "Lakers"
SHORT_NAME_LEAGUES = {"nba", "nfl", "mlb", "nhl", "wnba"}
TWO_WORD_NICKNAMES = (
    "Trail Blazers", "Red Sox", "White Sox", "Blue Jays", "Blue Jackets",
    "Maple Leafs", "Golden Knights", "Red Wings", "Hockey Club",
)

# ESPN is used ONLY to look up where each game is broadcast.
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
ESPN_EXTRA_PARAMS = {
    "ncaaf": {"groups": "80"},
    "ncaam": {"groups": "50"},
}

EMBED_DESC_LIMIT = 4000   # Discord hard limit is 4096
MAX_EMBEDS_PER_MSG = 10   # Discord limit per webhook message


# ---------------------------------------------------------------- Odds API --

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


# ------------------------------------------------------- ESPN TV lookup ----

def normalize(name: str) -> str:
    return "".join(ch for ch in name.lower().replace("&", "and") if ch.isalnum())


def _market_type(market) -> str:
    if isinstance(market, dict):
        market = market.get("type", "")
    return str(market or "").lower()


def get_main_broadcast(comp: dict) -> str:
    """Return ONE network: the national broadcaster if there is one, else the first listed."""
    broadcasts = comp.get("broadcasts") or []
    geo = comp.get("geoBroadcasts") or []

    # 1) National broadcasts first
    for b in broadcasts:
        if _market_type(b.get("market")) == "national" and b.get("names"):
            return b["names"][0]
    for g in geo:
        name = (g.get("media") or {}).get("shortName")
        if name and _market_type(g.get("market")) == "national":
            return name

    # 2) Otherwise whatever is listed first
    for b in broadcasts:
        if b.get("names"):
            return b["names"][0]
    for g in geo:
        name = (g.get("media") or {}).get("shortName")
        if name:
            return name
    return ""


def fetch_espn_broadcasts(league_key: str, dates: list) -> dict:
    """Return {(normalized_away, normalized_home): 'ESPN'} from ESPN."""
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
                tv = get_main_broadcast(comp)
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
    for (la, lh), tv in lookup.items():  # fallback for naming differences
        if (la in a or a in la) and (lh in h or h in lh):
            return tv
    return ""


# -------------------------------------------------------------- formatting --

def short_name(name: str) -> str:
    """'Los Angeles Lakers' -> 'Lakers', 'Boston Red Sox' -> 'Red Sox'."""
    for nick in TWO_WORD_NICKNAMES:
        if name.endswith(nick):
            return nick
    return name.split()[-1]


def make_row(event: dict, tz: ZoneInfo, score_event: dict = None,
             tv: str = "", short: bool = False) -> tuple:
    """Return (time, game, score, watch) for one table row."""
    full_away, full_home = event["away_team"], event["home_team"]
    if short:
        away, home = short_name(full_away), short_name(full_home)
    else:
        away, home = full_away, full_home
    game = f"{away} @ {home}"

    scores = (score_event or {}).get("scores")
    if scores:
        by_team = {s["name"]: s["score"] for s in scores}
        score = f"{by_team.get(full_away, '0')}–{by_team.get(full_home, '0')}"
        if score_event.get("completed"):
            return ("FINAL", game, score, "")
        return ("LIVE", game, score, tv)

    start = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00")).astimezone(tz)
    when = start.strftime("%I:%M %p").lstrip("0")
    return (when, game, "", tv)


def build_table_embeds(title: str, color: int, rows: list, show_watch: bool = True) -> list:
    """Render rows as an aligned monospace table inside a code block.

    Discord doesn't render Markdown tables, so a code block is the way to get
    aligned columns. Splits into extra embeds if it would exceed Discord's limit.
    """
    show_score = any(r[2] for r in rows)
    headers = ["TIME", "GAME"] + (["SCORE"] if show_score else []) + (["WATCH"] if show_watch else [])

    table = []
    for when, game, score, watch in rows:
        cells = [when, game]
        if show_score:
            cells.append(score or "-")
        if show_watch:
            cells.append(watch or "-")
        table.append(cells)

    widths = [max(len(h), *(len(r[i]) for r in table)) for i, h in enumerate(headers)]

    def fmt(cells):
        return "  ".join(c.ljust(w) for c, w in zip(cells, widths)).rstrip()

    head = fmt(headers)
    divider = "  ".join("-" * w for w in widths)
    lines = [fmt(r) for r in table]

    overhead = len("```\n") + len(head) + 1 + len(divider) + 1 + len("```")
    chunks, current, size = [], [], overhead
    for line in lines:
        if current and size + len(line) + 1 > EMBED_DESC_LIMIT:
            chunks.append(current)
            current, size = [], overhead
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append(current)

    return [
        {
            "title": title + (" (cont.)" if i else ""),
            "description": "```\n" + head + "\n" + divider + "\n" + "\n".join(chunk) + "\n```",
            "color": color,
        }
        for i, chunk in enumerate(chunks)
    ]


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


# -------------------------------------------------------------------- main --

def main() -> int:
    api_key = os.environ.get("ODDS_API_KEY")
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not api_key or not webhook_url:
        print("Set ODDS_API_KEY and DISCORD_WEBHOOK_URL environment variables.", file=sys.stderr)
        return 1

    tz = ZoneInfo(os.environ.get("TIMEZONE", "America/New_York"))
    show_scores = os.environ.get("SHOW_SCORES") == "1"
    show_tv = os.environ.get("SHOW_TV", "1") != "0"
    use_short = os.environ.get("SHORT_NAMES", "1") != "0"
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
        name, sport_key, color, icon = LEAGUES[key]
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
        short = use_short and key in SHORT_NAME_LEAGUES
        rows = [
            make_row(
                e, tz, scores.get(e["id"]),
                find_broadcast(tv_lookup, e["away_team"], e["home_team"]),
                short,
            )
            for e in events
        ]
        embeds.extend(build_table_embeds(f"{icon} {name}", color, rows, show_watch=show_tv))

    if not embeds:
        post_to_discord(webhook_url, f"No games found for {date_label}.", [])
        return 0

    header = f"**Today's games** · {date_label} · times in {now.strftime('%Z')}"
    post_to_discord(webhook_url, header, embeds)
    print(f"Posted {len(embeds)} embed(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
