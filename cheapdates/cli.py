from __future__ import annotations

import datetime as dt
import json
import sys

import typer

from .core import cheapest_dates

app = typer.Typer(add_completion=False, help="Cheapest dates to fly a route, from Google Flights.")


@app.command()
def main(
    origin: str = typer.Argument(..., help="IATA code, e.g. JFK"),
    destination: str = typer.Argument(..., help="IATA code, e.g. LHR"),
    start: dt.datetime = typer.Argument(..., formats=["%Y-%m-%d"], help="first departure date"),
    end: dt.datetime = typer.Argument(..., formats=["%Y-%m-%d"], help="last departure date"),
    trip_length: int | None = typer.Option(None, "--return", "-r", help="round-trip: return N days after departure (omit for one-way)"),
    currency: str = typer.Option("USD", "--currency", "-c"),
    backend: str = typer.Option("auto", "--backend", "-b", help="auto | graph (browser, 1 call/60 days) | sweep (fast-flights, 1 call/day)"),
    nonstop: bool = typer.Option(False, "--nonstop", help="nonstop only"),
    seat: str = typer.Option("economy", "--seat", help="economy | premium_economy | business | first"),
    include_airlines: bool = typer.Option(False, "--include-airlines", help="Use individual flight searches so airline names match their prices"),
    top: int = typer.Option(5, "--top", help="how many cheapest dates to highlight"),
    as_json: bool = typer.Option(False, "--json", help="machine-readable output"),
):
    try:
        res = cheapest_dates(origin, destination, start.date(), end.date(), trip_length=trip_length, currency=currency,
                             backend=backend, max_stops=0 if nonstop else None, seat=seat,
                             include_airlines=include_airlines)
    except ValueError as e:
        raise typer.BadParameter(str(e)) from e
    if as_json:
        print(json.dumps(res.to_json(), indent=1))
        if res.status in ("partial", "error"):
            raise typer.Exit(1)
        return
    sym = {"USD": "$", "EUR": "€", "GBP": "£"}.get(res.currency, res.currency + " ")
    cheap = {d.depart for d in sorted(res.priced, key=lambda d: d.price)[:top]}
    for d in res.days:
        price = f"{sym}{d.price}" if d.price is not None else "--"
        mark = " ◀" if d.depart in cheap else ""
        ret = f" → {d.ret:%b %d}" if d.ret else ""
        air = f"  {d.airlines}" if d.airlines else ""
        print(f"{d.depart} {d.depart:%a}{ret}  {price:>8}{air}{mark}")
    if res.cheapest:
        c = res.cheapest
        print(f"\ncheapest: {c.depart} ({c.depart:%a})" + (f" returning {c.ret}" if c.ret else "") + f"  {sym}{c.price}" + (f"  {c.airlines}" if c.airlines else ""))
    else:
        print("\nno prices found", file=sys.stderr)
    print(f"[{res.backend} backend · {len(res.priced)}/{len(res.days)} dates priced]", file=sys.stderr)
    for w in res.warnings:
        print("warning:", w, file=sys.stderr)
    if res.status in ("partial", "error"):
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
