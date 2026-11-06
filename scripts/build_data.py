#!/usr/bin/env python3
"""
Builds the bundled game data for the Champions Team Lab iPhone app.

Sources (downloaded, parsed as plain data, never executed):
  - Pokemon Showdown: species, moves, type chart, items, and the `champions` mod
    (legal Pokemon/items, Champions move changes, learnsets).
  - Smogon ladder stats (smogon.com/stats): usage, moves, items, abilities,
    Stat Point spreads, teammates and checks & counters for the in-game
    Singles (BSS) and Doubles (VGC) formats.
  - Pikalytics: tournament usage, win rates and top tournament teams (Doubles).

Usage:  python3 -I scripts/build_data.py            (re-downloads everything)
        python3 -I scripts/build_data.py --offline  (reuses scripts/.cache)
        python3 -I scripts/build_data.py --out data/gamedata.json --sprites data/sprites
                                                    (used by the GitHub data job)
Output: PokemonChampions/Data/gamedata.json and PokemonChampions/Sprites/*.png
"""
import json, math, os, re, sys, time, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "scripts", ".cache")
def _arg(name, default):
    if name in sys.argv:
        i = sys.argv.index(name)
        if i + 1 < len(sys.argv):
            return os.path.abspath(sys.argv[i + 1])
    return default


# --out and --sprites let the GitHub data job write somewhere other than the app project
OUT_JSON = _arg("--out", os.path.join(ROOT, "PokemonChampions", "Data", "gamedata.json"))
SPRITES = _arg("--sprites", os.path.join(ROOT, "PokemonChampions", "Sprites"))
OFFLINE = "--offline" in sys.argv
DATA_SCHEMA = 1  # bump when the app can no longer read the JSON shape

PS_RAW = "https://raw.githubusercontent.com/smogon/pokemon-showdown/master"
PS_PLAY = "https://play.pokemonshowdown.com"
SMOGON = "https://www.smogon.com/stats"
PIKA = "https://www.pikalytics.com/ai/pokedex"
UA = "Mozilla/5.0 (ChampionsTeamLab data builder)"

TYPES = ["Normal", "Fire", "Water", "Electric", "Grass", "Ice", "Fighting", "Poison", "Ground",
         "Flying", "Psychic", "Bug", "Rock", "Ghost", "Dragon", "Dark", "Steel", "Fairy"]
NATURE_TABLE = None  # natures live in the app


def to_id(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def fetch(url, name, binary=False, required=True):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, name)
    if not (OFFLINE and os.path.exists(path)):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            with open(path, "wb") as f:
                f.write(data)
        except urllib.error.HTTPError as e:
            if required:
                raise SystemExit(f"Download failed {url}: {e}")
            return None
    with open(path, "rb") as f:
        data = f.read()
    return data if binary else data.decode("utf-8")


# ---------------------------------------------------------------- TS parsing
def ts_entries(text):
    """Top-level `\\tid: {...\\n\\t},` entries of a Showdown data table."""
    return dict(re.findall(r"\n\t(\w+): \{(.*?)\n\t\},", text, re.S))


def ts_scalar(body, field):
    """A field at the first nesting level (two tabs) of an entry body."""
    m = re.search(r"\n\t\t" + field + r": (.*?),\n", "\n" + body + "\n")
    if not m:
        return None
    v = m.group(1).strip()
    if v == "null":
        return "null"
    if v in ("true", "false"):
        return v == "true"
    if v.startswith('"') or v.startswith("'"):
        return v[1:-1]
    try:
        return int(v)
    except ValueError:
        return v


# ------------------------------------------------------------- Showdown data
def load_showdown():
    pokedex = json.loads(fetch(f"{PS_PLAY}/data/pokedex.json", "pokedex.json"))
    moves = json.loads(fetch(f"{PS_PLAY}/data/moves.json", "moves.json"))
    typechart_js = fetch(f"{PS_PLAY}/data/typechart.js", "typechart.js")
    items_main = fetch(f"{PS_RAW}/data/items.ts", "items.ts")
    items_text = fetch(f"{PS_RAW}/data/text/items.ts", "text_items.ts")
    moves_text = fetch(f"{PS_RAW}/data/text/moves.ts", "text_moves.ts")
    abil_text = fetch(f"{PS_RAW}/data/text/abilities.ts", "text_abilities.ts")
    c_formats = fetch(f"{PS_RAW}/data/mods/champions/formats-data.ts", "champ_formats-data.ts")
    c_items = fetch(f"{PS_RAW}/data/mods/champions/items.ts", "champ_items.ts")
    c_moves = fetch(f"{PS_RAW}/data/mods/champions/moves.ts", "champ_moves.ts")
    c_learn = fetch(f"{PS_RAW}/data/mods/champions/learnsets.ts", "champ_learnsets.ts")

    # Type chart: damageTaken codes 0 neutral, 1 weak, 2 resist, 3 immune
    chart = {}
    for dtype, body in re.findall(r"(\w+):\{damageTaken:\{(.*?)\}", typechart_js):
        dt = dtype.capitalize()
        for atk, code in re.findall(r"([A-Z]\w+):(\d)", body):
            if atk in TYPES:
                chart.setdefault(atk, {})[dt] = {0: 1, 1: 2, 2: 0.5, 3: 0}[int(code)]
    matrix = [[chart[a][d] for d in TYPES] for a in TYPES]

    # Legal species
    legal = set()
    for sid, body in ts_entries(c_formats).items():
        if ts_scalar(body, "isNonstandard") is None:
            legal.add(sid)

    # Items: legal = champions override null, or not overridden and standard in main
    c_item_entries = ts_entries(c_items)
    main_items = ts_entries(items_main)
    texts = ts_entries(items_text)
    items = {}
    for iid, body in main_items.items():
        status = ts_scalar(body, "isNonstandard")
        if iid in c_item_entries:
            status = ts_scalar(c_item_entries[iid], "isNonstandard")
        if status not in (None, "null"):
            continue
        name = ts_scalar(body, "name")
        desc = ts_scalar(texts.get(iid, ""), "shortDesc") or ""
        mega = re.search(r'megaStone: \{ "([^"]+)": "([^"]+)" \}', body)
        items[iid] = {"name": name, "desc": desc}
        if mega:
            items[iid]["mega"] = to_id(mega.group(2))

    # Moves with Champions overrides
    c_move_entries = ts_entries(c_moves)
    move_texts = ts_entries(moves_text)
    for mid, body in c_move_entries.items():
        if mid not in moves:
            continue
        for field in ("basePower", "accuracy", "isNonstandard", "type", "category", "priority", "pp"):
            v = ts_scalar(body, field)
            if v is not None:
                moves[mid][field] = None if v == "null" else v

    # Learnsets
    learnsets = {}
    for sid, body in ts_entries(c_learn).items():
        learnsets[sid] = re.findall(r"\n\t\t\t(\w+): \[", body)

    abil_texts = {k: ts_scalar(v, "shortDesc") or "" for k, v in ts_entries(abil_text).items()}
    return pokedex, moves, move_texts, matrix, legal, items, learnsets, abil_texts


# ------------------------------------------------------------- Smogon stats
def latest_smogon():
    index = fetch(f"{SMOGON}/", "smogon_index.html")
    months = sorted(set(re.findall(r'href="(\d{4}-\d{2})/"', index)), reverse=True)
    for m in months[:4]:
        listing = fetch(f"{SMOGON}/{m}/chaos/", f"smogon_{m}_chaos.html", required=False)
        if not listing:
            continue
        files = set(re.findall(r'href="([^"]+\.json)"', listing))

        def pick(prefix):
            cands = sorted({re.sub(r"-\d+\.json$", "", f) for f in files
                            if re.match(prefix + r"reg[a-z]{2}-\d+\.json$", f)})
            return cands[-1] if cands else None

        singles, doubles = pick("gen9championsbss"), pick("gen9championsvgc2026")
        if singles and doubles:
            return m, singles, doubles
    raise SystemExit("No Champions stats found on smogon.com/stats")


def load_chaos(month, fmt, cutoff):
    raw = fetch(f"{SMOGON}/{month}/chaos/{fmt}-{cutoff}.json", f"{fmt}-{cutoff}-{month}.json")
    return json.loads(raw)


def summarize_format(chaos_sets, chaos_cc, pokedex_ids, min_n):
    """Per-Pokemon usage summary. chaos_sets: high-rated file, chaos_cc: wider file for counters."""
    out = {}
    data = chaos_sets["data"]
    ranked = sorted(data.items(), key=lambda kv: -kv[1]["usage"])
    for rank, (name, v) in enumerate(ranked, 1):
        pid = to_id(name)
        if pid not in pokedex_ids or v["usage"] < 0.0005:
            continue
        total = sum(v["Abilities"].values()) or 1

        def top(d, k, conv=lambda x: x):
            rows = sorted(d.items(), key=lambda kv: -kv[1])[:k]
            return [[conv(a), round(b / total * 100, 1)] for a, b in rows if b / total >= 0.01]

        spreads = []
        for s, w in sorted(v["Spreads"].items(), key=lambda kv: -kv[1])[:4]:
            nature, sps = s.split(":")
            spreads.append([nature, [int(x) for x in sps.split("/")], round(w / total * 100, 1)])
        mates = [[to_id(a), p] for a, p in top(v["Teammates"], 12)]
        out[pid] = {
            "usage": round(v["usage"] * 100, 2),
            "rank": rank,
            "moves": [m for m in top(v["Moves"], 10) if m[0]],
            "items": [i for i in top(v["Items"], 6) if i[0] and i[0] != "nothing"],
            "abilities": top(v["Abilities"], 3),
            "spreads": spreads,
            "mates": mates,
        }
    # Checks and counters: Smogon score = p - 4*sd ; from the wider sample
    for name, v in chaos_cc["data"].items():
        pid = to_id(name)
        if pid not in out:
            continue
        rows = []
        for cname, c in v.get("Checks and Counters", {}).items():
            if isinstance(c, dict):
                n, p, d = c["n"], c["p"], c["d"]
            else:
                n, p, d = c
            if n >= min_n and to_id(cname) in pokedex_ids:
                rows.append([to_id(cname), round(p - 4 * d, 3), int(n)])
        rows.sort(key=lambda r: -r[1])
        out[pid]["counters"] = rows[:25]
    return out


# ------------------------------------------------------------- Pikalytics
def load_pikalytics(fmt):
    text = fetch(f"{PIKA}/{fmt}", f"pika_{fmt}.md", required=False)
    if not text or "Format Not Found" in text:
        return {}, [], None
    usage = {}
    for name, use, wr in re.findall(r"\| \d+ \| \*\*(.+?)\*\* \| ([\d.]+)% \| ([\d.]+|N/A)%? \|", text):
        usage[to_id(name)] = {"tourUsage": float(use), "winrate": None if wr == "N/A" else float(wr)}
    teams = []
    sec = text.split("## Recent Top Teams", 1)[-1].split("##", 1)[0]
    for row in re.findall(r"^\| \d+ \| (.+?) \| (.+?) \| (.+?) \| (.+?) \|$", sec, re.M):
        author, record, event, mons = row
        teams.append({"author": author, "record": record, "event": event.replace("\\|", "|"),
                      "mons": [to_id(m.strip()) for m in mons.split(",")]})
    date = re.search(r"\*\*Data Date\*\*: ([\d-]+)", text)
    return usage, teams, date.group(1) if date else None


# ------------------------------------------------------------- Build
def main():
    print("Loading Showdown data…")
    pokedex, moves, move_texts, matrix, legal, items, learnsets, abil_texts = load_showdown()

    species = {}
    for sid in sorted(legal):
        p = pokedex.get(sid)
        if not p or "Mythical" in p.get("tags", []) or "Restricted Legendary" in p.get("tags", []):
            continue
        if p.get("battleOnly") and "Mega" not in p.get("forme", ""):
            continue
        species[sid] = p
    print(f"  {len(species)} legal Pokemon, {len(items)} legal items")

    month, fmt_s, fmt_d = latest_smogon()
    print(f"Smogon {month}: singles={fmt_s} doubles={fmt_d}")
    singles = summarize_format(load_chaos(month, fmt_s, 1760), load_chaos(month, fmt_s, 1500), species, 25)
    doubles = summarize_format(load_chaos(month, fmt_d, 1760), load_chaos(month, fmt_d, 1500), species, 150)
    battles_s = load_chaos(month, fmt_s, 1500)["info"]["number of battles"]
    battles_d = load_chaos(month, fmt_d, 1500)["info"]["number of battles"]

    print("Pikalytics…")
    pk_usage, pk_teams, pk_date = load_pikalytics(fmt_d)
    for pid, d in pk_usage.items():
        if pid in doubles:
            doubles[pid].update(d)

    # Build Pokemon list
    used_moves = set()
    mons = []
    for sid, p in species.items():
        base_id = to_id(p.get("baseSpecies", p["name"]))
        learn = learnsets.get(sid) or learnsets.get(to_id(p.get("changesFrom", ""))) or learnsets.get(base_id) or []
        learn = [m for m in learn if m in moves and moves[m].get("isNonstandard") in (None,)]
        used_moves.update(learn)
        stone = to_id(p["requiredItem"]) if p.get("requiredItem") and "Mega" in p.get("forme", "") else None
        bs = p["baseStats"]
        entry = {
            "id": sid, "name": p["name"], "base": base_id, "num": p["num"],
            "types": p["types"],
            "stats": [bs["hp"], bs["atk"], bs["def"], bs["spa"], bs["spd"], bs["spe"]],
            "abilities": list(dict.fromkeys(p["abilities"].values())),
            "weight": p.get("weightkg", 50),
            "learn": sorted(learn),
        }
        if stone:
            entry["stone"] = stone
        if sid in singles:
            entry["singles"] = singles[sid]
        if sid in doubles:
            entry["doubles"] = doubles[sid]
        for fmt in (singles, doubles):
            for m in fmt.get(sid, {}).get("moves", []):
                used_moves.add(m[0])
        mons.append(entry)

    move_out = {}
    for mid in sorted(used_moves):
        m = moves.get(mid)
        if not m:
            continue
        mh = m.get("multihit")
        hits = (3 if mh == [2, 5] else (sum(mh) / 2 if isinstance(mh, list) else mh)) if mh else 1
        move_out[mid] = {
            "name": m["name"], "type": m["type"], "cat": m["category"][0],  # P / S / S(tatus) -> fixed below
            "bp": m.get("basePower", 0) or 0,
            "acc": 101 if m.get("accuracy") is True else m.get("accuracy", 100),
            "pri": m.get("priority", 0),
            "spread": m.get("target") in ("allAdjacent", "allAdjacentFoes"),
            "contact": bool(m.get("flags", {}).get("contact")),
            "hits": hits,
            "desc": ts_scalar(move_texts.get(mid, ""), "shortDesc") or "",
        }
        move_out[mid]["cat"] = {"Physical": "P", "Special": "S", "Status": "X"}[m["category"]]

    # Attacking-type pressure per format: usage-weighted share of damaging move types
    def type_pressure(fmt):
        w = {t: 0.0 for t in TYPES}
        for pid, d in fmt.items():
            for mid, pct in d["moves"]:
                mv = move_out.get(mid)
                if mv and mid != "fakeout" and mv["cat"] != "X" and mv["bp"] > 0 and mv["type"] in w:
                    w[mv["type"]] += d["usage"] * pct / 100
        tot = sum(w.values()) or 1
        return {t: round(v / tot * 18, 3) for t, v in w.items()}  # 1.0 = average type

    used_items = set()
    for fmt in (singles, doubles):
        for d in fmt.values():
            used_items.update(i for i, _ in d["items"])
    item_out = {k: v for k, v in items.items()}
    abil_names = sorted({a for m in mons for a in m["abilities"]})
    abil_out = {a: abil_texts.get(to_id(a), "") for a in abil_names}

    data = {
        "meta": {
            "built": time.strftime("%Y-%m-%d %H:%M", time.gmtime()),
            "smogonMonth": month, "singlesFormat": fmt_s, "doublesFormat": fmt_d,
            "singlesBattles": battles_s, "doublesBattles": battles_d,
            "pikalyticsDate": pk_date,
            "sources": [
                "Smogon ladder statistics (smogon.com/stats) – usage, sets, Stat Point spreads, teammates, checks & counters",
                "Pikalytics (pikalytics.com) – tournament usage, win rates and top teams (Doubles)",
                "Pokémon Showdown data (Champions mod) – legal Pokémon, items, moves, learnsets, base stats",
                "Champdex (champdex.com) – format rules: Lv. 50, 66 Stat Points (max 32 per stat)",
            ],
        },
        "types": TYPES,
        "typeChart": matrix,
        "typePressure": {"singles": type_pressure(singles), "doubles": type_pressure(doubles)},
        "pokemon": mons,
        "moves": move_out,
        "items": item_out,
        "abilities": abil_out,
        "topTeams": {"doubles": pk_teams},
    }
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
    # Small manifest the app checks before downloading the full file
    import hashlib
    with open(OUT_JSON, "rb") as f:
        raw_bytes = f.read()
    manifest = {"schema": DATA_SCHEMA, "built": data["meta"]["built"],
                "smogonMonth": data["meta"]["smogonMonth"], "bytes": len(raw_bytes),
                "sha256": hashlib.sha256(raw_bytes).hexdigest()}
    with open(os.path.join(os.path.dirname(OUT_JSON), "version.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"Wrote {OUT_JSON} ({os.path.getsize(OUT_JSON)//1024} KB): "
          f"{len(mons)} Pokemon, {len(move_out)} moves, {len(item_out)} items")

    # Sprites
    os.makedirs(SPRITES, exist_ok=True)
    missing = []
    for m in mons:
        dest = os.path.join(SPRITES, f"{m['id']}.png")
        if os.path.exists(dest):
            continue
        p = species[m["id"]]
        base = to_id(p.get("baseSpecies", p["name"]))
        forme = to_id(p.get("forme", ""))
        names = [f"{base}-{forme}" if forme else base, base]
        for n in names:
            png = fetch(f"{PS_PLAY}/sprites/gen5/{n}.png", f"sprite_{n}.png", binary=True, required=False)
            if png and png[:4] == b"\x89PNG":
                with open(dest, "wb") as f:
                    f.write(png)
                if n != names[0]:
                    missing.append(m["id"])
                break
    if missing:
        print("Sprites using base-form fallback:", ", ".join(missing))


if __name__ == "__main__":
    main()
