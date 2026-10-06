from __future__ import annotations

import heapq
import itertools
import time
from dataclasses import dataclass

from ecogrid.events import Event, EventBus

PRICE_CAP = 100  # cents per kWh


@dataclass
class Order:
    participant: str
    meter_id: str
    side: str  # "buy" or "sell"
    wh: int  # energy still wanted or offered
    price: int  # cents per kWh
    seq: int = 0


class OrderBook:

    def __init__(self) -> None:
        self.closed = False
        self._bids: list[tuple[int, int, Order]] = []  # highest price first
        self._asks: list[tuple[int, int, Order]] = []  # lowest price first
        self._counter = itertools.count()

    def place(self, order: Order) -> list[tuple[Order, int]]:
        """Add an order. Returns (matched resting order, Wh) pairs."""
        if self.closed:
            raise ValueError("this trading interval is closed")
        if order.wh <= 0 or not 0 <= order.price <= PRICE_CAP:
            raise ValueError("invalid quantity or price")
        order.seq = next(self._counter)
        other_side = self._asks if order.side == "buy" else self._bids
        fills = []
        while order.wh > 0 and other_side and _prices_cross(order, other_side[0][2]):
            resting = other_side[0][2]
            wh = min(order.wh, resting.wh)
            order.wh -= wh
            resting.wh -= wh
            fills.append((resting, wh))
            if resting.wh == 0:
                heapq.heappop(other_side)
        if order.wh > 0:
            if order.side == "buy":
                heapq.heappush(self._bids, (-order.price, order.seq, order))
            else:
                heapq.heappush(self._asks, (order.price, order.seq, order))
        return fills


def _prices_cross(incoming: Order, resting: Order) -> bool:
    if incoming.side == "buy":
        return incoming.price >= resting.price
    return incoming.price <= resting.price


class MarketplaceService:
    def __init__(self, bus: EventBus, meter_owners: dict[str, str]) -> None:
        self.bus = bus
        self.trades: dict[str, dict] = {}
        self.status: dict[str, str] = {}  # trade_id -> matched / void / settled
        self.delivered: dict[tuple[str, int], int] = {}  # (participant, interval) -> Wh exported
        self.latency_ms: list[float] = []
        self._meter_owners = meter_owners
        self._books: dict[tuple[int, str], OrderBook] = {}
        bus.subscribe("IntervalReadingFinalised", self._translate_meter_data)
        bus.subscribe("TradeReservationFailed", self._void_trade)
        bus.subscribe("SettlementCompleted", self._mark_settled)

    def place_order(
        self, participant: str, meter_id: str, feeder: str, interval: int, side: str, wh: int, price: int
    ) -> list[str]:
        """Place an offer (sell) or bid (buy). Returns the ids of any trades made."""
        started = time.perf_counter()
        book = self._books.setdefault((interval, feeder), OrderBook())
        order = Order(participant, meter_id, side, wh, price)
        trade_ids = []
        for resting, matched_wh in book.place(order):
            buyer, seller = (order, resting) if side == "buy" else (resting, order)
            trade_id = f"T{len(self.trades) + 1:05d}"
            trade = {
                "trade_id": trade_id,
                "interval": interval,
                "buyer": buyer.participant,
                "buyer_meter": buyer.meter_id,
                "seller": seller.participant,
                "seller_meter": seller.meter_id,
                "wh": matched_wh,
                "price": resting.price,
            }
            self.trades[trade_id] = trade
            self.status[trade_id] = "matched"
            trade_ids.append(trade_id)
            self.bus.publish(Event("TradeMatched", trade))
        self.latency_ms.append((time.perf_counter() - started) * 1000)
        return trade_ids

    def close_interval(self, interval: int) -> None:
        for (start, _feeder), book in self._books.items():
            if start == interval:
                book.closed = True

    def _translate_meter_data(self, event: Event) -> None:

        participant = self._meter_owners.get(event.data["meter_id"])
        if participant:
            self.delivered[(participant, event.data["interval"])] = event.data["export_wh"]

    def _void_trade(self, event: Event) -> None:
        """Compensating action in the settlement saga: the buyer could not pay."""
        if self.status.get(event.data["trade_id"]) == "matched":
            self.status[event.data["trade_id"]] = "void"

    def _mark_settled(self, event: Event) -> None:
        if self.status.get(event.data["trade_id"]) == "matched":
            self.status[event.data["trade_id"]] = "settled"
