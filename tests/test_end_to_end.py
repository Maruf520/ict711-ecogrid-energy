from ecogrid import demo


def test_demo_trade_lifecycle_across_all_three_contexts():
    grid = demo.run()
    statuses = sorted(grid.marketplace.status.values())
    assert statuses == ["settled", "settled", "settled", "void"]  # B3 could not pay
    delivered = {t: s["delivered_wh"] for t, s in grid.settlement.sagas.items()}
    assert delivered == {"T00001": 200, "T00002": 100, "T00003": 75, "T00004": None}
    assert grid.metering.rejected == {"duplicate": 1, "malformed": 1, "impossible_jump": 1}
    assert grid.settlement.ledger.total() == 0
    assert grid.bus.dead_letters == []


def test_demo_prints_a_summary(capsys):
    demo.main()
    assert "Ledger total (must be 0): 0" in capsys.readouterr().out


def test_a_failing_handler_goes_to_dead_letters_without_stopping_others():
    from ecogrid.events import Event, EventBus

    bus = EventBus()
    seen = []

    def broken(_event):
        raise ValueError("cannot read this event")

    bus.subscribe("X", broken)
    bus.subscribe("X", seen.append)
    bus.publish(Event("X", {}))
    assert len(seen) == 1 and len(bus.dead_letters) == 1
