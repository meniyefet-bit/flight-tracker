"""Scan test: real Google Flights scans for all destinations, to measure
run time, errors/blocks and which airlines show up."""
import json
import os
import pathlib
import time
import traceback
from datetime import date, timedelta

from fast_flights import FlightQuery, Passengers, create_query, get_flights

OUT = pathlib.Path("out")
OUT.mkdir(exist_ok=True)

DESTINATIONS = {
    "Prague": "PRG",
    "Vienna": "VIE",
    "Bratislava": "BTS",
    "Tokyo Narita": "NRT",
    "Hanoi": "HAN",
    "Ho Chi Minh": "SGN",
    "Da Nang": "DAD",
}
DEPARTURES = ["2027-07-01", "2027-07-15", "2027-08-01"]
STAYS = [9, 14]
MY_AIRLINES = ["el al", "etihad", "flydubai", "emirates", "lufthansa", "austrian"]
PAUSE_SECONDS = 3
MAX_CONSECUTIVE_ERRORS = 5


def make_passengers():
    try:
        return Passengers(adults=2, children=3), "2 adults + 3 children"
    except Exception:
        return Passengers(adults=1), "FELL BACK to 1 adult"


def segment_route(segments):
    route = []
    for s in segments:
        try:
            route.append(f"{s.from_airport.code}-{s.to_airport.code}")
        except Exception:
            route.append("?")
    return route


def scan_one(passengers, code, dep, ret):
    started = time.time()
    rec = {"dest": code, "depart": dep, "return": ret}
    try:
        query = create_query(
            flights=[
                FlightQuery(date=dep, from_airport="TLV", to_airport=code),
                FlightQuery(date=ret, from_airport=code, to_airport="TLV"),
            ],
            trip="round-trip",
            seat="economy",
            passengers=passengers,
            language="en-US",
            currency="ILS",
        )
        flights = list(get_flights(query) or [])
        offers = []
        for f in flights:
            airlines = list(getattr(f, "airlines", []) or [])
            segments = list(getattr(f, "flights", []) or [])
            offers.append(
                {
                    "price": getattr(f, "price", None),
                    "airlines": airlines,
                    "segments": len(segments),
                    "route": segment_route(segments),
                    "mine": any(m in a.lower() for a in airlines for m in MY_AIRLINES),
                }
            )
        rec["status"] = "ok" if offers else "empty"
        rec["count"] = len(offers)
        rec["offers"] = offers[:20]
        if flights and "sample_fields" not in rec:
            rec["sample_fields"] = [a for a in dir(flights[0]) if not a.startswith("_")][:30]
    except Exception as exc:
        rec["status"] = "error"
        rec["error"] = str(exc)[:300]
        rec["trace"] = traceback.format_exc()[-500:]
    rec["seconds"] = round(time.time() - started, 1)
    return rec


def cheapest(offers, predicate=lambda o: True):
    prices = [o["price"] for o in offers if isinstance(o.get("price"), (int, float)) and predicate(o)]
    return min(prices) if prices else None


def main():
    passengers, passengers_note = make_passengers()
    print("Passengers:", passengers_note, flush=True)
    t_all = time.time()
    records = []
    consecutive_errors = 0
    aborted = False

    for name, code in DESTINATIONS.items():
        if aborted:
            break
        for dep in DEPARTURES:
            if aborted:
                break
            for stay in STAYS:
                ret = (date.fromisoformat(dep) + timedelta(days=stay)).isoformat()
                rec = scan_one(passengers, code, dep, ret)
                rec["dest_name"] = name
                rec["stay_days"] = stay
                records.append(rec)
                print(
                    f"{name} {dep}->{ret}: {rec['status']} "
                    f"count={rec.get('count', 0)} {rec['seconds']}s "
                    f"{rec.get('error', '')[:80]}",
                    flush=True,
                )
                consecutive_errors = consecutive_errors + 1 if rec["status"] == "error" else 0
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    print("Too many consecutive errors - stopping (likely rate limit).", flush=True)
                    aborted = True
                    break
                time.sleep(PAUSE_SECONDS)

    total_seconds = round(time.time() - t_all, 1)
    (OUT / "scan_results.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"Passengers: {passengers_note}. Total run: {total_seconds}s. Queries: {len(records)}"
        + (" (STOPPED EARLY)" if aborted else ""),
        "",
        "| Destination | OK | Empty | Error | Cheapest (any) | Cheapest (my airlines) | Cheapest (1 segment) | Airlines seen |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in DESTINATIONS:
        rows = [r for r in records if r["dest_name"] == name]
        if not rows:
            continue
        offers = [o for r in rows for o in r.get("offers", [])]
        airlines_seen = sorted({a for o in offers for a in o["airlines"]})
        lines.append(
            "| {n} | {ok} | {em} | {er} | {a} | {m} | {d} | {al} |".format(
                n=name,
                ok=sum(r["status"] == "ok" for r in rows),
                em=sum(r["status"] == "empty" for r in rows),
                er=sum(r["status"] == "error" for r in rows),
                a=cheapest(offers),
                m=cheapest(offers, lambda o: o["mine"]),
                d=cheapest(offers, lambda o: o["segments"] == 1),
                al=", ".join(airlines_seen)[:90],
            )
        )
    summary = "\n".join(lines)
    print("\n" + summary)

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write("## Scan test results\n\n" + summary + "\n")


if __name__ == "__main__":
    main()
