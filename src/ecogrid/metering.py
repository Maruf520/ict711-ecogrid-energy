from __future__ import annotations

from collections import Counter
from typing import Any

from ecogrid.events import Event, EventBus, interval_start

# Upper bound for a house

MAX_POWER_W = 30_000

Reading = dict[str, Any]  # keys: meter_id, ts, import_wh, export_wh


class MeteringService:
    def __init__(self, bus: EventBus) -> None:
        self.bus = bus
        self.rejected: Counter[str] = Counter()  
        self._windows: dict[tuple[str, int], dict[str, Any]] = {}
        self._latest: dict[str, Reading] = {}  
        self._baseline: dict[str, Reading] = {}  
        self._closed: set[int] = set()

    def ingest(self, raw: dict[str, Any]) -> bool:
        """Validate a reading and file it under its interval. False = rejected."""
        reading = parse(raw)
        reason = self.check(reading) if reading else "malformed"
        if reason:
            self.rejected[reason] += 1
            return False

        key = (reading["meter_id"], interval_start(reading["ts"]))
        window = self._windows.setdefault(key, {"first": reading, "last": reading, "seen": set()})
        window["seen"].add(reading["ts"])
        
        if reading["ts"] < window["first"]["ts"]:
            window["first"] = reading
        if reading["ts"] > window["last"]["ts"]:
            window["last"] = reading

        latest = self._latest.get(reading["meter_id"])
        if latest and interval_start(latest["ts"]) < key[1]:
            # We've just crossed into a new interval.
            self._baseline.setdefault(reading["meter_id"], latest)
        if latest is None or reading["ts"] > latest["ts"]:
            self._latest[reading["meter_id"]] = reading
        return True

    def check(self, reading: Reading) -> str | None:
        """Returns the reason a reading is rejected, or None if it's OK."""
        start = interval_start(reading["ts"])
        if start in self._closed:
            # finalised and probably paid out. Not a big deal though - the next

            return "too_late"

        window = self._windows.get((reading["meter_id"], start))
        if window and reading["ts"] in window["seen"]:
            return "duplicate"

        latest = self._latest.get(reading["meter_id"])
        if latest is None:
            return None  # first reading from this meter

        seconds = reading["ts"] - latest["ts"]
        more_import = reading["import_wh"] - latest["import_wh"]
        more_export = reading["export_wh"] - latest["export_wh"]

        if seconds < 0:
            # Older than our newest reading. 
            return "inconsistent_late" if max(more_import, more_export) > 0 else None
        if min(more_import, more_export) < 0:
            return "register_went_down"
        if max(more_import, more_export) > MAX_POWER_W * seconds / 3600:
            return "impossible_jump"
        return None

    def finalise(self, interval: int) -> int:
        """Close the interval and publish one event per meter. Returns how many were sent."""
        self._closed.add(interval)
        keys = sorted(k for k in self._windows if k[1] == interval)
        for meter_id, start in keys:
            window = self._windows.pop((meter_id, start))
            # brand new meter with no earlier reading 
            base = self._baseline.get(meter_id, window["first"])
            self._baseline[meter_id] = window["last"]  # next interval starts from here
            self.bus.publish(
                Event(
                    "IntervalReadingFinalised",
                    {
                        "meter_id": meter_id,
                        "interval": start,
                        "import_wh": window["last"]["import_wh"] - base["import_wh"],
                        "export_wh": window["last"]["export_wh"] - base["export_wh"],
                        "readings": len(window["seen"]),
                    },
                )
            )
        return len(keys)


def parse(raw: dict[str, Any]) -> Reading | None:
    # Meters send all sorts of strings for numbers, missing fields.
    
    try:
        return {
            "meter_id": str(raw["meter_id"]),
            "ts": int(raw["ts"]),
            "import_wh": int(raw["import_wh"]),
            "export_wh": int(raw["export_wh"]),
        }
    except (KeyError, TypeError, ValueError):
        return None
