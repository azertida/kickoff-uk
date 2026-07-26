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

Note: openfootball league times (e.g. Premier League) are bare local times with
no UTC offset -> interpreted in the source timezone (Europe/London) and stored
as UTC. World Cup / Euro times carry an explicit "UTC±H" offset.
"""

import json, re, sys, time, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

TIMEOUT = 25
UA = {"User-Agent": "kickoff-uk/1.0 (+github action)"}

def get_json(url):
    # Wikipedia rate-limits (HTTP 429) when many pages are fetched in a row.
    # Retry with growing back-off before giving up.
    delay = 1.0
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(delay)
                delay *= 2
                continue
            raise

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
        ft = (sc.get("et") or sc.get("ft")) if isinstance(sc, dict) else (sc if isinstance(sc, list) else None)
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

def tsdb_events(idl):
    # eventsnextleague.php is throttled on the free key (returns a single event),
    # so we read the whole season instead: eventsseason.php is a free method and
    # returns every fixture + result, like openfootball does for the leagues.
    for s in season_guesses():
        try:
            evs = get_json(f"{TSDB}/eventsseason.php?id={idl}&s={s}").get("events") or []
        except Exception:
            evs = []
        if evs:
            return evs
        time.sleep(0.4)
    return []

def collect_tsdb(name, idl, label):
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
            "competition": name,
            "date": e.get("dateEvent"), "start": start, "tbd": tbd,
            "home": h, "away": a, "score": score,
            "status": "finished" if score else "scheduled",
            "group": e.get("strRound") or None,
            "venue": e.get("strVenue") or None,
        })
    return out

# (display name, TheSportsDB league id, output sport label)
# Only the WSL is left here: the free key caps eventsseason.php at the first 15
# events of a season. That's tolerable for a season not started yet (you get its
# opening fixtures) but useless once a competition is under way — hence the
# European cups moved to Wikipedia below.
# To add a competition: find it on thesportsdb.com, the id is in the page URL
# (e.g. /league/4849-English-Womens-Super-League -> 4849).
TSDB_SOURCES = [
    ("Women's Super League", 4849, "Women's football"),
]

# ---------------------------------------------------------------- Wikipedia (EN)
# The free TheSportsDB key caps eventsseason.php at the first 15 events of a
# season, which is useless for a competition already under way. English Wikipedia
# carries every match in {{Football box}} templates, with kick-off times.
# Machinery ported from the (proven) rugby scraper in Coup d'envoi.
WIKI_EN = "https://en.wikipedia.org/w/api.php"

EN_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"], 1)}

def wiki_season():
    now = datetime.now(timezone.utc)
    y = now.year if now.month >= 6 else now.year - 1
    return f"{y}\u2013{str(y+1)[2:]}"        # en dash, e.g. "2026–27"

def wiki_wikitext(page):
    """Raw wikitext of a page, or None when the page doesn't exist yet."""
    url = (f"{WIKI_EN}?action=parse&page={urllib.parse.quote(page.replace(' ', '_'))}"
           f"&format=json&prop=wikitext&utf8=1&redirects=1")
    try:
        d = get_json(url)
    except Exception:
        return None
    if "parse" not in d:                      # missing page -> {"error": ...}
        return None
    return d["parse"]["wikitext"]["*"]

def _fb_extract(wikitext):
    """Every {{Football box ...}} / {{Footballbox ...}}, brace-matched."""
    out, low, i = [], wikitext.lower(), 0
    while True:
        hits = [h for h in (low.find("{{football box", i), low.find("{{footballbox", i)) if h != -1]
        if not hits:
            break
        idx = min(hits)
        depth, j = 0, idx
        while j < len(wikitext):
            if wikitext[j:j+2] == "{{":
                depth += 1; j += 2
            elif wikitext[j:j+2] == "}}":
                depth -= 1; j += 2
                if depth == 0:
                    out.append(wikitext[idx:j]); break
            else:
                j += 1
        i = j if j > idx else idx + 2
    return out

def _fb_fields(body):
    """Split template params on top-level pipes (nested {{ }} and [[ ]] safe)."""
    if body.startswith("{{"): body = body[2:]
    if body.endswith("}}"): body = body[:-2]
    parts, current, db, dk, i = [], [], 0, 0, 0
    while i < len(body):
        c, nxt = body[i], body[i+1] if i+1 < len(body) else ""
        if c == "{" and nxt == "{": db += 1; current += [c, nxt]; i += 2; continue
        if c == "}" and nxt == "}": db -= 1; current += [c, nxt]; i += 2; continue
        if c == "[" and nxt == "[": dk += 1; current += [c, nxt]; i += 2; continue
        if c == "]" and nxt == "]": dk -= 1; current += [c, nxt]; i += 2; continue
        if c == "|" and db == 0 and dk == 0:
            parts.append("".join(current)); current = []; i += 1; continue
        current.append(c); i += 1
    if current:
        parts.append("".join(current))
    fields = {}
    for p in parts[1:]:
        if "=" in p:
            k, _, v = p.partition("=")
            fields[k.strip().lower()] = v.strip()
    return fields

def _fb_date(text):
    if not text:
        return None
    # {{dts|2026|07|07}} / {{start date|2026|7|7}}
    m = re.search(r"\{\{\s*(?:dts|start date)[^}]*?\|\s*(\d{4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})", text, re.I)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    # "7 July 2026"
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", text)
    if m and m.group(2).lower() in EN_MONTHS:
        return f"{int(m.group(3)):04d}-{EN_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    # "July 7, 2026"
    m = re.search(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", text)
    if m and m.group(1).lower() in EN_MONTHS:
        return f"{int(m.group(3)):04d}-{EN_MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    return None

def _fb_time(text):
    if not text:
        return None
    m = re.search(r"(\d{1,2})[:.](\d{2})", text)
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None

def _fb_team(text):
    if not text:
        return None
    text = text.replace("'''", "")
    text = re.sub(r"\{\{\s*(?:flagicon|fb|fbicon|fbaicon)[^}]*\}\}", "", text, flags=re.I)
    m = re.search(r"\[\[[^\]|]+\|([^\]]+)\]\]", text)   # [[Arsenal F.C.|Arsenal]]
    if m:
        return m.group(1).strip()
    m = re.search(r"\[\[([^\]|]+)\]\]", text)            # [[Arsenal]]
    if m:
        return m.group(1).strip()
    text = re.sub(r"\{\{[^}]*\}\}", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip() or None

def _fb_score(text):
    if not text:
        return None
    text = text.replace("'''", "").strip()
    m = re.match(r"^\s*(\d+)\s*[-\u2013]\s*(\d+)", text)
    return f"{m.group(1)}\u2013{m.group(2)}" if m else None

def collect_wiki_football(name, page_base, label):
    """Merge every Football box across the pages of one competition."""
    s = wiki_season()
    if page_base == "UEFA Nations League":
        # The main article only carries standings grids (dates, no kick-off times).
        # The actual Football boxes live on the per-division articles.
        pages = [f"{s} UEFA Nations League {d}" for d in ("A", "B", "C", "D")]
        pages.append(f"{s} UEFA Nations League Finals")
    elif "Women's" in page_base:
        # Women's competitions use "qualifying rounds" (not just "qualifying").
        pages = [f"{s} {page_base} qualifying rounds",
                 f"{s} {page_base} league phase",
                 f"{s} {page_base} knockout phase",
                 f"{s} {page_base}"]
    else:
        pages = [f"{s} {page_base} qualifying",
                 f"{s} {page_base} league phase",
                 f"{s} {page_base} knockout phase",
                 f"{s} {page_base}"]
    out, seen, pages_ok = [], set(), []
    for page in pages:
        wt = wiki_wikitext(page)
        if not wt:
            continue
        pages_ok.append(page)
        for body in _fb_extract(wt):
            f = _fb_fields(body)
            date = _fb_date(f.get("date", ""))
            home, away = _fb_team(f.get("team1", "")), _fb_team(f.get("team2", ""))
            if not date or not home or not away:
                continue
            key = (date, home, away)
            if key in seen:
                continue
            seen.add(key)
            start = combine_date_time_cet(date, _fb_time(f.get("time", "")))
            score = _fb_score(f.get("score", ""))
            out.append({
                "id": slug(name, date, home, away),
                "sport": label, "competition": name,
                "date": date, "start": start,
                "tbd": start is None and score is None,
                "home": home, "away": away, "score": score,
                "status": "finished" if score else "scheduled",
                "group": f.get("round") or None,
                "venue": _fb_team(f.get("stadium", "")) or None,
            })
        time.sleep(0.3)
    return out, pages_ok

def combine_date_time_cet(date_iso, time_str):
    """UEFA lists kick-off times in CET/CEST -> store as UTC."""
    if not date_iso or not time_str:
        return None
    try:
        y, mo, d = (int(x) for x in date_iso.split("-"))
        hh, mm = (int(x) for x in time_str.split(":"))
        is_dst = 3 < mo < 10 or (mo == 3 and d >= 28) or (mo == 10 and d < 28)
        local = datetime(y, mo, d, hh, mm, tzinfo=timezone(timedelta(hours=2 if is_dst else 1)))
        return iso_z(local)
    except Exception:
        return None


# ================================================================ RUGBY
# Moteur porté de Coup d'envoi : lit les {{Match rugby}} sur Wikipédia FR.
# Les horaires y sont en heure de Paris (CET/CEST) -> stockés en UTC ;
# l'affichage en heure de Londres est géré par le frontend (TZ=Europe/London).
MOIS_FR = {
    "janvier": 1, "février": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8,
    "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12,
}

def combine_date_time_paris(date_iso, time_str):
    if not date_iso or not time_str:
        return None
    try:
        y, mo, d = (int(x) for x in date_iso.split("-"))
        hh, mm = (int(x) for x in time_str.split(":"))
        is_dst = 3 < mo < 10 or (mo == 3 and d >= 28) or (mo == 10 and d < 28)
        offset_hours = 2 if is_dst else 1
        local = datetime(y, mo, d, hh, mm, tzinfo=timezone(timedelta(hours=offset_hours)))
        return iso_z(local)
    except Exception:
        return None

WIKI_API = "https://fr.wikipedia.org/w/api.php"

def _wiki_parse_date(text):
    if not text:
        return None
    m = re.search(r"\{\{date\|([^}|]+)", text)
    if m:
        text = m.group(1)
    text = text.strip().lower()
    m = re.match(r"(\d{1,2})\s+(\w+)\s+(\d{4})", text)
    if m:
        day = int(m.group(1))
        month = MOIS_FR.get(m.group(2))
        year = int(m.group(3))
        if month:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return None

def _wiki_parse_heure(text):
    if not text:
        return None
    m = re.search(r"\{\{heure\|(\d{1,2})\|(\d{2})", text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    m = re.match(r"\s*(\d{1,2})[h:](\d{2})", text)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None

def _wiki_parse_team(text):
    if not text:
        return None
    text = text.replace("'''", "")
    m = re.search(r"\{\{([A-Za-zÀ-ÿ\s\-]+?)\s+rugby\s*[|}]", text)
    if m:
        return m.group(1).strip()
    m = re.search(r"\[\[(?:Équipe d['e]\s+)?([^\]|]+?)(?:\s+de rugby[^]]*)?(?:\|[^\]]*)?\]\]", text)
    if m:
        return m.group(1).strip()
    text = re.sub(r"\{\{[^}]+\}\}", "", text)
    text = re.sub(r"\[\[[^]]+\]\]", "", text)
    return text.strip() or None

def _wiki_parse_score(text):
    if not text:
        return None
    text = text.replace("'''", "").strip()
    m = re.match(r"^\s*(\d+)\s*[-\u2013]\s*(\d+)", text)
    if m:
        return f"{m.group(1)}\u2013{m.group(2)}"
    return None

def _wiki_parse_lieu(text):
    if not text:
        return None
    # Cas {{Lien|langue=en|Nom du stade}}
    m = re.search(r"\{\{Lien\|[^}]*\|([^}|]+)\}\}", text)
    if m:
        stadium = m.group(1).strip()
        rest = text[m.end():]
        m2 = re.search(r"\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]", rest)
        if m2:
            city = re.sub(r"\s*\([^)]+\)$", "", m2.group(1).strip())
            return f"{stadium}, {city}"
        return stadium
    # Cas standard [[Stade]], [[Ville]]
    m = re.search(r"\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]", text)
    if m:
        stadium = m.group(1).strip()
        rest = text[m.end():]
        m2 = re.search(r"\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]", rest)
        if m2:
            city = re.sub(r"\s*\([^)]+\)$", "", m2.group(1).strip())
            return f"{stadium}, {city}"
        return stadium
    return text.strip() or None

def _wiki_parse_match_template(body):
    if body.startswith("{{"):
        body = body[2:]
    if body.endswith("}}"):
        body = body[:-2]
    parts, current = [], []
    depth_braces = depth_brackets = 0
    i = 0
    while i < len(body):
        c, nxt = body[i], body[i+1] if i+1 < len(body) else ""
        if c == "{" and nxt == "{":
            depth_braces += 1; current.append(c); current.append(nxt); i += 2; continue
        if c == "}" and nxt == "}":
            depth_braces -= 1; current.append(c); current.append(nxt); i += 2; continue
        if c == "[" and nxt == "[":
            depth_brackets += 1; current.append(c); current.append(nxt); i += 2; continue
        if c == "]" and nxt == "]":
            depth_brackets -= 1; current.append(c); current.append(nxt); i += 2; continue
        if c == "|" and depth_braces == 0 and depth_brackets == 0:
            parts.append("".join(current)); current = []; i += 1; continue
        current.append(c); i += 1
    if current:
        parts.append("".join(current))
    fields = {}
    for part in parts[1:]:
        if "=" in part:
            k, _, v = part.partition("=")
            fields[k.strip().lower()] = v.strip()
    return {
        "date": _wiki_parse_date(fields.get("date", "")),
        "time_local": _wiki_parse_heure(fields.get("heure", "")),
        "home": _wiki_parse_team(fields.get("équipe1", "")),
        "away": _wiki_parse_team(fields.get("équipe2", "")),
        "score": _wiki_parse_score(fields.get("score", "")),
        "venue": _wiki_parse_lieu(fields.get("lieu", "")),
    }

def _wiki_extract_templates(wikitext):
    out = []
    i = 0
    while i < len(wikitext):
        idx = wikitext.find("{{Match rugby", i)
        if idx == -1:
            break
        depth, j = 0, idx
        while j < len(wikitext):
            if wikitext[j:j+2] == "{{":
                depth += 1; j += 2
            elif wikitext[j:j+2] == "}}":
                depth -= 1; j += 2
                if depth == 0:
                    out.append(wikitext[idx:j]); break
            else:
                j += 1
        i = j
    return out

def _wiki_find_section(wikitext, title_pattern):
    pattern = rf"==+\s*{title_pattern}\s*==+(.+?)(?===+\s*\S)"
    m = re.search(pattern, wikitext, re.DOTALL)
    return m.group(1) if m else ""

# Six Nations
JOURNEES_6N = [
    ("Première journée", "J1"),
    ("Deuxième journée", "J2"),
    ("Troisième journée", "J3"),
    ("Quatrième journée", "J4"),
    ("Cinquième journée", "J5"),
]

def collect_six_nations(year, competition="Six Nations", page_base="Tournoi_des_Six_Nations", id_prefix="six-nations"):
    page = f"{page_base}_{year}"
    url = f"{WIKI_API}?action=parse&page={page}&format=json&prop=wikitext&utf8=1"
    data = get_json(url)
    wikitext = data["parse"]["wikitext"]["*"]
    out = []
    for section_title, phase in JOURNEES_6N:
        section = _wiki_find_section(wikitext, re.escape(section_title))
        if not section:
            continue
        for tpl in _wiki_extract_templates(section):
            m = _wiki_parse_match_template(tpl)
            if not m.get("home") or not m.get("away"):
                continue
            start_utc = combine_date_time_paris(m["date"], m["time_local"]) if m["time_local"] else None
            out.append({
                "id": slug(id_prefix, year, m["date"], m["home"], m["away"]),
                "sport": "Rugby", "competition": competition,
                "date": m["date"], "start": start_utc,
                "tbd": start_utc is None and m["score"] is None,
                "home": m["home"], "away": m["away"], "score": m["score"],
                "status": "finished" if m["score"] else "scheduled",
                "group": phase, "venue": m["venue"],
            })
    return out

def collect_rugby_wholepage(year, competition, page_base, id_prefix, tz="paris"):
    # Extraction page entière (pas de sections « journée ») — pour les compétitions
    # à poules comme la Coupe du monde. tz : fuseau des horaires dans le wikicode.
    #   "paris" -> heure de Paris (CET/CEST). À VÉRIFIER pour un tournoi hors Europe :
    #   la Coupe du monde 2027 est en Australie, si les heures sont locales il faudra
    #   passer tz="sydney" (voir combine_date_time_sydney ci-dessous).
    page = f"{page_base}_{year}"
    url = f"{WIKI_API}?action=parse&page={urllib.parse.quote(page)}&format=json&prop=wikitext&utf8=1"
    data = get_json(url)
    if "parse" not in data:
        return []
    wikitext = data["parse"]["wikitext"]["*"]
    conv = combine_date_time_sydney if tz == "sydney" else combine_date_time_paris
    out, seen = [], set()
    for tpl in _wiki_extract_templates(wikitext):
        m = _wiki_parse_match_template(tpl)
        if not m.get("home") or not m.get("away"):
            continue
        key = (m["date"], m["home"], m["away"])
        if key in seen:
            continue
        seen.add(key)
        start_utc = conv(m["date"], m["time_local"]) if m["time_local"] else None
        out.append({
            "id": slug(id_prefix, year, m["date"], m["home"], m["away"]),
            "sport": "Rugby", "competition": competition,
            "date": m["date"], "start": start_utc,
            "tbd": start_utc is None and m["score"] is None,
            "home": m["home"], "away": m["away"], "score": m["score"],
            "status": "finished" if m["score"] else "scheduled",
            "group": None, "venue": m["venue"],
        })
    return out

def combine_date_time_sydney(date_iso, time_str):
    # Sydney/Melbourne en oct.-nov. = heure d'été australienne (AEDT, UTC+11).
    if not date_iso or not time_str:
        return None
    try:
        y, mo, d = (int(x) for x in date_iso.split("-"))
        hh, mm = (int(x) for x in time_str.split(":"))
        local = datetime(y, mo, d, hh, mm, tzinfo=timezone(timedelta(hours=11)))
        return iso_z(local)
    except Exception:
        return None


def rugby_edition_years():
    y = datetime.now(timezone.utc).year
    return [y + 1, y, y - 1]


# (display name, Wikipedia page base, output sport label)
WIKI_SOURCES = [
    ("UEFA Champions League",   "UEFA Champions League",         "Football"),
    ("UEFA Europa League",      "UEFA Europa League",            "Football"),
    ("UEFA Nations League",     "UEFA Nations League",           "Football"),
    ("Women's Champions League","UEFA Women's Champions League", "Women's football"),
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

    for name, page_base, label in WIKI_SOURCES:
        try:
            rows, pages_ok = collect_wiki_football(name, page_base, label)
            matches += rows
            sources.append({"name": name, "sport": label, "ok": True,
                            "count": len(rows), "season": wiki_season()})
            print(f"[ok] {name}: {len(rows)} (pages: {', '.join(pages_ok) or 'none'})")
        except Exception as e:
            sources.append({"name": name, "sport": label, "ok": False, "error": str(e)})
            print(f"[!!] {name}: {e}", file=sys.stderr)

    for name, idl, label in TSDB_SOURCES:
        try:
            rows = collect_tsdb(name, idl, label)
            matches += rows
            sources.append({"name": name, "sport": label, "ok": True, "count": len(rows)})
            print(f"[ok] {name}: {len(rows)}")
        except Exception as e:
            sources.append({"name": name, "sport": label, "ok": False, "error": str(e)})
            print(f"[!!] {name}: {e}", file=sys.stderr)

    # ---- Rugby (Wikipédia FR, {{Match rugby}}) ----
    # Pause : le foot a déjà enchaîné une douzaine de pages Wikipédia juste avant,
    # on laisse le quota respirer pour limiter le 429.
    time.sleep(2)
    # (display name, page base FR, id prefix)
    RUGBY_6N = [("Six Nations", "Tournoi_des_Six_Nations", "six-nations")]
    for name, page_base, id_prefix in RUGBY_6N:
        try:
            rows, used, last_err = [], None, None
            for yr in rugby_edition_years():
                try:
                    r = collect_six_nations(yr, competition=name, page_base=page_base, id_prefix=id_prefix)
                except Exception as e:
                    r = []
                    last_err = f"{yr}: {type(e).__name__}: {e}"
                if r:
                    rows, used = r, yr
                    break
            matches += rows
            entry = {"name": name, "sport": "Rugby", "ok": True, "count": len(rows), "year": used}
            if not rows and last_err:
                entry["note"] = last_err          # visible dans matches.json pour diagnostic
            sources.append(entry)
            print(f"[ok] {name} ({used}): {len(rows)}  {('- ' + last_err) if (not rows and last_err) else ''}")
        except Exception as e:
            sources.append({"name": name, "sport": "Rugby", "ok": False, "error": str(e)})
            print(f"[!!] {name}: {e}", file=sys.stderr)

    # Coupe du monde de rugby 2027 (Australie). Page à poules -> extraction entière.
    # tz="paris" par défaut : À VÉRIFIER au 1er run contre un match connu
    # (Australie-Hong Kong, 1 oct. 2027, Perth). Si décalé, passer tz="sydney".
    try:
        rwc = collect_rugby_wholepage(
            2027, competition="Rugby World Cup",
            page_base="Coupe_du_monde_masculine_de_rugby_à_XV",
            id_prefix="rwc", tz="paris")
        matches += rwc
        entry = {"name": "Rugby World Cup", "sport": "Rugby", "ok": True, "count": len(rwc), "year": 2027}
        # échantillon pour la vérif horaire
        if rwc:
            s = rwc[0]
            entry["sample"] = f"{s['home']} v {s['away']} {s['date']} start={s['start']}"
        sources.append(entry)
        print(f"[ok] Rugby World Cup (2027): {len(rwc)}" + (f"  ex: {entry.get('sample')}" if rwc else ""))
    except Exception as e:
        sources.append({"name": "Rugby World Cup", "sport": "Rugby", "ok": False, "error": str(e)})
        print(f"[!!] Rugby World Cup: {e}", file=sys.stderr)

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
