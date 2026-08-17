from PIL import Image
import pytest
from types import SimpleNamespace

from supvan_t50 import BleDeviceInfo, BluetoothPrinter, Printer, PrintJob, PrintPage, UsbPrinter, preview_job, print_job
from supvan_t50.job_rendering import render_job_images
from supvan_t50.errors import DeviceError
from supvan_t50.status import PrinterStatus


class FakeTransport:
    def __init__(self): self.writes = []
    def connect(self): pass
    def close(self): pass
    def write(self, data): pass
    def read(self, size=512, timeout=2.0): return b""
    def write_chunked(self, data, chunk_size=512, delay=0.01): self.writes.append((data, chunk_size, delay))


class FakePrinter:
    def __init__(self, status: PrinterStatus):
        self.status = status
        self.jobs = []
        self.closed = False

    def get_status(self): return self.status
    def print(self, job): self.jobs.append(job); return True
    def close(self): self.closed = True
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def test_common_printer_protocol_is_structural() -> None:
    fake = FakePrinter(PrinterStatus.from_state(0))
    assert isinstance(fake, Printer)
    assert isinstance(BluetoothPrinter(FakeTransport()), Printer)


def test_common_status_is_not_ready_while_printing_or_busy() -> None:
    assert not PrinterStatus.from_state(0, printing=True).ready
    assert not PrinterStatus.from_state(0, busy=True).ready
    assert not PrinterStatus.from_state(0, second_device_busy=True).ready


def test_bluetooth_printer_high_level_factories(monkeypatch) -> None:
    device = BleDeviceInfo("T0123", "AA:BB:CC:DD:EE:FF", -42)
    monkeypatch.setattr("supvan_t50.bluetooth.BleTransport.discover_devices", lambda **kwargs: [device])

    assert BluetoothPrinter.discover("T0", timeout=2.0) == [device]

    by_device = BluetoothPrinter.from_device(device, timeout=7.0, print_timeout=60.0)
    assert by_device.transport.address == device.address
    assert by_device.timeout == 7.0
    assert by_device.print_timeout == 60.0

    by_address = BluetoothPrinter.from_address("AA", timeout=6.0, chunk_size=64)
    assert by_address.transport.address == "AA"
    assert by_address.transport.chunk_size == 64


def test_bluetooth_printer_from_name_hides_transport_layer(monkeypatch) -> None:
    transport = FakeTransport()
    calls = []

    def fake_from_name(name, **kwargs):
        calls.append((name, kwargs))
        return transport

    monkeypatch.setattr("supvan_t50.bluetooth.BleTransport.from_name", fake_from_name)
    printer = BluetoothPrinter.from_name("T0123", timeout=7.0, scan_timeout=3.0, chunk_size=20)
    assert printer.transport is transport
    assert calls == [("T0123", {"timeout": 7.0, "scan_timeout": 3.0, "chunk_size": 20})]


def test_usb_printer_exposes_common_contract_with_fake_backend() -> None:
    usb = object.__new__(UsbPrinter)
    usb._backend = FakePrinter(PrinterStatus.from_state(0))
    assert isinstance(usb, Printer)


def test_print_job_checks_common_status_before_dispatch() -> None:
    job = PrintJob([PrintPage()])
    ready = FakePrinter(PrinterStatus.from_state(0))
    assert print_job(ready, job) and ready.jobs == [job]
    bad = FakePrinter(PrinterStatus.from_state(2))
    with pytest.raises(DeviceError):
        print_job(bad, job)
    assert bad.jobs == []


def test_render_job_images_uses_shared_physical_page_order(monkeypatch) -> None:
    def fake_render(page: PrintPage) -> Image.Image:
        return Image.new("L", (1, 1), int(page.width))

    monkeypatch.setattr("supvan_t50.job_rendering.render_page", fake_render)
    pages = [PrintPage(width=10, repeat=2), PrintPage(width=20, repeat=1)]
    job = PrintJob(pages, settings=__import__("supvan_t50").PrintSettings(copies=2, one_by_one=True))
    assert [image.getpixel((0, 0)) for image in render_job_images(job)] == [10, 10, 20, 10, 10, 20]

    job.settings.one_by_one = False
    assert [image.getpixel((0, 0)) for image in render_job_images(job)] == [10, 10, 10, 10, 20, 20]


def test_preview_job_returns_and_saves_single_label(tmp_path) -> None:
    job = PrintJob([PrintPage(width=10, height=5)])
    output = tmp_path / "label.png"
    images = preview_job(job, output)
    assert len(images) == 1
    assert images[0].size == (80, 40)
    assert output.exists()


def test_preview_job_numbers_multiple_physical_labels(tmp_path) -> None:
    job = PrintJob([PrintPage(width=10, height=5, repeat=2)])
    images = preview_job(job, tmp_path / "label.png")
    assert len(images) == 2
    assert (tmp_path / "label-1.png").exists()
    assert (tmp_path / "label-2.png").exists()


def test_bluetooth_page_count_waits_for_idle_before_next_page(monkeypatch) -> None:
    printer = BluetoothPrinter(FakeTransport())
    statuses = [
        PrinterStatus.from_state(0, printed_pages=0),
        PrinterStatus.from_state(0, printed_pages=1, busy=True, printing=True),
        PrinterStatus.from_state(0, printed_pages=1, busy=False, printing=False),
    ]
    monkeypatch.setattr(printer, "get_status", lambda timeout=None: statuses.pop(0))
    monkeypatch.setattr(printer, "_exchange", lambda *args, **kwargs: b"")
    monkeypatch.setattr(printer, "_send_compressed", lambda data: None)
    monkeypatch.setattr("supvan_t50.bluetooth.image_frames", lambda *args, **kwargs: [b"frame"])
    monkeypatch.setattr(
        "supvan_t50.bluetooth.compressed_batches",
        lambda frames: [SimpleNamespace(data=b"batch")],
    )
    monkeypatch.setattr("supvan_t50.bluetooth.time.sleep", lambda delay: None)

    settings = __import__("supvan_t50").PrintSettings(
        material_width=48, material_height=30, gap=3
    )
    assert printer._print_images_once([Image.new("L", (1, 1))], settings)
    assert statuses == []
