import pytest

from ecogrid.events import Event, EventBus
from ecogrid.marketplace import MarketplaceService, Order, OrderBook

T0 = 1_789_437_600


def test_best_price_first_and_trade_at_resting_price():
    book = OrderBook()
    book.place(Order("S1", "M-S1", "sell", 100, 15))
    book.place(Order("S2", "M-S2", "sell", 100, 12))  # cheapest
    fills = book.place(Order("B1", "M-B1", "buy", 150, 20))
    assert [(o.participant, wh, o.price) for o, wh in fills] == [("S2", 100, 12), ("S1", 50, 15)]


def test_orders_that_do_not_cross_wait_in_the_book():
    book = OrderBook()
    book.place(Order("S1", "M-S1", "sell", 100, 25))
    assert book.place(Order("B1", "M-B1", "buy", 100, 20)) == []


@pytest.mark.parametrize("wh, price", [(0, 10), (100, -1), (100, 101)])
def test_invalid_orders_are_refused(wh, price):
    with pytest.raises(ValueError):
        OrderBook().place(Order("B1", "M-B1", "buy", wh, price))


def test_closed_interval_refuses_orders():
    book = OrderBook()
    book.closed = True
    with pytest.raises(ValueError):
        book.place(Order("B1", "M-B1", "buy", 100, 10))


def test_trade_is_published_and_voided_when_buyer_cannot_pay():
    bus = EventBus()
    market = MarketplaceService(bus, {})
    market.place_order("S1", "M-S1", "F1", T0, "sell", 100, 10)
    (trade_id,) = market.place_order("B1", "M-B1", "F1", T0, "buy", 100, 12)
    assert bus.published[-1].type == "TradeMatched"
    failed = Event("TradeReservationFailed", {"trade_id": trade_id, "reason": "x"})
    bus.publish(failed)
    bus.publish(failed)  # a duplicate changes nothing
    assert market.status[trade_id] == "void"



def test_anti_corruption_layer_keeps_only_what_the_marketplace_needs():
    bus = EventBus()
    market = MarketplaceService(bus, {"M-S1": "S1"})
    data = {
        "meter_id": "M-S1",
        "interval": T0,
        "import_wh": 0,
        "export_wh": 250,
        "readings": 30,
        "some_new_field": "ignored",
    }
    bus.publish(Event("IntervalReadingFinalised", data))
    bus.publish(Event("IntervalReadingFinalised", dict(data, meter_id="UNKNOWN")))
    assert market.delivered == {("S1", T0): 250}
 