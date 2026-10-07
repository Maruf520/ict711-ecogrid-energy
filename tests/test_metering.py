from ecogrid.demo import readings
from ecogrid.events import EventBus
from ecogrid.metering import MeteringService

T0 = 1_789_437_600



def setup():
    bus = EventBus()
    return bus, MeteringService(bus)


def finalised(bus):
    return {e.data["meter_id"]: e.data for e in bus.published}


def test_one_event_per_meter_per_interval_with_correct_energy():
    bus, metering = setup()
    for r in readings("M1", T0, 300, export_w=3600) + readings("M2", T0, 300, import_w=1200):
        assert metering.ingest(r)
    assert metering.finalise(T0) == 2
    data = finalised(bus)
    assert data["M1"]["export_wh"] == 300  
    assert data["M2"]["import_wh"] == 100
    assert data["M1"]["readings"] == 30  


def test_lost_messages_do_not_lose_energy():
    # this is the whole point of using cumulative registers
    bus, metering = setup()
    for r in readings("M1", T0, 300, export_w=3600)[::3]:  
        metering.ingest(r)
    metering.finalise(T0)
    assert finalised(bus)["M1"]["export_wh"] == 300


def test_bad_readings_are_rejected_and_counted():
    _, metering = setup()
    good = readings("M1", T0, 60, export_w=3600)
    for r in good:
        metering.ingest(r)
    last = good[-1]
    assert not metering.ingest(dict(last)) 
    assert not metering.ingest({"meter_id": "M1", "ts": "garbage"})  
    assert not metering.ingest(dict(last, ts=last["ts"] + 10, export_wh=0))  
    assert not metering.ingest(dict(last, ts=last["ts"] + 10, export_wh=99_999))  
    assert not metering.ingest(dict(good[2], ts=good[2]["ts"] + 1, export_wh=99))
    assert metering.rejected == {
        "duplicate": 1,
        "malformed": 1,
        "register_went_down": 1,
        "impossible_jump": 1,
        "inconsistent_late": 1,
    }


def test_readings_for_a_closed_interval_are_too_late():
    _, metering = setup()
    batch = readings("M1", T0, 300, export_w=3600)
    for r in batch:
        metering.ingest(r)
    metering.finalise(T0)
    assert not metering.ingest(dict(batch[5], ts=batch[5]["ts"] + 1))
    assert metering.rejected["too_late"] == 1
