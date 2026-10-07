import pytest

from ecogrid.events import Event, EventBus
from ecogrid.settlement import Ledger, NotEnoughMoney, SettlementService

T0 = 1_789_437_600
MC = 1000  # millicents per cent


# builders for the two events settlement 
def trade(trade_id="T1", wh=5000, price=20, buyer="B1", seller="S1"):
    return Event(
        "TradeMatched",
        {
            "trade_id": trade_id,
            "interval": T0,
            "wh": wh,
            "price": price,
            "buyer": buyer,
            "buyer_meter": f"M-{buyer}",
            "seller": seller,
            "seller_meter": f"M-{seller}",
        },
    )


def meter(meter_id, import_wh=0, export_wh=0):
    return Event(
        "IntervalReadingFinalised",
        {
            "meter_id": meter_id,
            "interval": T0,
            "import_wh": import_wh,
            "export_wh": export_wh,
            "readings": 30,
        },
    )


@pytest.fixture
def setup():
    bus = EventBus()
    settlement = SettlementService(bus)
    settlement.top_up("B1", 1000, "pay-1")  # $10.00
    return bus, settlement, settlement.ledger


def test_ledger_refuses_unbalanced_or_overdrawn_transactions():
    ledger = Ledger()
    with pytest.raises(ValueError):
        ledger.post("t1", {"bank": -10, "wallet:A": 9})
    with pytest.raises(NotEnoughMoney):
        ledger.post("t2", {"wallet:A": -1, "wallet:B": 1})
    assert ledger.entries == []


def test_same_transaction_id_is_applied_once(setup):
    _, settlement, ledger = setup
    settlement.top_up("B1", 1000, "pay-1")  
    assert ledger.balances["wallet:B1"] == 1000 * MC


def test_happy_path_pays_seller_for_delivered_energy(setup):
    bus, settlement, ledger = setup
    for event in (trade(), meter("M-S1", export_wh=6000), meter("M-B1", import_wh=8000)):
        bus.publish(event)
    assert settlement.sagas["T1"]["state"] == "settled"
    assert ledger.balances["earnings:S1"] == 95 * MC  
    assert ledger.balances["fees"] == 5 * MC
    assert ledger.balances["wallet:B1"] == 900 * MC
    assert ledger.balances["held:B1"] == 0
    assert ledger.total() == 0


def test_seller_shortfall_is_refunded_to_buyer(setup):
    bus, settlement, ledger = setup
    for event in (trade(), meter("M-B1", import_wh=8000), meter("M-S1", export_wh=3000)):
        bus.publish(event)
    assert settlement.sagas["T1"]["delivered_wh"] == 3000
    # only 3 kWh actually arrived: 3 x 20c = 60c, minus 3c fee
    assert ledger.balances["earnings:S1"] == 57 * MC
    assert ledger.balances["wallet:B1"] == 940 * MC


def test_not_enough_money_starts_the_compensation(setup):
    bus, settlement, ledger = setup
    bus.publish(trade(trade_id="T2", wh=100_000, price=50))  # $50 > $10
    assert settlement.sagas["T2"]["state"] == "failed"
    assert bus.published[-1].type == "TradeReservationFailed"
    assert ledger.balances["wallet:B1"] == 1000 * MC


def test_overdue_meter_data_settles_provisionally(setup):
    bus, settlement, ledger = setup
    bus.publish(trade())
    bus.publish(meter("M-S1", export_wh=6000))  
    assert settlement.settle_overdue(now=T0 + 300 + 899) == 0
    assert settlement.settle_overdue(now=T0 + 300 + 900) == 1
    assert settlement.sagas["T1"]["state"] == "provisional"
    assert ledger.total() == 0
