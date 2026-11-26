"""Probe: why doesn't Google Flights (via fast-flights) return El Al direct TLV<->PRG?
Prints library info, then compares four query variants for several dates:
  A) round-trip, plain          B) one-way, plain
  C) round-trip + airlines=["LY"], max_stops=0 (filter)
  D) round-trip + ShoppingOptions(cheapest / price)
"""
import importlib.metadata
import inspect
import json
import os
import pathlib
import time
from datetime import date, timedelta

import fast_flights
from fast_flights import FlightQuery, Passengers, create_query, get_flights

OUT = pathlib.Path("out")
OUT.mkdir(exist_ok=True)

DEST = "PRG"
DAYS = int(os.environ.get("PROBE_DAYS", "47"))
DEPARTS = [date(2027, 7, 1) + timedelta(days=i) for i in range(0, DAYS, 3)]
STAYS = [9, 14]
PAX = Passengers(adults=1, children=0)


def legs(dep, ret, **first_leg_kwargs):
    out = [FlightQuery(date=dep.isoformat(), from_airport="TLV", to_airport=DEST, **first_leg_kwargs)]
    if ret:
        out.append(FlightQuery(date=ret.isoformat(), from_airport=DEST, to_airport="TLV"))
    return out


def fetch(flights, trip, shopping=False):
    kwargs = dict(flights=flights, trip=trip, seat="economy", passengers=PAX, language="en-US", currency="ILS")
    if shopping:
        so = fast_flights.ShoppingOptions(ranking_mode="cheapest", result_sort="price")
        try:
            return list(get_flights(create_query(shopping=so, **kwargs)) or [])
        except TypeError:
            return list(get_flights(create_query(**kwargs), shopping=so) or [])
    return list(get_flights(create_query(**kwargs)) or [])


def to_offers(res):
    offers = []
    for f in res:
        segs = list(getattr(f, "flights", []) or [])
        offers.append(
            {
                "price": getattr(f, "price", None),
                "airlines": list(getattr(f, "airlines", []) or []),
                "stops": max(len(segs) - 1, 0),
            }
        )
    return offers


def attempt(build):
    """build() -> list of flights. Returns (status, offers)."""
    last = "error"
    for _ in range(3):
        try:
            return "ok", to_offers(build())
        except (TypeError, AttributeError) as exc:
            msg = str(exc)
            if "unexpected keyword" in msg or "ShoppingOptions" in msg or "has no attribute" in msg:
                return "unsupported: " + msg[:60], []
            last = type(exc).__name__
        except Exception as exc:
            last = type(exc).__name__
        time.sleep(1.5)
    return last, []


def direct_text(offers):
    direct = [o for o in offers if o["stops"] == 0]
    if not direct:
        return f"no direct ({len(offers)} results)"
    return "; ".join(f"{'+'.join(o['airlines'])} {o['price']}" for o in direct)


def all_text(offers):
    if not offers:
        return "no results"
    return "; ".join(f"{'+'.join(o['airlines'])} {o['price']} ({o['stops']} stops)" for o in offers)[:160]


def info():
    lines = []
    try:
        lines.append("fast-flights version: " + importlib.metadata.version("fast-flights"))
    except Exception:
        lines.append("fast-flights version: unknown")
    for name, obj in (("FlightQuery", FlightQuery), ("create_query", create_query), ("get_flights", get_flights)):
        try:
            lines.append(f"{name}{inspect.signature(obj)}")
        except Exception as exc:
            lines.append(f"{name}: signature unavailable ({type(exc).__name__})")
    lines.append("has ShoppingOptions: " + str(hasattr(fast_flights, "ShoppingOptions")))
    return lines


def main():
    records = []
    rows = [
        "| Depart | Stay | A round-trip: direct | B one-way: direct | C filter LY direct | D shopping cheapest: direct |",
        "|---|---|---|---|---|---|",
    ]
    for dep in DEPARTS:
        for stay in STAYS:
            ret = dep + timedelta(days=stay)
            a = attempt(lambda: fetch(legs(dep, ret), "round-trip"))
            time.sleep(1)
            b = attempt(lambda: fetch(legs(dep, None), "one-way"))
            time.sleep(1)
            c = attempt(lambda: fetch(legs(dep, ret, airlines=["LY"], max_stops=0), "round-trip"))
            time.sleep(1)
            d = attempt(lambda: fetch(legs(dep, ret), "round-trip", shopping=True))
            time.sleep(1)
            records.append({"depart": dep.isoformat(), "stay": stay, "A": a, "B": b, "C": c, "D": d})
            cell = lambda r, fn=direct_text: fn(r[1]) if r[0] == "ok" else r[0]
            rows.append(f"| {dep.isoformat()} | {stay} | {cell(a)} | {cell(b)} | {cell(c, all_text)} | {cell(d)} |")
            print(rows[-1], flush=True)

    def stats(key):
        offers = [o for r in records for o in r[key][1]]
        elal = sum(any("el al" in x.lower() for x in o["airlines"]) for o in offers)
        direct = sum(o["stops"] == 0 for o in offers)
        status = sorted({r[key][0] for r in records})
        return f"{key}: {len(offers)} offers, {direct} direct, {elal} with El Al, statuses={status}"

    text = "\n".join(info() + [""] + [stats(k) for k in "ABCD"] + [""] + rows)
    print(text)
    label = os.environ.get("PROBE_LABEL", "run")
    (OUT / f"probe_results_{label}.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    sf = os.environ.get("GITHUB_STEP_SUMMARY")
    if sf:
        with open(sf, "a", encoding="utf-8") as fh:
            fh.write(f"## Probe results ({label})\n\n```\n" + text + "\n```\n")


if __name__ == "__main__":
    main()
