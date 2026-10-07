
from __future__ import annotations

from ecogrid.app import EcoGrid, build

START = 1_789_437_600  


def readings(
    meter_id: str, start: int, seconds: int, import_w: float = 0, export_w: float = 0, step: int = 10
) -> list[dict]:
    """Fake meter readings at constant power, every `step` seconds (cumulative Wh).

    Starts one step BEFORE `start` so the interval has a starting total to diff against.
    Also used by the tests.
    """
    out = []
    for i, ts in enumerate(range(start - step, start + seconds, step)):
        hours = i * step / 3600
        out.append(
            {
                "meter_id": meter_id,
                "ts": ts,
                "import_wh": int(import_w * hours),
                "export_wh": int(export_w * hours),
            }
        )
    return out


HOMES = [
    ("S1", "M-S1", 0, 3600), 
    ("S2", "M-S2", 0, 900),  
    ("B1", "M-B1", 2400, 0),
    ("B2", "M-B2", 2400, 0),
    ("B3", "M-B3", 2400, 0),  
]


def run() -> EcoGrid:
    grid = build({meter: who for who, meter, _, _ in HOMES}, duplicate_every=4)
    for buyer, cents in (("B1", 500), ("B2", 500), ("B3", 0)):
        grid.settlement.top_up(buyer, cents, payment_ref=f"pay-{buyer}")

    # (who, side, Wh, c/kWh)
    orders = [
        ("S1", "sell", 300, 15),
        ("S2", "sell", 250, 16),  
        ("B1", "buy", 200, 20),
        ("B2", "buy", 200, 18),
        ("B3", "buy", 100, 20),
    ]
    for who, side, wh, price in orders:
        grid.marketplace.place_order(who, f"M-{who}", "FEEDER-1", START, side, wh, price)
    grid.marketplace.close_interval(START)

    messages = [r for _, meter, use, solar in HOMES for r in readings(meter, START, 300, use, solar)]
    messages.sort(key=lambda r: r["ts"])

    # --- inject some bad data ---
    messages.insert(40, dict(messages[39])) 
    messages.insert(80, {"meter_id": "M-S1", "ts": "garbage"})
    spike = dict(messages[120], ts=messages[120]["ts"] + 1)
    spike["export_wh"] += 50_000  
    messages.insert(121, spike)

    for message in messages:
        grid.metering.ingest(message)
    grid.metering.finalise(START)
    return grid


def main() -> None:
    grid = run()
    statuses = list(grid.marketplace.status.values())
    print("Rejected readings:", dict(grid.metering.rejected))
    print(f"Trades: {len(statuses)}  settled: {statuses.count('settled')}  voided: {statuses.count('void')}")
    for trade_id, saga in grid.settlement.sagas.items():
        print(
            f"  {trade_id}: {saga['trade']['wh']} Wh agreed, "
            f"{saga['delivered_wh']} Wh delivered, {saga['state']}"
        )
    print("Ledger total (must be 0):", grid.settlement.ledger.total())
    print("Events published:", len(grid.bus.published), " dead letters:", len(grid.bus.dead_letters))


if __name__ == "__main__":
    main()
