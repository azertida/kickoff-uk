#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kick-off (UK) — data collector.

English fork of "Coup d'envoi". Aggregates fixtures into a single matches.json
served by the PWA (same origin -> no CORS).

Sources:
  - Football (men)   : openfootball (public domain JSON, no key)
                       -> Premier League + World Cup 2026 + Euro 2028
  - Women's football : TheSportsDB V1 (free key "123", server-side)
                       -> Women's Super League, UEFA Women's Champions League,
                          FIFA Women's World Cup 2027 (best-effort)
  - Rugby            : TheSportsDB -> Six Nations

Note: openfootball league times (e.g. Premier League) are bare local times with
no UTC offset -> interpreted in the source timezone (Europe/London) and stored
as UTC. World Cup / Euro times carry an explicit "UTC±H" offset.
"""

import json, re, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

TIMEOUT = 25
UA = {"User-Agent": "kickoff-uk/1.0 (+github action)"}

def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))

# ---------------------------------------------------------------- time helpers
def iso_z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def parse_openfootball_time(date, t, tz=None):
    """ '13:00 UTC-6' -> explicit offset. '15:00' + tz -> local in tz. None -> (None, True). """
    if not t:
        return None, True
    y, mo, d = (int(x) for x in date.split("-"))
    m = re.match(r"\s*(\d{1,2}):(\d{2})\s*UTC([+-]\d{1,2})", t)
    if m:
        hh, mm, off = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return iso_z(datetime(y, mo, d, hh, mm, tzinfo=timezone(timedelta(hours=off)))), False
    m2 = re.match(r"\s*(\d{1,2}):(\d{2})", t)
    if m2:
        hh, mm = int(m2.group(1)), int(m2.group(2))
        tzinfo = ZoneInfo(tz) if tz else timezone.utc
        return iso_z(datetime(y, mo, d, hh, mm, tzinfo=tzinfo)), False
    return None, True

def parse_tsdb_time(ev):
    ts = ev.get("strTimestamp")
    if ts:
        ts = ts.replace(" ", "T").replace("Z", "").split("+")[0]
        try:
            return iso_z(datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)), False
        except ValueError:
            pass
    d, t = ev.get("dateEvent"), ev.get("strTime")
    if d and t and t != "00:00:00":
        try:
            return iso_z(datetime.fromisoformat(f"{d}T{t}").replace(tzinfo=timezone.utc)), False
        except ValueError:
            pass
    return None, True

def slug(*parts):
    s = "-".join(str(p) for p in parts if p)
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:80]

# ---------------------------------------------------------------- openfootball
def parse_openfootball(data, name, tz=None):
    out = []
    for m in data.get("matches", []):
        date = m.get("date")
        if not date:
            continue
        start, tbd = parse_openfootball_time(date, m.get("time"), tz)
        sc = m.get("score")
        ft = sc.get("ft") if isinstance(sc, dict) else (sc if isinstance(sc, list) else None)
        score = f"{ft[0]}\u2013{ft[1]}" if isinstance(ft, list) and len(ft) == 2 else None
        h, a = m.get("team1"), m.get("team2")
        out.append({
            "id": slug(name, date, h, a),
            "sport": "Football",
            "competition": name,
            "date": date, "start": start, "tbd": tbd,
            "home": h, "away": a, "score": score,
            "status": "finished" if score else "scheduled",
            "group": m.get("group") or m.get("round"),
            "venue": m.get("ground"),
        })
    return out

# Tournois à fichier fixe (offset UTC explicite dans les heures)
OPENFOOTBALL_FIXED = [
    ("World Cup 2026", "https://raw.githubusercontent.com/openfootball/worldcup.json/master/2026/worldcup.json"),
    ("Euro 2028",      "https://raw.githubusercontent.com/openfootball/euro.json/master/2028/euro.json"),
]

# Ligues club (heures locales -> timezone) : on prend la saison la plus récente disponible
def league_season_candidates():
    now = datetime.now(timezone.utc)
    y, mth = now.year, now.month
    start = y if mth >= 7 else y - 1          # année de début de saison en cours
    yrs = [start + 1, start, start - 1]        # plus récente d'abord
    return [f"{a}-{str(a+1)[2:]}" for a in yrs]

def collect_openfootball_league(name, code, tz):
    for s in league_season_candidates():
        url = f"https://raw.githubusercontent.com/openfootball/football.json/master/{s}/{code}.json"
        try:
            data = get_json(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise
        return parse_openfootball(data, name, tz), s
    return [], None

OPENFOOTBALL_LEAGUES = [
    ("Premier League", "en.1", "Europe/London"),
]

# ---------------------------------------------------------------- TheSportsDB
TSDB = "https://www.thesportsdb.com/api/v1/json/123"

def season_guesses():
    now = datetime.now(timezone.utc)
    y, mth = now.year, now.month
    s = y if mth >= 7 else y - 1
    return [f"{s}-{s+1}", f"{y}", f"{y}-{y+1}"]

_leagues = None
def tsdb_all_leagues():
    global _leagues
    if _leagues is None:
        _leagues = get_json(f"{TSDB}/all_leagues.php").get("leagues") or []
    return _leagues

def resolve_league(substrs, sport):
    for l in tsdb_all_leagues():
        nm = (l.get("strLeague") or "")
        if l.get("strSport") == sport and any(s.lower() in nm.lower() for s in substrs):
            return l.get("idLeague"), nm
    return None, None

def tsdb_events(idl):
    try:
        evs = get_json(f"{TSDB}/eventsnextleague.php?id={idl}").get("events") or []
        if evs:
            return evs
    except Exception:
        pass
    cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
    for s in season_guesses():
        try:
            evs = get_json(f"{TSDB}/eventsseason.php?id={idl}&s={s}").get("events") or []
        except Exception:
            evs = []
        up = []
        for e in evs:
            iso, _ = parse_tsdb_time(e)
            if iso and datetime.fromisoformat(iso.replace("Z", "+00:00")) > cutoff:
                up.append(e)
        if up:
            return up
        time.sleep(0.4)
    return []

def collect_tsdb(name, substrs, match_sport, label):
    idl, real = resolve_league(substrs, match_sport)
    if not idl:
        raise RuntimeError(f"league not found ({'/'.join(substrs)})")
    out = []
    for e in tsdb_events(idl):
        start, tbd = parse_tsdb_time(e)
        h = e.get("strHomeTeam") or e.get("strEvent")
        a = e.get("strAwayTeam")
        hs, as_ = e.get("intHomeScore"), e.get("intAwayScore")
        score = f"{hs}\u2013{as_}" if hs not in (None, "") and as_ not in (None, "") else None
        out.append({
            "id": slug(name, e.get("idEvent")),
            "sport": label,
            "competition": real or name,
            "date": e.get("dateEvent"), "start": start, "tbd": tbd,
            "home": h, "away": a, "score": score,
            "status": "finished" if score else "scheduled",
            "group": e.get("strRound") or None,
            "venue": e.get("strVenue") or None,
        })
        time.sleep(0.4)
    return out

# (name, substrings to resolve, TheSportsDB sport, output sport label)
TSDB_SOURCES = [
    ("UEFA Champions League",    ["UEFA Champions League"],                              "Soccer", "Football"),
    ("UEFA Europa League",       ["UEFA Europa League"],                                 "Soccer", "Football"),
    ("Women's Super League",     ["Women's Super League", "Womens Super League", "WSL"], "Soccer", "Women's football"),
    ("Women's Champions League", ["Women's Champions League"],                           "Soccer", "Women's football"),
    ("Women's Euro",             ["Women's Euro", "European Women's Championship"],       "Soccer", "Women's football"),
    ("FIFA Women's World Cup",   ["Women's World Cup", "Womens World Cup"],              "Soccer", "Women's football"),
]

# ---------------------------------------------------------------- main
def main():
    matches, sources = [], []

    for name, url in OPENFOOTBALL_FIXED:
        try:
            rows = parse_openfootball(get_json(url), name)
            matches += rows
            sources.append({"name": name, "sport": "Football", "ok": True, "count": len(rows)})
            print(f"[ok] {name}: {len(rows)}")
        except Exception as e:
            sources.append({"name": name, "sport": "Football", "ok": False, "error": str(e)})
            print(f"[!!] {name}: {e}", file=sys.stderr)

    for name, code, tz in OPENFOOTBALL_LEAGUES:
        try:
            rows, season = collect_openfootball_league(name, code, tz)
            matches += rows
            sources.append({"name": name, "sport": "Football", "ok": True, "count": len(rows), "season": season})
            print(f"[ok] {name} ({season}): {len(rows)}")
        except Exception as e:
            sources.append({"name": name, "sport": "Football", "ok": False, "error": str(e)})
            print(f"[!!] {name}: {e}", file=sys.stderr)

    for name, substrs, msport, label in TSDB_SOURCES:
        try:
            rows = collect_tsdb(name, substrs, msport, label)
            matches += rows
            sources.append({"name": name, "sport": label, "ok": True, "count": len(rows)})
            print(f"[ok] {name}: {len(rows)}")
        except Exception as e:
            sources.append({"name": name, "sport": label, "ok": False, "error": str(e)})
            print(f"[!!] {name}: {e}", file=sys.stderr)

    seen, uniq = set(), []
    for m in matches:
        if m["id"] in seen:
            continue
        seen.add(m["id"]); uniq.append(m)
    uniq.sort(key=lambda m: (m.get("start") or (m.get("date", "9999") + "T99")))

    out = {"generated": iso_z(datetime.now(timezone.utc)), "sources": sources,
           "count": len(uniq), "matches": uniq}
    with open("matches.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"\nTotal: {len(uniq)} matchs -> matches.json")

if __name__ == "__main__":
    main()
