import asyncio
import queue

import pytest

from supvan_t50 import BleDeviceInfo, BleTransport
from supvan_t50.errors import CommunicationError


def test_ble_transport_verified_uuid_contract() -> None:
    assert BleTransport.SERVICE_UUID == "0000e0ff-3c17-d293-8e48-14fe2e4da212"
    assert BleTransport.WRITE_UUID.endswith("ffe9-0000-1000-8000-00805f9b34fb")
    assert BleTransport.NOTIFY_UUID.endswith("ffe1-0000-1000-8000-00805f9b34fb")
    assert BleTransport.BULK_NOTIFY_UUID.endswith("ffea-0000-1000-8000-00805f9b34fb")
    assert BleTransport.bulk_ack_required is False
    assert BleTransport.separate_physical_pages is True


def test_ble_device_info_and_transport_factory() -> None:
    device = BleDeviceInfo("T0123", "AA:BB:CC:DD:EE:FF", -42)
    transport = BleTransport.from_device(device, timeout=7.0, chunk_size=20)
    assert transport.address == device.address
    assert transport.timeout == 7.0
    assert transport.chunk_size == 20


def test_ble_transport_factory_from_address() -> None:
    transport = BleTransport.from_address(" AA:BB:CC:DD:EE:FF ", timeout=6.0, chunk_size=64)
    assert transport.address == "AA:BB:CC:DD:EE:FF"
    assert transport.timeout == 6.0
    assert transport.chunk_size == 64


def test_ble_transport_factory_from_name_uses_exact_match(monkeypatch) -> None:
    devices = [
        BleDeviceInfo("T0123 Pro", "AA", -20),
        BleDeviceInfo("T0123", "BB", -30),
        BleDeviceInfo("t0123", "CC", -40),
    ]
    calls = []

    def fake_discover(cls, name_prefix, timeout):
        calls.append((name_prefix, timeout))
        return devices

    monkeypatch.setattr(BleTransport, "discover_devices", classmethod(fake_discover))
    transport = BleTransport.from_name("T0123", timeout=7.0, scan_timeout=3.0, chunk_size=20)
    assert calls == [("T0123", 3.0)]
    assert transport.address == "BB"
    assert transport.timeout == 7.0
    assert transport.chunk_size == 20


def test_ble_transport_factory_from_name_reports_missing_device(monkeypatch) -> None:
    monkeypatch.setattr(BleTransport, "discover_devices", classmethod(lambda cls, **kwargs: []))
    with pytest.raises(CommunicationError, match="未发现名为"):
        BleTransport.from_name("T0999")


@pytest.mark.parametrize(
    ("factory", "value"),
    [(BleTransport.from_address, ""), (BleTransport.from_name, "   ")],
)
def test_ble_transport_named_factories_reject_empty_values(factory, value) -> None:
    with pytest.raises(ValueError, match="不能为空"):
        factory(value)


def test_ble_transport_factory_rejects_untyped_device() -> None:
    with pytest.raises(TypeError, match="BleDeviceInfo"):
        BleTransport.from_device(("T0123", "AA", -42))


def test_ble_notification_queue_preserves_fragments_and_read_sizes() -> None:
    transport = BleTransport("AA:BB:CC:DD:EE:FF")
    transport._client = object()
    transport._on_notify(None, bytearray(b"abc"))
    transport._on_notify(None, bytearray(b"defgh"))
    assert transport.read(2, 0.01) == b"ab"
    assert transport.read(4, 0.01) == b"c"
    assert transport.read(4, 0.01) == b"defg"
    assert transport.read(4, 0.01) == b"h"


def test_ble_read_timeout_is_empty_and_disconnected_read_is_typed() -> None:
    transport = BleTransport("AA:BB:CC:DD:EE:FF")
    with pytest.raises(CommunicationError, match="尚未连接"):
        transport.read(timeout=0.001)
    transport._client = object()
    assert transport.read(timeout=0.001) == b""


def test_ble_close_clears_buffer_and_queue_without_connection() -> None:
    transport = BleTransport("AA:BB:CC:DD:EE:FF")
    transport._buffer.extend(b"secret")
    transport._received.put(b"queued")
    transport.close()
    assert transport._buffer == b""
    with pytest.raises(queue.Empty):
        transport._received.get_nowait()


def test_ble_control_frames_are_written_to_ffe9() -> None:
    class FakeClient:
        def __init__(self):
            self.writes = []

        async def write_gatt_char(self, characteristic, data, response=False):
            self.writes.append((characteristic, bytes(data), response))

    transport = BleTransport("AA:BB:CC:DD:EE:FF")
    client = FakeClient()
    transport._client = client

    def submit(coroutine, timeout=None):
        return asyncio.run(coroutine)

    transport._submit = submit
    transport.write(b"control-frame")
    assert client.writes == [(transport.WRITE_UUID, b"control-frame", False)]
