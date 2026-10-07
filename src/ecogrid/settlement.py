

from __future__ import annotations

from collections import defaultdict

from ecogrid.events import INTERVAL_SECONDS, Event, EventBus

FEE_PERCENT = 5
LATE_DATA_SECONDS = 900  # 15 min grace period for meter data 
EXTERNAL = "bank"  


class NotEnoughMoney(ValueError):
    pass


class Ledger:


    def __init__(self) -> None:
        self.balances: dict[str, int] = defaultdict(int)
        self.entries: list[tuple[str, str, int]] = []
        self._done: set[str] = set()

    def post(self, tx_id: str, amounts: dict[str, int]) -> bool:
        if tx_id in self._done:
            return False  # seen it already, quietly ignore
        if sum(amounts.values()) != 0:
            raise ValueError(f"{tx_id} does not balance")
        # check every account BEFORE changing anything, so a failed tx leaves no half-applied entries
        for account, amount in amounts.items():
            if account != EXTERNAL and self.balances[account] + amount < 0:
                raise NotEnoughMoney(account)
        for account, amount in amounts.items():
            self.balances[account] += amount
            self.entries.append((tx_id, account, amount))
        self._done.add(tx_id)
        return True

    def total(self) -> int:

        return sum(self.balances.values())


class SettlementService:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.ledger = Ledger()
        self.sagas: dict[str, dict] = {}  # trade_id -> saga state

        self._energy: dict[tuple[str, int], dict[str, int]] = {}
        self._processed: set[str] = set()
        bus.subscribe("TradeMatched", self._once(self._start_saga))
        bus.subscribe("IntervalReadingFinalised", self._once(self._record_meter_data))

    def top_up(self, participant: str, cents: int, payment_ref: str) -> None:

        amount = cents * 1000
        self.ledger.post(f"topup:{payment_ref}", {EXTERNAL: -amount, f"wallet:{participant}": amount})

    def settle_overdue(self, now: int) -> int:
        """Provisionally settle trades still waiting on meter data past the grace period."""
        overdue = [
            s
            for s in self.sagas.values()
            if s["state"] == "reserved"
            and now >= s["trade"]["interval"] + INTERVAL_SECONDS + LATE_DATA_SECONDS
        ]
        for saga in overdue:

            self._settle(saga, saga["trade"]["wh"], provisional=True)
        return len(overdue)

    def _once(self, handler):

        def wrapper(event: Event) -> None:
            if event.id not in self._processed:
                handler(event)
                self._processed.add(event.id)

        return wrapper

    def _start_saga(self, event: Event) -> None:
        trade = event.data
        reserve = trade["wh"] * trade["price"]  # millicents, see note at the top
        saga = {"trade": trade, "state": "started", "reserved": reserve, "delivered_wh": None}
        self.sagas[trade["trade_id"]] = saga
        buyer = trade["buyer"]
        try:
            self.ledger.post(
                f"reserve:{trade['trade_id']}",
                {f"wallet:{buyer}": -reserve, f"held:{buyer}": reserve},
            )
        except NotEnoughMoney:
            saga["state"] = "failed"
            self.bus.publish(
                Event("TradeReservationFailed", {"trade_id": trade["trade_id"], "reason": "not enough money"})
            )
            return
        saga["state"] = "reserved"
        self._try_settle(saga)  # meter data might already be here

    def _record_meter_data(self, event: Event) -> None:
        data = event.data
        self._energy[(data["meter_id"], data["interval"])] = {
            "import": data["import_wh"],
            "export": data["export_wh"],
        }
        # new meter data could unblock any trade that's waiting
        for saga in list(self.sagas.values()):
            if saga["state"] == "reserved":
                self._try_settle(saga)

    def _try_settle(self, saga: dict) -> None:
        trade = saga["trade"]
        seller = self._energy.get((trade["seller_meter"], trade["interval"]))
        buyer = self._energy.get((trade["buyer_meter"], trade["interval"]))
        if seller is None or buyer is None:
            return  # still waiting on one of the meters

        delivered = max(0, min(trade["wh"], seller["export"], buyer["import"]))
        seller["export"] -= delivered
        buyer["import"] -= delivered
        self._settle(saga, delivered, provisional=False)

    def _settle(self, saga: dict, delivered_wh: int, provisional: bool) -> None:
        trade = saga["trade"]
        value = delivered_wh * trade["price"]  # millicents
        fee = (value * FEE_PERCENT + 50) // 100  

        self.ledger.post(
            f"settle:{trade['trade_id']}",
            {
                f"held:{trade['buyer']}": -saga["reserved"],
                f"earnings:{trade['seller']}": value - fee,
                "fees": fee,
                f"wallet:{trade['buyer']}": saga["reserved"] - value,
            },
        )
        saga["state"] = "provisional" if provisional else "settled"
        saga["delivered_wh"] = delivered_wh
        self.bus.publish(
            Event(
                "SettlementCompleted",
                {
                    "trade_id": trade["trade_id"],
                    "delivered_wh": delivered_wh,
                    "seller_paid_millicents": value - fee,
                    "provisional": provisional,
                },
            )
        )
