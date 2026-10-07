import ast
import json
import math
import random
import re
import time
from pathlib import Path

from ecogrid import demo
from ecogrid.demo import readings
from ecogrid.events import Event, EventBus
from ecogrid.marketplace import MarketplaceService
from ecogrid.metering import MeteringService
from ecogrid.settlement import SettlementService

SRC = Path(__file__).parents[1] / "src" / "ecogrid"
CONTRACTS = json.loads((Path(__file__).parents[1] / "contracts" / "events.json").read_text())
CONTEXTS = ["metering", "marketplace", "settlement"]
T0 = 1_789_437_600


def imported_modules(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_ff01_bounded_contexts_never_import_each_other():
    for context in CONTEXTS:
        others = {f"ecogrid.{c}" for c in CONTEXTS if c != context}
        bad = imported_modules(SRC / f"{context}.py") & others
        assert not bad, f"{context} imports {bad}: contexts may only talk through events"
    shared = imported_modules(SRC / "events.py")
    assert not shared & {f"ecogrid.{c}" for c in CONTEXTS}, "shared kernel must not depend on a context"


def test_ff02_every_event_matches_its_contract():
    grid = demo.run()
    types = {"str": str, "int": int, "bool": bool}
    assert {e.type for e in grid.bus.published} == set(CONTRACTS), "demo must produce every event"
    for event in grid.bus.published:
        fields = CONTRACTS[event.type]["fields"]
        assert set(fields) <= set(event.data), f"{event.type} is missing a field"
        for name, type_name in fields.items():
            assert isinstance(event.data[name], types[type_name]), f"{event.type}.{name}"


def test_ff02_each_event_is_published_by_its_owner_only():
    for context in CONTEXTS:
        source = (SRC / f"{context}.py").read_text()
        published = set(re.findall(r'Event\(\s*"(\w+)"', source))
        owned = {name for name, c in CONTRACTS.items() if c["owner"] == context}
        assert published == owned, f"{context} publishes {published}, owns {owned}"


def test_ff05_ingestion_throughput():
    batch = [r for i in range(500) for r in readings(f"M{i}", T0, 600, 500 + i, 2000 + i)]
    metering = MeteringService(EventBus())
    started = time.perf_counter()
    for reading in batch:
        metering.ingest(reading)
    rate = len(batch) / (time.perf_counter() - started)
    print(f"\nFF-05: {rate:,.0f} readings per second")
    assert rate >= 10_000


def test_ff06_matching_latency_p99():
    market = MarketplaceService(EventBus(), {})
    rng = random.Random(42)
    for i in range(5000):
        market.place_order(f"S{i}", f"MS{i}", "F1", T0, "sell", rng.randint(100, 500), rng.randint(10, 40))
    for i in range(1000):
        market.place_order(f"B{i}", f"MB{i}", "F1", T0, "buy", rng.randint(100, 1500), rng.randint(5, 45))
    times = sorted(market.latency_ms[5000:])
    p99 = times[math.ceil(0.99 * len(times)) - 1]
    print(f"\nFF-06: p99 {p99:.3f} ms")
    assert p99 <= 10


def random_scenario(rng: random.Random) -> list[Event]:
    events = []
    for n in range(rng.randint(1, 20)):
        b, s = rng.randint(0, 3), rng.randint(0, 3)
        events.append(
            Event(
                "TradeMatched",
                {
                    "trade_id": f"T{n}",
                    "interval": T0,
                    "wh": rng.randint(1, 5000),
                    "price": rng.randint(0, 100),
                    "buyer": f"B{b}",
                    "buyer_meter": f"MB{b}",
                    "seller": f"S{s}",
                    "seller_meter": f"MS{s}",
                },
            )
        )
    for i in range(4):
        events.append(
            Event(
                "IntervalReadingFinalised",
                {
                    "meter_id": f"MS{i}",
                    "interval": T0,
                    "import_wh": 0,
                    "export_wh": rng.randint(0, 6000),
                    "readings": 30,
                },
            )
        )
        if rng.random() < 0.7:  # some buyers' meters never report
            events.append(
                Event(
                    "IntervalReadingFinalised",
                    {
                        "meter_id": f"MB{i}",
                        "interval": T0,
                        "import_wh": rng.randint(0, 6000),
                        "export_wh": 0,
                        "readings": 30,
                    },
                )
            )
    return events


def test_ff07_money_is_never_created_lost_or_overdrawn():
    rng = random.Random(7)
    for _ in range(200):
        bus = EventBus()
        settlement = SettlementService(bus)
        for i in range(4):
            settlement.top_up(f"B{i}", rng.randint(0, 5000), f"pay-{i}")
        for event in random_scenario(rng):
            bus.publish(event)
        settlement.settle_overdue(now=T0 + 10_000)
        balances = settlement.ledger.balances
        assert settlement.ledger.total() == 0, "ledger does not balance"
        assert all(v >= 0 for k, v in balances.items() if k != "bank"), "an account went negative"
        assert all(v == 0 for k, v in balances.items() if k.startswith("held:")), "money stuck on hold"
        assert all(s["state"] in {"settled", "provisional", "failed"} for s in settlement.sagas.values())


def test_ff08_repeated_or_replayed_events_never_pay_twice():
    events = random_scenario(random.Random(3))
    results = []
    for copies in (1, 3):  # once, then every event delivered three times (at-least-once)
        bus = EventBus()
        settlement = SettlementService(bus)
        settlement.top_up("B0", 5000, "pay-0")
        settlement.top_up("B1", 5000, "pay-1")
        for event in events:
            for _ in range(copies):
                bus.publish(event)
        for event in events:  # full replay, e.g. after a restart
            bus.publish(event)
        results.append(dict(settlement.ledger.balances))
    assert results[0] == results[1]
