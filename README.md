# Daily-Sports-Digest
# 🏟️ Daily Sports Digest for Discord

A small Python script that posts **today's games** to a Discord channel every day: start times, betting lines, and where to watch, organized by league in clean tables. It runs for free on GitHub Actions, so nothing has to stay running on your computer.

## What it posts

Every day at the time you choose, your Discord channel gets a post like this (sample data):

> # 🏟️ Today's Games
> -# Thu, Oct 29 · times in EDT

```
## 🏒 NHL
TIME   | GAME      | LINE     | WATCH
-------+-----------+----------+--------
7:10p  | CBJ @ CAR | CAR -258 | ESPN+
7:40p  | CHI @ DET | DET -162 | Disney+
10:10p | BUF @ ANA | BUF -142 | Disney+

## 🏀 NBA
TIME   | GAME      | LINE     | WATCH
-------+-----------+----------+-------
8:10p  | CLE @ ATL | ATL -1.5 | NBA TV
10:10p | MEM @ POR | POR -7.5 | NBA LP
```

Each league gets its own colored section with a large heading. Leagues with no games today (or that are out of season) are skipped automatically.

### What the columns mean

| Column | Meaning |
|--------|---------|
| **TIME** | Start time in your timezone (`7:10p` = 7:10 PM) |
| **GAME** | Away team `@` home team, using standard team abbreviations |
| **LINE** | The betting favorite. Spread leagues show the point spread (`ATL -1.5`); MLB, NHL, and soccer show the moneyline (`CAR -258`). `PK` means pick'em |
| **O/U** | *(optional)* The over/under total points |
| **SCORE** | *(optional)* Live and final scores |
| **WATCH** | The main TV channel or streaming service for the game |

## Features

- **Ten leagues out of the box:** NBA, NFL, MLB, NHL, WNBA, college football, men's college basketball, Premier League, MLS, and Champions League
- **Betting lines** pulled from a sportsbook of your choice (DraftKings by default)
- **One "where to watch" network per game,** favoring the national broadcaster
- **Built for phones:** tables are compact and auto-fit to a maximum width so lines don't wrap on mobile
- **Runs on a schedule** with GitHub Actions, or on your own machine with cron
- **Handles busy days:** long tables are split across multiple messages to stay inside Discord's size limits
- **Optional colored tables** for desktop users

## Where the data comes from

| Data | Source |
|------|--------|
| Game schedule and start times | [The Odds API](https://the-odds-api.com) |
| Betting lines | [The Odds API](https://the-odds-api.com) |
| Team abbreviations, short names, and TV/streaming info | ESPN's public scoreboard (unofficial, no key required) |

ESPN's feed is undocumented and could change without notice. If it ever fails, the post still goes out, but without TV info or short names for the affected games.

## Setup

You need a GitHub account, a Discord server where you can manage webhooks, and a free Odds API key. Setup takes about 10 minutes.

### 1. Create a Discord webhook

In Discord, go to **Server Settings → Integrations → Webhooks → New Webhook**. Choose the channel to post in and copy the webhook URL.

### 2. Get an Odds API key

Sign up for a free key at [the-odds-api.com](https://the-odds-api.com). The key is sent to your email.

### 3. Add this project to your own GitHub repository

Fork this repository, or create a new one and upload `daily_sports_oddsapi.py`.

### 4. Save your keys as secrets

In your repository, go to **Settings → Secrets and variables → Actions → New repository secret** and add:

| Name | Value |
|------|-------|
| `DISCORD_WEBHOOK_URL` | Your Discord webhook URL |
| `ODDS_API_KEY` | Your Odds API key |

Secrets keep your keys out of the code. Anyone with your webhook URL could post to your channel, so never paste it into a file.

### 5. Add the workflow file

Create a file at `.github/workflows/daily-sports.yml` with this content:

```yaml
name: Daily Sports Post

on:
  schedule:
    - cron: "0 13 * * *"   # 13:00 UTC every day
  workflow_dispatch:        # adds a manual "Run workflow" button

jobs:
  post:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: pip install requests

      - run: python daily_sports_oddsapi.py
        env:
          DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
          ODDS_API_KEY: ${{ secrets.ODDS_API_KEY }}
          TIMEZONE: America/New_York
          # LEAGUES: nba,nhl,epl
          # TABLE_WIDTH: "36"
```

### 6. Test it

Open the **Actions** tab, select **Daily Sports Post**, and click **Run workflow**. A post should appear in your Discord channel within a minute or so.

From then on it runs automatically every day.

## Configuration

All settings are optional environment variables. In GitHub Actions, add them to the `env:` block of the run step.

| Variable | Default | What it does |
|----------|---------|--------------|
| `TIMEZONE` | `America/New_York` | Timezone for "today" and the displayed times ([list of names](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones)) |
| `LEAGUES` | all | Comma-separated leagues to include, e.g. `nba,nhl,epl` |
| `SHOW_LINES` | `1` | Set to `0` to remove the betting LINE column |
| `SHOW_TOTALS` | `0` | Set to `1` to add an over/under column |
| `BOOKMAKER` | `draftkings` | Preferred sportsbook key, e.g. `fanduel`. Falls back to any book that has a line |
| `SHOW_TV` | `1` | Set to `0` to remove the WATCH column |
| `SHOW_SCORES` | `0` | Set to `1` to add live/final scores |
| `SHORT_NAMES` | `1` | Set to `0` to use full names and times when they fit |
| `TABLE_WIDTH` | `40` | Max characters per table line. Lower it (e.g. `36`) if tables wrap on phones |
| `GRID` | `ascii` | `ascii` works everywhere; `box` uses prettier line characters that some phones draw unevenly |
| `BIG_HEADERS` | `1` | Large headings for the post and each league. Set to `0` for plain titles |
| `ANSI` | `0` | Set to `1` for colored tables. Shows on Discord desktop and web only; unreliable on mobile |

### League keys

| Key | League |
|-----|--------|
| `nba` | NBA |
| `nfl` | NFL |
| `mlb` | MLB |
| `nhl` | NHL |
| `wnba` | WNBA |
| `ncaaf` | College football |
| `ncaam` | Men's college basketball |
| `epl` | Premier League |
| `mls` | MLS |
| `ucl` | Champions League |

To add another league, add a line to the `LEAGUES` and `ESPN_PATHS` settings near the top of the script.

## Running it on your own computer

```bash
pip install requests

export ODDS_API_KEY="your-odds-api-key"
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

python daily_sports_oddsapi.py
```

To run it daily, schedule it with cron (Mac/Linux) or Task Scheduler (Windows). Python 3.9 or newer is required.

## API credit usage

The free Odds API plan includes a monthly credit allowance (500 credits at the time of writing; check their site for current limits). This script is designed to stay well within it:

| What | Cost |
|------|------|
| Game schedule and sports list | Free |
| Betting lines | 1 credit per in-season league with games that day |
| Over/under (`SHOW_TOTALS=1`) | +1 credit per league |
| Live scores (`SHOW_SCORES=1`) | +1 credit per league |

With the default settings on a typical day (4 to 6 leagues in season), that is roughly 120 to 180 credits a month. The script prints your remaining credits in the run log. To use fewer, limit `LEAGUES` or set `SHOW_LINES=0`.

## Good to know

- **Schedules run on UTC.** GitHub's `cron` doesn't adjust for daylight saving, so a post set for 9 AM Eastern in summer will arrive at 8 AM in winter.
- **Runs can start late.** GitHub may start scheduled runs 5 to 30 minutes after the scheduled time.
- **Scheduled workflows pause after 60 days of no repository activity.** If posts stop, open the Actions tab and re-enable the workflow.
- **Discord can't render real tables or change font size inside them.** The tables are monospace text, so the script keeps every line short enough to avoid wrapping.
- **Start times can differ slightly from other sites.** The Odds API gets times from sportsbooks, which sometimes list a game a few minutes before the broadcast start.
- **Some minor games may be missing.** The Odds API only lists games that sportsbooks offer lines on.
- **Local and regional channels vary by area.** The WATCH column shows the main national option, so check your own TV provider for local broadcasts.

## Troubleshooting

| Problem | What to check |
|---------|---------------|
| Nothing posted | Open the **Actions** tab and read the log for the failed run. Confirm both secrets are set |
| "No games found" message | Your selected leagues may be out of season or have no games today |
| Tables wrap on phones | Lower `TABLE_WIDTH` to `36` |
| Weird characters instead of colors | `ANSI` colors don't display on mobile. Remove `ANSI` or set it to `0` |
| Some games have no TV listed | ESPN may not have broadcast info yet, or the game couldn't be matched. This usually fills in later in the day |

## Disclaimer

Betting lines are shown for information only. They move often and vary by sportsbook, and nothing here is betting advice. This project is not affiliated with Discord, ESPN, The Odds API, or any sportsbook or league.
