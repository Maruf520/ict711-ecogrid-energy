

from __future__ import annotations

from dataclasses import dataclass

from ecogrid.events import EventBus
from ecogrid.marketplace import MarketplaceService
from ecogrid.metering import MeteringService
from ecogrid.settlement import SettlementService


@dataclass
class EcoGrid:
    bus: EventBus
    metering: MeteringService
    marketplace: MarketplaceService
    settlement: SettlementService


def build(meter_owners: dict[str, str], duplicate_every: int = 0) -> EcoGrid:
    # meter_owners maps meter id -> participant
    bus = EventBus(duplicate_every)
    return EcoGrid(
        bus,
        MeteringService(bus),
        MarketplaceService(bus, meter_owners),
        SettlementService(bus),
    )
