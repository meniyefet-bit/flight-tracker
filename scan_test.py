"""Diagnostic scan: compares passenger counts and currency, retries failures,
and lists every airline Google Flights returns."""
import json
import os
import pathlib
import time
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
DEPART = "2027-07-15"
STAYS = [9, 14]
MY_AIRLINES = ["el al", "etihad", "flydubai", "emirates", "lufthansa", "austrian"]
RETRIES = 2
PAUSE_SECONDS = 2

CONFIGS = {
    "5pax_ILS": {"adults": 2, "children": 3, "currency": "ILS"},
    "1adult_ILS": {"adults": 1, "children": 0, "currency": "ILS"},
    "5pax_USD": {"adults": 2, "children": 3, "currency": "USD"},
}


def run_query(code, dep, ret, cfg):
    started = time.time()
    rec = {"dest": code, "depart": dep, "return": ret, "attempts": 0}
    for attempt in range(1, RETRIES + 2):
        rec["attempts"] = attempt
        try:
            query = create_query(
                flights=[
                    FlightQuery(date=dep, from_airport="TLV", to_airport=code),
                    FlightQuery(date=ret, from_airport=code, to_airport="TLV"),
                ],
                trip="round-trip",
                seat="economy",
                passengers=Passengers(adults=cfg["adults"], children=cfg["children"]),
                language="en-US",
                currency=cfg["currency"],
            )
            flights = list(get_flights(query) or [])
            offers = []
            for f in flights:
                airlines = list(getattr(f, "airlines", []) or [])
                segments = list(getattr(f, "flights", []) or [])
                route = []
                for s in segments:
                    try:
                        route.append(f"{s.from_airport.code}-{s.to_airport.code}")
                    except Exception:
                        route.append("?")
                offers.append(
                    {
                        "price": getattr(f, "price", None),
                        "airlines": airlines,
                        "segments": len(segments),
                        "route": route,
                        "mine": any(m in a.lower() for a in airlines for m in MY_AIRLINES),
                    }
                )
            rec["status"] = "ok" if offers else "empty"
            rec["count"] = len(offers)
            rec["offers"] = offers
            rec.pop("error", None)
            break
        except Exception as exc:
            rec["status"] = "error"
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
            time.sleep(PAUSE_SECONDS)
    rec["seconds"] = round(time.time() - started, 1)
    return rec


def cheapest(offers, predicate=lambda o: True):
    prices = [o["price"] for o in offers if isinstance(o.get("price"), (int, float)) and predicate(o)]
    return min(prices) if prices else None


def main():
    t_all = time.time()
    records = []
    for cfg_name, cfg in CONFIGS.items():
        for name, code in DESTINATIONS.items():
            for stay in STAYS:
                ret = (date.fromisoformat(DEPART) + timedelta(days=stay)).isoformat()
                rec = run_query(code, DEPART, ret, cfg)
                rec.update(config=cfg_name, dest_name=name, stay_days=stay)
                records.append(rec)
                print(
                    f"[{cfg_name}] {name} {stay}d: {rec['status']} "
                    f"count={rec.get('count', 0)} attempts={rec['attempts']} "
                    f"{rec['seconds']}s {rec.get('error', '')[:70]}",
                    flush=True,
                )
                time.sleep(PAUSE_SECONDS)

    total = round(time.time() - t_all, 1)
    (OUT / "diag_results.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"Total run: {total}s. Queries: {len(records)}",
        "",
        "| Config | Destination | OK | Err | Extra tries | Cheapest | Cheapest mine | Airlines seen |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for cfg_name in CONFIGS:
        for name in DESTINATIONS:
            rows = [r for r in records if r["config"] == cfg_name and r["dest_name"] == name]
            offers = [o for r in rows for o in r.get("offers", [])]
            airlines = sorted({a for o in offers for a in o["airlines"]})
            lines.append(
                "| {c} | {n} | {ok} | {er} | {ex} | {a} | {m} | {al} |".format(
                    c=cfg_name,
                    n=name,
                    ok=sum(r["status"] in ("ok", "empty") for r in rows),
                    er=sum(r["status"] == "error" for r in rows),
                    ex=sum(r["attempts"] - 1 for r in rows),
                    a=cheapest(offers),
                    m=cheapest(offers, lambda o: o["mine"]),
                    al=", ".join(airlines)[:80],
                )
            )
    summary = "\n".join(lines)
    print("\n" + summary)

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as fh:
            fh.write("## Diagnostic results\n\n" + summary + "\n")


if __name__ == "__main__":
    main()
