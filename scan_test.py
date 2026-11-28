"""Probe: does checked_bags change the price / work at all? PRG, El Al, a few dates."""
import inspect, json, os, pathlib, time
from datetime import date, timedelta
import fast_flights
from fast_flights import FlightQuery, Passengers, create_query, get_flights

OUT = pathlib.Path("out"); OUT.mkdir(exist_ok=True)
lines = [f"create_query{inspect.signature(create_query)}"]
rows = []
for off in (0, 9, 18, 27):
    dep = date(2027, 7, 4) + timedelta(days=off); ret = dep + timedelta(days=10)
    for bags in (None, 0, 1, 2, 3):
        extra = {} if bags is None else {"checked_bags": bags}
        status, prices = "ok", []
        for _ in range(3):
            try:
                q = create_query(
                    flights=[FlightQuery(date=dep.isoformat(), from_airport="TLV", to_airport="PRG", airlines=["LY"], max_stops=0),
                             FlightQuery(date=ret.isoformat(), from_airport="PRG", to_airport="TLV")],
                    trip="round-trip", seat="economy", passengers=Passengers(adults=1, children=0),
                    language="en-US", currency="ILS", **extra)
                res = list(get_flights(q) or [])
                prices = sorted(f.price for f in res if getattr(f, "price", None))
                status = "ok"; break
            except Exception as exc:
                status = type(exc).__name__ + ": " + str(exc)[:60]; time.sleep(1.5)
        rows.append(f"{dep} bags={bags}: {status} {prices[:4]}")
        time.sleep(1)
text = "\n".join(lines + rows)
print(text)
sf = os.environ.get("GITHUB_STEP_SUMMARY")
if sf:
    open(sf, "a", encoding="utf-8").write(f"## Bags probe ({os.environ.get('PROBE_LABEL','run')})\n\n```\n{text}\n```\n")
