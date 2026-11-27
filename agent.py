"""Flight price tracker: scans Google Flights (via fast-flights) once a day for
the configured destinations / airlines / date window and reports the best fare
per destination to Telegram, compared with the previous day.

Files: config.json (settings), history.json (daily best prices), state.json.
Secrets (environment): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
"""
import json
import os
import time
import urllib.parse
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

CONFIG_PATH = "config.json"
HISTORY_PATH = "history.json"
STATE_PATH = "state.json"

IL = ZoneInfo("Asia/Jerusalem")
SEND_HOUR = 20                  # scheduled runs wait until 20:00 Israel time
MAX_WAIT_SECONDS = 100 * 60
SCAN_BUDGET_SECONDS = 75 * 60
PAUSE_SECONDS = 0.5
RETRIES = 2
MAX_CONSECUTIVE_FAILS = 60

HELP_TEXT = (
    "פקודות זמינות (השינוי ייכנס לתוקף בסריקה הבאה):\n"
    "/list - הצגת ההגדרות הנוכחיות\n"
    "/add_dest קוד[,קוד] שם europe|far - הוספת יעד (europe = ישיר בלבד, far = עד קונקשן אחד)\n"
    "   דוגמה: /add_dest HAN,SGN וייטנאם far\n"
    "/remove_dest שם-או-קוד - הסרת יעד\n"
    "/add_airline שם [קישור] - הוספת חברת תעופה\n"
    "/remove_airline שם - הסרת חברה\n"
    "/threshold מספר - סף ירידת מחיר להדגשה באחוזים\n"
    "לחיפוש מיידי: GitHub ← Actions ← Daily flight scan ← Run workflow"
)


# ----------------------------------------------------------------- files
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


# -------------------------------------------------------------- telegram
def tg_call(method, params):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        print("Telegram: TELEGRAM_BOT_TOKEN secret is missing/empty")
        return None
    token = token.strip()
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:200]
        except Exception:  # noqa: BLE001
            pass
        print(f"Telegram error: HTTP {exc.code} {body}")
        return None
    except Exception as exc:  # noqa: BLE001
        print("Telegram error:", type(exc).__name__)
        return None


def split_message(text, limit=3800):
    chunks, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > limit and current:
            chunks.append(current)
            current = ""
        current += line + "\n"
    if current.strip():
        chunks.append(current)
    return chunks


def tg_send(text):
    print(text, flush=True)
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not chat:
        return
    for chunk in split_message(text):
        tg_call("sendMessage", {"chat_id": chat, "text": chunk, "disable_web_page_preview": "true"})


# -------------------------------------------------------------- commands
def find_dest(cfg, key):
    key = key.strip().lower()
    for d in cfg["destinations"]:
        if d["name"].lower() == key or key.upper() in d["airports"]:
            return d
    return None


def handle_command(cfg, text):
    parts = text.strip().split()
    if not parts:
        return None
    cmd = parts[0].split("@")[0].lower()
    args = parts[1:]

    if cmd in ("/start", "/help"):
        return HELP_TEXT

    if cmd == "/list":
        lines = ["יעדים:"]
        for d in cfg["destinations"]:
            kind = "ישיר בלבד" if d["max_stops"] == 0 else "עד קונקשן אחד"
            lines.append(f"• {d['name']} ({', '.join(d['airports'])}) - {kind}")
        lines.append("")
        lines.append("חברות: " + ", ".join(a["name"] for a in cfg["airlines"]))
        w = cfg["window"]
        lines.append(f"תאריכים: יציאה מ-{w['depart_from']}, חזרה עד {w['return_by']}, {w['min_days']}-{w['max_days']} ימים")
        lines.append(f"סף הדגשת ירידה: {cfg['drop_threshold_pct']}%")
        return "\n".join(lines)

    if cmd == "/add_dest":
        if len(args) < 3 or args[-1].lower() not in ("europe", "far"):
            return "שימוש: /add_dest קוד[,קוד] שם europe|far\nדוגמה: /add_dest VIE וינה europe"
        codes = [c.upper() for c in args[0].split(",") if c]
        if not all(len(c) == 3 and c.isalpha() for c in codes):
            return "קוד שדה תעופה חייב להיות 3 אותיות באנגלית (למשל PRG)."
        name = " ".join(args[1:-1])
        max_stops = 0 if args[-1].lower() == "europe" else 1
        existing = find_dest(cfg, name)
        if existing:
            existing.update(airports=codes, max_stops=max_stops, active=True)
            return f"היעד {name} עודכן."
        cfg["destinations"].append({"name": name, "airports": codes, "max_stops": max_stops, "active": True})
        return f"היעד {name} ({', '.join(codes)}) נוסף. ייסרק בריצה הבאה."

    if cmd == "/remove_dest":
        if not args:
            return "שימוש: /remove_dest שם-או-קוד"
        dest = find_dest(cfg, " ".join(args))
        if not dest:
            return "היעד לא נמצא. /list מציג את היעדים."
        cfg["destinations"].remove(dest)
        return f"היעד {dest['name']} הוסר."

    if cmd == "/add_airline":
        if not args:
            return "שימוש: /add_airline שם [קישור]"
        url = ""
        if args[-1].startswith("http"):
            url = args[-1]
            args = args[:-1]
        name = " ".join(args)
        for a in cfg["airlines"]:
            if a["name"].lower() == name.lower():
                return f"{a['name']} כבר ברשימה."
        cfg["airlines"].append({"name": name, "match": [name.lower()], "url": url})
        return f"החברה {name} נוספה."

    if cmd == "/remove_airline":
        name = " ".join(args).lower()
        for a in cfg["airlines"]:
            if a["name"].lower() == name:
                cfg["airlines"].remove(a)
                return f"החברה {a['name']} הוסרה."
        return "החברה לא נמצאה. /list מציג את החברות."

    if cmd == "/threshold":
        try:
            value = float(args[0])
            if not 0 < value < 100:
                raise ValueError
        except (IndexError, ValueError):
            return "שימוש: /threshold מספר בין 1 ל-99"
        cfg["drop_threshold_pct"] = value
        return f"סף ההדגשה עודכן ל-{value:g}%."

    return "פקודה לא מוכרת.\n\n" + HELP_TEXT


def process_commands(cfg, state):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return
    resp = tg_call("getUpdates", {"offset": state.get("last_update_id", 0) + 1, "timeout": 0})
    if not resp or not resp.get("ok"):
        return
    chat = str(os.environ.get("TELEGRAM_CHAT_ID", ""))
    for upd in resp.get("result", []):
        state["last_update_id"] = upd["update_id"]
        msg = upd.get("message") or {}
        sender = str((msg.get("chat") or {}).get("id", ""))
        text = msg.get("text", "")
        if not chat:
            print("Chat id seen (put it in the TELEGRAM_CHAT_ID secret):", sender)
            continue
        if sender != chat or not text.startswith("/"):
            continue
        reply = handle_command(cfg, text)
        if reply:
            tg_send(reply)


# ------------------------------------------------------------------ scan
def _dt(sd):
    y, m, d = sd.date
    t = tuple(sd.time) if sd.time else ()
    return datetime(y, m, d, t[0] if len(t) > 0 else 0, t[1] if len(t) > 1 else 0)


def _max_stops_for(code, cfg):
    for d in cfg["destinations"]:
        if code in d["airports"]:
            return d["max_stops"]
    return 1


def _fetch(cfg, origin, dest, dep, ret, airlines=None, max_stops=None, shopping=False):
    import fast_flights
    from fast_flights import FlightQuery, Passengers, create_query, get_flights

    extra = {}
    if airlines:
        extra["airlines"] = airlines
    if max_stops is not None:
        extra["max_stops"] = max_stops
    kwargs = dict(
        flights=[
            FlightQuery(date=dep.isoformat(), from_airport=origin, to_airport=dest, **extra),
            FlightQuery(date=ret.isoformat(), from_airport=dest, to_airport=origin),
        ],
        trip="round-trip",
        seat="economy",
        passengers=Passengers(adults=cfg.get("adults", 1), children=0),
        language="en-US",
        currency=cfg.get("currency", "ILS"),
    )
    so = None
    if shopping and hasattr(fast_flights, "ShoppingOptions"):
        so = fast_flights.ShoppingOptions(ranking_mode="cheapest", result_sort="price")
    if so is None:
        return list(get_flights(create_query(**kwargs)) or [])
    try:
        return list(get_flights(create_query(shopping=so, **kwargs)) or [])
    except TypeError:
        return list(get_flights(create_query(**kwargs), shopping=so) or [])


def search_google(origin, dest, dep, ret, cfg):
    """Two complementary queries, merged: (1) filtered to our airlines and stop limit
    (Google then returns them even when they are not among its default top results),
    (2) unfiltered 'cheapest first' list. Raises only if both fail."""
    iata = [a["iata"] for a in cfg["airlines"] if a.get("iata")]
    results, last_exc, ok = [], None, False
    attempts = []
    if iata:
        attempts.append(dict(airlines=iata, max_stops=_max_stops_for(dest, cfg), shopping=True))
    attempts.append(dict(shopping=True))
    for kw in attempts:
        try:
            results += _fetch(cfg, origin, dest, dep, ret, **kw)
            ok = True
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if kw.get("airlines") and type(exc).__name__ == "TypeError":
                # filter not accepted by this library version -> keep going unfiltered
                pass
    if not ok and last_exc:
        raise last_exc
    return results


def parse_offer(flight):
    segs = list(getattr(flight, "flights", []) or [])
    airlines = list(getattr(flight, "airlines", []) or [])
    price = getattr(flight, "price", None)
    if not segs or not isinstance(price, (int, float)) or price <= 0:
        return None
    layovers = []
    for a, b in zip(segs, segs[1:]):
        try:
            layovers.append((_dt(b.departure) - _dt(a.arrival)).total_seconds() / 3600)
        except Exception:  # noqa: BLE001
            layovers.append(None)
    route = []
    for s in segs:
        try:
            route.append(f"{s.from_airport.code}-{s.to_airport.code}")
        except Exception:  # noqa: BLE001
            route.append("?")
    return {"price": price, "airlines": airlines, "stops": len(segs) - 1, "layovers": layovers, "route": route}


def airline_url(offer_airlines, cfg):
    for a in cfg["airlines"]:
        for name in offer_airlines:
            if any(m in name.lower() for m in a["match"]):
                return a.get("url", "")
    return ""


def classify(offer, dest, cfg):
    """Return 'short', 'long' or None (rejected)."""
    if offer["stops"] > dest["max_stops"]:
        return None
    names = [n.lower() for n in offer["airlines"]]
    if not any(m in n for a in cfg["airlines"] for m in a["match"] for n in names):
        return None
    known = [x for x in offer["layovers"] if x is not None]
    longest = max(known) if known else 0
    if longest > cfg["layover_max_hours"]:
        return None
    return "long" if longest > cfg["layover_ok_hours"] else "short"


def query_with_retry(search_fn, origin, code, dep, ret, cfg):
    """Return (flights, status) where status is 'ok', 'empty' or 'error'."""
    last_status = "error"
    for attempt in range(RETRIES + 1):
        try:
            flights = search_fn(origin, code, dep, ret, cfg)
            return flights, ("ok" if flights else "empty")
        except Exception as exc:  # noqa: BLE001
            last_status = "empty" if type(exc).__name__ == "FlightsNotFound" else "error"
            time.sleep(1.5 if attempt < RETRIES else 0)
    return [], last_status


def combos(cfg):
    w = cfg["window"]
    start = date.fromisoformat(w["depart_from"])
    end = date.fromisoformat(w["return_by"])
    step = max(1, int(cfg.get("departure_step_days", 1)))
    out = []
    d = start
    while d <= end:
        for stay in range(w["min_days"], w["max_days"] + 1):
            r = d + timedelta(days=stay)
            if r <= end:
                out.append((d, r, stay))
        d += timedelta(days=step)
    return out


def run_scan(cfg, search_fn=search_google, pause=PAUSE_SECONDS, budget=SCAN_BUDGET_SECONDS):
    started = time.time()
    active = [d for d in cfg["destinations"] if d.get("active", True)]
    best = {d["name"]: {"short": None, "long": None} for d in active}
    stats = {"queries": 0, "errors": 0, "empty": 0, "stopped": ""}
    consecutive = 0

    for dep, ret, stay in combos(cfg):
        for dest in active:
            for code in dest["airports"]:
                if time.time() - started > budget:
                    stats["stopped"] = "time"
                    return best, stats
                flights, status = query_with_retry(search_fn, cfg["origin"], code, dep, ret, cfg)
                stats["queries"] += 1
                if status == "error":
                    stats["errors"] += 1
                    consecutive += 1
                elif status == "empty":
                    stats["empty"] += 1
                    consecutive = 0
                else:
                    consecutive = 0
                if consecutive >= MAX_CONSECUTIVE_FAILS:
                    stats["stopped"] = "blocked"
                    return best, stats
                for f in flights:
                    offer = parse_offer(f)
                    if not offer:
                        continue
                    kind = classify(offer, dest, cfg)
                    if not kind:
                        continue
                    known = [x for x in offer["layovers"] if x is not None]
                    rec = {
                        "price": offer["price"],
                        "airlines": offer["airlines"],
                        "airport": code,
                        "depart": dep.isoformat(),
                        "return": ret.isoformat(),
                        "days": stay,
                        "stops": offer["stops"],
                        "layover_h": round(max(known), 1) if known else 0,
                        "url": airline_url(offer["airlines"], cfg),
                    }
                    cur = best[dest["name"]][kind]
                    if cur is None or rec["price"] < cur["price"]:
                        best[dest["name"]][kind] = rec
                time.sleep(pause)
    return best, stats


# ---------------------------------------------------------------- report
def pick_entries(best, cfg):
    """Choose the headline offer per destination. Returns {name: (headline, long_alt or None)}."""
    entries = {}
    for name, kinds in best.items():
        short, long_ = kinds["short"], kinds["long"]
        if short is None and long_ is None:
            continue
        if short is None:
            entries[name] = (dict(long_, long=True), None)
            continue
        alt = None
        if long_ and long_["price"] <= short["price"] * (1 - cfg["cheap_exception_pct"] / 100):
            alt = long_
        entries[name] = (dict(short, long=False), alt)
    return entries


def fmt_price(p):
    return f"{int(round(p)):,} ₪"


def fmt_day(iso):
    d = date.fromisoformat(iso)
    return f"{d.day:02d}.{d.month:02d}"


def previous_entry(history, name, today):
    for day in sorted(history, reverse=True):
        if day < today and name in history[day]:
            return day, history[day][name]
    return None, None


def stops_text(rec):
    return "ישיר" if rec["stops"] == 0 else f"קונקשן אחד (המתנה {rec['layover_h']:g} ש')"


def offer_lines(rec, cfg):
    airlines = " + ".join(rec["airlines"])
    lines = [f"{airlines} · {stops_text(rec)}", f"{fmt_day(rec['depart'])} ← {fmt_day(rec['return'])} ({rec['days']} ימים) · {rec['airport']}"]
    return lines


def build_report(cfg, today, entries, history, stats):
    d = date.fromisoformat(today)
    lines = [f"✈️ מעקב טיסות · {d.day:02d}.{d.month:02d}.{d.year}", "מחיר לנוסע אחד, הלוך חזור, ללא מזוודה", ""]
    if not entries:
        lines.append("לא נמצאו היום תוצאות מתאימות.")
    for name, (rec, alt) in entries.items():
        lines.append(f"📍 {name} · {fmt_price(rec['price'])}" + (" ⚠️ המתנה ארוכה" if rec.get("long") else ""))
        lines.extend("   " + x for x in offer_lines(rec, cfg))
        prev_day, prev = previous_entry(history, name, today)
        if prev:
            diff = rec["price"] - prev["price"]
            when = "אתמול" if prev_day == (d - timedelta(days=1)).isoformat() else fmt_day(prev_day)
            if abs(diff) < 0.5:
                lines.append(f"   ללא שינוי מ{when}")
            else:
                pct = abs(diff) / prev["price"] * 100
                arrow = "▼" if diff < 0 else "▲"
                lines.append(f"   {arrow} {fmt_price(abs(diff))} ({pct:.1f}%) לעומת {when} ({fmt_price(prev['price'])})")
                if diff < 0 and pct >= cfg["drop_threshold_pct"]:
                    lines.append(f"   🔥 ירידה של {pct:.1f}%")
            earlier = [h[name]["price"] for day, h in history.items() if day < today and name in h]
            if earlier and rec["price"] < min(earlier):
                lines.append("   🏆 המחיר הנמוך ביותר מאז תחילת המעקב")
        else:
            lines.append("   יום ראשון במעקב")
        if rec.get("url"):
            lines.append(f"   🔗 {rec['url']}")
        if alt:
            lines.append(f"   💡 זול יותר עם המתנה ארוכה: {fmt_price(alt['price'])} ({' + '.join(alt['airlines'])}, "
                         f"{fmt_day(alt['depart'])}←{fmt_day(alt['return'])}, המתנה {alt['layover_h']:g} ש')")
        lines.append("")
    lines.append("מקור: Google Flights (לא האתר הרשמי של החברה). בדוק מחיר סופי באתר החברה.")
    q = stats.get("queries", 0)
    if q and (stats["errors"] / q) > 0.4:
        lines.append(f"⚠️ {stats['errors']} מתוך {q} חיפושים נכשלו, ייתכן שהתוצאות חלקיות.")
    if stats.get("stopped") == "time":
        lines.append("⚠️ הסריקה נעצרה בגלל מגבלת זמן, התוצאות חלקיות.")
    if stats.get("stopped") == "blocked":
        lines.append("⚠️ הסריקה נעצרה: רצף שגיאות ארוך, ייתכן שגוגל חסם זמנית.")
    return "\n".join(lines).strip()


# ------------------------------------------------------------------ main
def wait_for_send_time():
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        return
    now = datetime.now(IL)
    target = now.replace(hour=SEND_HOUR, minute=0, second=0, microsecond=0)
    wait = (target - now).total_seconds()
    if 0 < wait <= MAX_WAIT_SECONDS:
        print(f"Waiting {int(wait)}s until {SEND_HOUR}:00 Israel time", flush=True)
        time.sleep(wait)


def main():
    cfg = load_json(CONFIG_PATH, None)
    if cfg is None:
        raise SystemExit("config.json is missing")
    history = load_json(HISTORY_PATH, {})
    state = load_json(STATE_PATH, {"last_update_id": 0})

    process_commands(cfg, state)
    save_json(CONFIG_PATH, cfg)
    save_json(STATE_PATH, state)

    best, stats = run_scan(cfg)
    entries = pick_entries(best, cfg)
    today = datetime.now(IL).date().isoformat()
    message = build_report(cfg, today, entries, history, stats)

    history[today] = {name: rec for name, (rec, _alt) in entries.items()}
    save_json(HISTORY_PATH, history)

    wait_for_send_time()
    tg_send(message)


if __name__ == "__main__":
    main()
