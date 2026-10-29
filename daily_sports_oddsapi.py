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
    SHOW_LINES    Set to 0 to drop the betting LINE column (default: on).
                  NBA/NFL/WNBA/college show the point spread; MLB/NHL/soccer
                  show the moneyline favorite.
    SHOW_TOTALS   Set to 1 to add an O/U (over/under) column (default: off).
    BOOKMAKER     Preferred sportsbook key (default: draftkings). If it has no
                  line for a game, the first book that does is used.
    SHOW_TV       Set to 0 to drop the WATCH column (default: on).
    SHORT_NAMES   Set to 0 to use full team names (default: on).
    SHOW_SCORES   Set to 1 to also fetch live scores (1 credit per league per run).
    ANSI          Set to 1 for COLORED tables (colored/bold headings, green lines,
                  red LIVE tags). Discord shows colors on desktop and web only;
                  on mobile it can look plain or garbled, so it is off by default.
    TABLE_WIDTH   Max characters per table line (default: 52). Discord wraps
                  longer lines onto a second line. The script automatically
                  compacts each table to fit: shorter times (7:10p), then team
                  abbreviations (CBJ @ CAR), then drops the O/U column. Lower
                  this (for example 40) if tables wrap on phones.
    BIG_HEADERS   Large markdown headers for the post title and each league
                  (default: on). Set to 0 to use plain embed titles instead.

Odds API credit cost per run (free plan = 500 credits/month):
    - Schedule (events) and sports list: free.
    - Lines: 1 credit per in-season league that has games today.
    - Totals: +1 credit per league. Scores: +1 credit per league.
    The script prints your remaining credits to the log after each run.

Notes:
    - Leagues that are out of season are skipped automatically.
    - Short team names, abbreviations, and TV info come from ESPN's unofficial
      scoreboard. If that lookup fails or a team can't be matched, the game
      falls back to the full name / no network, and the post still goes out.
    - The WATCH column shows ONE network per game (the national broadcaster
      when there is one).
"""

import os
import sys
import time as time_module
import unicodedata
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

# Which betting market is shown in the LINE column for each league.
#   spreads = point spread (favorite and how many points)
#   h2h     = moneyline (favorite and price)
LINE_MARKET = {
    "nba": "spreads", "nfl": "spreads", "wnba": "spreads",
    "ncaaf": "spreads", "ncaam": "spreads",
    "mlb": "h2h", "nhl": "h2h", "epl": "h2h", "mls": "h2h", "ucl": "h2h",
}

# Fallback only (used when ESPN has no short name): "Los Angeles Lakers" -> "Lakers"
SHORT_NAME_LEAGUES = {"nba", "nfl", "mlb", "nhl", "wnba"}
TWO_WORD_NICKNAMES = (
    "Trail Blazers", "Red Sox", "White Sox", "Blue Jays", "Blue Jackets",
    "Maple Leafs", "Golden Knights", "Red Wings", "Hockey Club",
)

# Long network names are shortened so the WATCH column stays narrow.
NETWORK_SHORT = {
    "NBA League Pass": "NBA LP",
    "Prime Video": "Prime",
    "Amazon Prime Video": "Prime",
    "Apple TV+": "Apple TV",
    "Paramount+": "Paramount",
    "NFL Network": "NFL Net",
    "MLB Network": "MLB Net",
    "NHL Network": "NHL Net",
    "CBS Sports Network": "CBSSN",
    "Big Ten Network": "BTN",
    "SEC Network": "SECN",
    "ACC Network": "ACCN",
    "Fox Sports 1": "FS1",
    "Fox Sports 2": "FS2",
    "YouTube TV": "YouTube",
}
MAX_WATCH_WIDTH = 9


def shorten_network(name: str) -> str:
    name = NETWORK_SHORT.get(name, name)
    if len(name) > MAX_WATCH_WIDTH:
        name = name[:MAX_WATCH_WIDTH - 1].rstrip() + "…"
    return name


# ESPN is used ONLY for short names, abbreviations, and where to watch.
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
MAX_EMBED_CHARS_PER_MSG = 5800  # Discord caps total embed text per message at 6000

USAGE = {}  # filled from Odds API response headers


# ---------------------------------------------------------------- Odds API --

def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _track_usage(resp: requests.Response) -> None:
    for header, key in (("x-requests-remaining", "remaining"), ("x-requests-last", "last")):
        if header in resp.headers:
            USAGE[key] = resp.headers[header]


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
    _track_usage(r)
    r.raise_for_status()
    return {e["id"]: e for e in r.json()}


def fetch_odds(api_key: str, sport_key: str, markets: list) -> dict:
    """Costs 1 credit per market (US region). Returns {event_id: odds_event}."""
    def call(mkts):
        r = requests.get(
            f"{API}/sports/{sport_key}/odds",
            params={
                "apiKey": api_key,
                "regions": "us",
                "markets": ",".join(mkts),
                "oddsFormat": "american",
                "dateFormat": "iso",
            },
            timeout=20,
        )
        _track_usage(r)
        r.raise_for_status()
        return {e["id"]: e for e in r.json()}

    try:
        return call(markets)
    except requests.HTTPError:
        # e.g. a sport that doesn't offer totals: retry with just the main line
        if len(markets) > 1:
            return call(markets[:1])
        raise


# ------------------------------------------------------- ESPN lookups ------

def normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", name.lower().replace("&", "and"))
    return "".join(ch for ch in name if ch.isalnum())


def _market_type(market) -> str:
    if isinstance(market, dict):
        market = market.get("type", "")
    return str(market or "").lower()


def get_main_broadcast(comp: dict) -> str:
    """Return ONE network: the national broadcaster if there is one, else the first listed."""
    broadcasts = comp.get("broadcasts") or []
    geo = comp.get("geoBroadcasts") or []

    for b in broadcasts:  # national first
        if _market_type(b.get("market")) == "national" and b.get("names"):
            return b["names"][0]
    for g in geo:
        name = (g.get("media") or {}).get("shortName")
        if name and _market_type(g.get("market")) == "national":
            return name

    for b in broadcasts:  # otherwise whatever is listed first
        if b.get("names"):
            return b["names"][0]
    for g in geo:
        name = (g.get("media") or {}).get("shortName")
        if name:
            return name
    return ""


def fetch_espn_info(league_key: str, dates: list) -> dict:
    """Return {"games": {(away, home): 'ESPN'}, "teams": {name: {"short", "abbr"}}}."""
    info = {"games": {}, "teams": {}}
    path = ESPN_PATHS.get(league_key)
    if not path:
        return info

    for d in dates:
        params = {"dates": d, "limit": "300", **ESPN_EXTRA_PARAMS.get(league_key, {})}
        try:
            r = requests.get(ESPN_URL.format(path=path), params=params, timeout=15)
            r.raise_for_status()
            espn_events = r.json().get("events", [])
        except (requests.RequestException, ValueError):
            continue  # ESPN data is a bonus; never fail the whole post over it

        for ev in espn_events:
            try:
                comp = ev["competitions"][0]
                names = {}
                for c in comp["competitors"]:
                    team = c.get("team") or {}
                    display = team.get("displayName", "")
                    if display:
                        info["teams"][normalize(display)] = {
                            "short": team.get("shortDisplayName") or "",
                            "abbr": team.get("abbreviation") or "",
                        }
                    names[c.get("homeAway")] = normalize(display)
                tv = get_main_broadcast(comp)
                if tv and names.get("away") and names.get("home"):
                    info["games"][(names["away"], names["home"])] = tv
            except (KeyError, IndexError, TypeError):
                continue
    return info


def find_team(teams: dict, name: str):
    n = normalize(name)
    if n in teams:
        return teams[n]
    for k, v in teams.items():  # fallback for naming differences
        if k and n and (k in n or n in k):
            return v
    return None


def find_broadcast(games: dict, away_team: str, home_team: str) -> str:
    a, h = normalize(away_team), normalize(home_team)
    if (a, h) in games:
        return games[(a, h)]
    for (la, lh), tv in games.items():
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


def team_display(name: str, league_key: str, teams: dict, use_short: bool) -> str:
    """Name shown in the GAME column."""
    if not use_short:
        return name
    info = find_team(teams, name)
    if info and info.get("short"):
        return info["short"]            # e.g. "Ohio State", "Man United"
    if league_key in SHORT_NAME_LEAGUES:
        return short_name(name)          # fallback: nickname only
    return name


def team_abbr(name: str, teams: dict, fallback: str) -> str:
    """Name shown in the LINE column (abbreviation keeps it narrow)."""
    info = find_team(teams, name)
    return (info or {}).get("abbr") or fallback


def pick_outcomes(odds_event, market_key: str, preferred: str):
    """Outcomes for a market, from the preferred book if it has one."""
    if not odds_event:
        return None
    books = sorted(odds_event.get("bookmakers", []), key=lambda b: b.get("key") != preferred)
    for b in books:
        for m in b.get("markets", []):
            if m.get("key") == market_key and m.get("outcomes"):
                return m["outcomes"]
    return None


def get_lines(odds_event, market: str, labels: dict, preferred: str, want_totals: bool) -> tuple:
    """Return (line, total) strings, e.g. ('BOS -5.5', '221.5')."""
    line = total = ""

    outcomes = pick_outcomes(odds_event, market, preferred)
    if outcomes:
        teams = [o for o in outcomes if o.get("name") in labels]  # skips "Draw"
        if market == "spreads":
            teams = [o for o in teams if o.get("point") is not None]
            if teams:
                fav = min(teams, key=lambda o: o["point"])  # most negative = favorite
                line = "PK" if fav["point"] == 0 else f"{labels[fav['name']]} {fav['point']:+g}"
        elif market == "h2h":
            teams = [o for o in teams if o.get("price") is not None]
            if teams:
                fav = min(teams, key=lambda o: o["price"])  # lowest American price = favorite
                line = f"{labels[fav['name']]} {int(fav['price']):+d}"

    if want_totals:
        outs = pick_outcomes(odds_event, "totals", preferred)
        if outs:
            over = next(
                (o for o in outs if str(o.get("name")).lower() == "over" and o.get("point") is not None),
                None,
            )
            if over:
                total = f"{over['point']:g}"

    return line, total


def make_row(event: dict, tz: ZoneInfo, score_event, away_d: str, home_d: str,
             line: str = "", total: str = "", tv: str = "", game_abbr: str = "") -> dict:
    game = f"{away_d} @ {home_d}"
    row = {
        "time": "", "time_short": "",
        "game": game, "game_abbr": game_abbr or game,
        "score": "", "line": "", "total": "", "watch": "",
    }

    scores = (score_event or {}).get("scores")
    if scores:
        by_team = {s["name"]: s["score"] for s in scores}
        row["score"] = f"{by_team.get(event['away_team'], '0')}–{by_team.get(event['home_team'], '0')}"
        if score_event.get("completed"):
            row["time"] = row["time_short"] = "FINAL"
        else:
            row["time"] = row["time_short"] = "LIVE"
            row["watch"] = tv
        return row

    start = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00")).astimezone(tz)
    clock = start.strftime("%I:%M").lstrip("0")
    row.update(
        time=f"{clock} {start.strftime('%p')}",           # 7:10 PM
        time_short=f"{clock}{'a' if start.hour < 12 else 'p'}",  # 7:10p
        line=line, total=total, watch=tv,
    )
    return row


# ANSI styles (only used when ANSI=1). Discord renders these on desktop/web.
HEAD_STYLE = "1;33"          # bold yellow column headings
SEP_STYLE = "30"             # gray grid lines
LIVE_STYLE = "1;31"          # bold red
COLUMN_STYLE = {"time": "36", "score": "1", "line": "32", "total": "35"}


def _style(text: str, code, ansi: bool) -> str:
    return f"\u001b[{code}m{text}\u001b[0m" if (ansi and code) else text


def _plan_table(rows: list, show_watch: bool, max_width: int) -> tuple:
    """Pick the roomiest layout whose widest line fits in max_width characters.

    Discord wraps long lines inside a code block, which wrecks the table, so
    we compact step by step: shorter times, then abbreviations, then drop O/U,
    and finally truncate the GAME column.
    """
    def columns_for(drop_total: bool) -> list:
        cols = [("TIME", "time"), ("GAME", "game")]
        if any(r["score"] for r in rows):
            cols.append(("SCORE", "score"))
        if any(r["line"] for r in rows):
            cols.append(("LINE", "line"))
        if any(r["total"] for r in rows) and not drop_total:
            cols.append(("O/U", "total"))
        if show_watch:
            cols.append(("WATCH", "watch"))
        return cols

    def value(r: dict, key: str, short_time: bool, abbr: bool) -> str:
        if key == "time":
            v = r["time_short"] if short_time else r["time"]
        elif key == "game":
            v = r["game_abbr"] if abbr else r["game"]
        else:
            v = r[key]
        return v or ("-" if key not in ("time", "game") else "")

    # (short_time, abbreviations, drop_total)
    attempts = [(False, False, False), (True, False, False), (True, True, False), (True, True, True)]
    for short_time, abbr, drop_total in attempts:
        cols = columns_for(drop_total)
        headers = [h for h, _ in cols]
        table = [[value(r, k, short_time, abbr) for _, k in cols] for r in rows]
        widths = [max(len(h), *(len(row[i]) for row in table)) for i, h in enumerate(headers)]
        width = sum(widths) + 3 * (len(widths) - 1)
        if width <= max_width:
            break
    else:
        # Last resort: truncate the GAME column (index 1) with an ellipsis
        room = max(10, widths[1] - (width - max_width))
        for row in table:
            if len(row[1]) > room:
                row[1] = row[1][:room - 1] + "…"
        widths[1] = max(len(headers[1]), *(len(row[1]) for row in table))

    return [k for _, k in cols], headers, table, widths


def build_table_embeds(title: str, color: int, rows: list, show_watch: bool = True,
                       ansi: bool = False, big_headers: bool = True,
                       max_width: int = 52) -> list:
    """Render rows as a grid table inside a code block.

    Discord doesn't render Markdown tables, and font size can't change inside a
    code block, so the league title gets a big markdown header above the table
    and the table itself uses box-drawing grid lines (and optional ANSI colors).
    The layout is auto-compacted so no line exceeds max_width characters (which
    would wrap). Columns with no data are left out. Splits into extra embeds if
    it would exceed Discord's size limit.
    """
    keys, headers, table, widths = _plan_table(rows, show_watch, max_width)
    n = len(keys)

    def cell(i: int, text: str, code) -> str:
        body = text.ljust(widths[i]) if i < n - 1 else text   # last column unpadded
        if i == 0:
            shown = body + " "
        elif i == n - 1:
            shown = " " + body
        else:
            shown = " " + body + " "
        return _style(shown, code, ansi)

    sep = _style("│", SEP_STYLE, ansi)

    def render(cells: list, is_head: bool = False) -> str:
        out = []
        for i, (text, key) in enumerate(zip(cells, keys)):
            if is_head:
                code = HEAD_STYLE
            elif key == "time" and text == "LIVE":
                code = LIVE_STYLE
            else:
                code = COLUMN_STYLE.get(key)
            out.append(cell(i, text, code))
        return sep.join(out)

    head = render(headers, is_head=True)
    divider = _style(
        "┼".join("─" * (w + (1 if i in (0, n - 1) else 2)) for i, w in enumerate(widths)),
        SEP_STYLE, ansi,
    )
    lines = [render(r) for r in table]

    lang = "ansi" if ansi else ""
    header_room = len(f"## {title} (cont.)") + 1 if big_headers else 0
    overhead = header_room + len(f"```{lang}\n") + len(head) + 1 + len(divider) + 1 + len("```")
    chunks, current, size = [], [], overhead
    for line in lines:
        if current and size + len(line) + 1 > EMBED_DESC_LIMIT:
            chunks.append(current)
            current, size = [], overhead
        current.append(line)
        size += len(line) + 1
    if current:
        chunks.append(current)

    embeds = []
    for i, chunk in enumerate(chunks):
        name = title + (" (cont.)" if i else "")
        body = f"```{lang}\n{head}\n{divider}\n" + "\n".join(chunk) + "\n```"
        embed = {"color": color}
        if big_headers:
            embed["description"] = f"## {name}\n{body}"
        else:
            embed["title"] = name
            embed["description"] = body
        embeds.append(embed)
    return embeds


def embed_size(embed: dict) -> int:
    return (len(embed.get("title", "")) + len(embed.get("description", ""))
            + len((embed.get("footer") or {}).get("text", "")))


def batch_embeds(embeds: list) -> list:
    """Group embeds so each message stays under Discord's count and total-size limits."""
    batches, current, size = [], [], 0
    for e in embeds:
        s = embed_size(e)
        if current and (len(current) >= MAX_EMBEDS_PER_MSG or size + s > MAX_EMBED_CHARS_PER_MSG):
            batches.append(current)
            current, size = [], 0
        current.append(e)
        size += s
    if current:
        batches.append(current)
    return batches


def post_to_discord(webhook_url: str, content: str, embeds: list) -> None:
    if not embeds:
        r = requests.post(webhook_url, json={"content": content}, timeout=15)
        r.raise_for_status()
        return
    for i, batch in enumerate(batch_embeds(embeds)):
        payload = {"embeds": batch}
        if i == 0:
            payload["content"] = content
        else:
            time_module.sleep(1)  # stay well inside webhook rate limits
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
    show_lines = os.environ.get("SHOW_LINES", "1") != "0"
    show_totals = show_lines and os.environ.get("SHOW_TOTALS") == "1"
    use_short = os.environ.get("SHORT_NAMES", "1") != "0"
    ansi = os.environ.get("ANSI") == "1"
    big_headers = os.environ.get("BIG_HEADERS", "1") != "0"
    try:
        max_width = int(os.environ.get("TABLE_WIDTH", "52"))
    except ValueError:
        max_width = 52
    book = os.environ.get("BOOKMAKER", "draftkings").strip().lower()
    selected = os.environ.get("LEAGUES")
    keys = [k.strip().lower() for k in selected.split(",")] if selected else list(LEAGUES)
    need_espn = show_tv or use_short or show_lines  # names/abbreviations/TV

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
    any_lines = False
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

        espn = fetch_espn_info(key, espn_dates) if need_espn else {"games": {}, "teams": {}}

        odds = {}
        market = LINE_MARKET.get(key, "h2h")
        if show_lines:
            wanted = [market] + (["totals"] if show_totals else [])
            try:
                odds = fetch_odds(api_key, sport_key, wanted)
            except requests.RequestException as e:
                print(f"Could not fetch lines for {name}: {e}", file=sys.stderr)

        rows = []
        for e in events:
            away, home = e["away_team"], e["home_team"]
            away_d = team_display(away, key, espn["teams"], use_short)
            home_d = team_display(home, key, espn["teams"], use_short)
            labels = {
                away: team_abbr(away, espn["teams"], away_d),
                home: team_abbr(home, espn["teams"], home_d),
            }
            line, total = get_lines(odds.get(e["id"]), market, labels, book, show_totals)
            tv = shorten_network(find_broadcast(espn["games"], away, home)) if show_tv else ""
            game_abbr = f"{labels[away]} @ {labels[home]}"
            rows.append(make_row(e, tz, scores.get(e["id"]), away_d, home_d, line, total, tv, game_abbr))

        any_lines = any_lines or any(r["line"] or r["total"] for r in rows)
        embeds.extend(build_table_embeds(
            f"{icon} {name}", color, rows,
            show_watch=show_tv, ansi=ansi, big_headers=big_headers, max_width=max_width,
        ))

    if not embeds:
        post_to_discord(webhook_url, f"No games found for {date_label}.", [])
        return 0

    if any_lines:
        embeds[-1]["footer"] = {"text": "Lines from The Odds API · they move, so check your book"}

    if big_headers:
        header = f"# 🏟️ Today's Games\n-# {date_label} · times in {now.strftime('%Z')}"
    else:
        header = f"**Today's games** · {date_label} · times in {now.strftime('%Z')}"
    post_to_discord(webhook_url, header, embeds)
    print(f"Posted {len(embeds)} embed(s).")
    if USAGE:
        print(f"Odds API credits — last call: {USAGE.get('last')}, remaining: {USAGE.get('remaining')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
