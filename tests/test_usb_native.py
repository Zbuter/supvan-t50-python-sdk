import lzma

from PIL import Image

from supvan_t50 import DrawObject, PrintJob, PrintPage, PrintSettings, UsbPrinter
from supvan_t50.usb_native import NativeUsbPrinter
from supvan_t50.usb_protocol import (
    CMD_BUFFER_FULL,
    CMD_CHECK_DEVICE,
    CMD_INQUIRY_STATUS,
    CMD_RETURN_MAT,
    CMD_SET_MEDIA,
    CMD_START_PRINT,
    CMD_TRANSFER_DATA,
    T50StatusFlags,
    build_media_config,
    build_vendor_request,
    image_to_frames,
    lzma_compress_frames,
    prepare_t50_image,
)
from supvan_t50.usb_transport import PyUsbTransport


def _status(*, printing: bool = False, pages: int = 0, busy: bool = False) -> bytes:
    data = bytearray(8)
    if busy:
        data[1] |= 0x04
    if printing:
        data[2] |= 0x40
    data[4] = pages & 0xFF
    data[5] = pages >> 8
    return bytes(data)


def test_vendor_request_matches_decompiled_dll() -> None:
    assert build_vendor_request(0x11, 0) == bytes.fromhex("c040000011000800")
    assert build_vendor_request(0x10, 0x1234, 0x003C) == bytes.fromhex("c040123410000800003c")


def test_media_configuration_offsets() -> None:
    data = build_media_config(paper_type=5, gap=7, height=30)
    assert len(data) == 80
    assert data[26] == 5
    assert data[28] == 30
    assert data[30] == 7


def test_status_bit_dictionary() -> None:
    raw = bytearray(8)
    raw[0] = 0x47
    raw[1] = 0x0C
    raw[2] = 0x48
    raw[3] = 0x81
    raw[4:6] = (0x34, 0x12)
    status = T50StatusFlags.parse(raw)
    assert status.buffer_full
    assert status.media_read_write_error
    assert status.media_empty
    assert status.low_battery
    assert status.device_busy
    assert status.head_overheat
    assert status.cover_open
    assert status.printing
    assert status.media_not_installed
    assert status.charging
    assert status.printed_pages == 0x1234


def test_frame_layout_and_lzma_container() -> None:
    image = Image.new("L", (384, 10), 255)
    image.putpixel((0, 0), 0)
    frames = image_to_frames(image, density=4, paper_type=1, last_job_page=True)
    assert len(frames) == 1
    frame = frames[0]
    assert len(frame) == 4096
    assert frame[2] == 0x0E  # page start + page end + print end
    assert frame[3] == 0x50  # paper type 1 + density 4
    assert frame[4:7] == bytes((10, 0, 48))
    assert frame[14] & 0x01
    compressed = lzma_compress_frames(frames)
    assert compressed[:5] == bytes.fromhex("5d00200000")
    assert int.from_bytes(compressed[5:13], "little") == 4096
    assert lzma.decompress(compressed, format=lzma.FORMAT_ALONE) == frame


def test_prepare_image_reproduces_full_head_and_mirror() -> None:
    image = Image.new("L", (16, 2), 255)
    image.putpixel((0, 0), 0)
    prepared = prepare_t50_image(image, max_dots=16)
    assert prepared.size == (16, 2)
    assert prepared.getpixel((15, 0)) < 240


def test_prepare_image_centers_without_stretching_or_clipping() -> None:
    image = Image.new("L", (320, 2), 255)
    image.putpixel((0, 0), 0)
    image.putpixel((319, 0), 0)
    prepared = prepare_t50_image(image, max_dots=384)
    assert prepared.size == (384, 2)
    # Mirroring swaps the centered content edges, which remain 32 dots from
    # either print-head boundary instead of being stretched and clipped.
    assert prepared.getpixel((32, 0)) == 0
    assert prepared.getpixel((351, 0)) == 0
    assert prepared.getpixel((31, 0)) == 255
    assert prepared.getpixel((352, 0)) == 255


def test_usb_threshold_matches_ble_antialiasing_weight() -> None:
    image = Image.new("L", (8, 1), 255)
    image.putpixel((0, 0), 200)
    frame = image_to_frames(image, density=3, paper_type=1, last_job_page=True)[0]
    assert frame[14] & 0x01 == 0


def test_hid_report_chunking_includes_vendor_zero_tail() -> None:
    transport = PyUsbTransport(device=object())
    transport._opened = True
    reports: list[bytes] = []
    transport._write_report = reports.append  # type: ignore[method-assign]
    transport.write_payload(bytes(range(64)))
    assert len(reports) == 2
    assert reports[0] == bytes(range(64))
    assert reports[1] == bytes(64)


class _FakeEndpoint:
    wMaxPacketSize = 64

    def __init__(self, reports: list[bytes]):
        self.reports = reports

    def read(self, size: int, timeout: int) -> bytes:
        return self.reports.pop(0)


def test_hid_input_discards_on_wire_prefix() -> None:
    transport = PyUsbTransport(device=object())
    transport._opened = True
    transport.endpoint_in = _FakeEndpoint([b"\xaaABCDEFGH" + bytes(55)])
    assert transport.read_payload(8) == b"ABCDEFGH"


class _FakeTransport:
    def __init__(self) -> None:
        self.commands: list[tuple[int, int, int | None]] = []
        self.payloads: list[bytes] = []
        self.inquiry_count = 0

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def command(self, command: int, value1: int = 0, value2: int | None = None) -> bytes:
        self.commands.append((command, value1, value2))
        if command == CMD_INQUIRY_STATUS:
            self.inquiry_count += 1
            return _status(pages=1) if self.inquiry_count >= 2 else _status()
        if command in (CMD_START_PRINT, CMD_BUFFER_FULL):
            return _status(printing=True)
        return _status()

    def command_payload(
        self, command: int, length: int, value1: int = 0, value2: int | None = None
    ) -> bytes:
        self.commands.append((command, value1, value2))
        frame = bytes.fromhex(
            "7e5a3900000355306d0a000000000000000000000000001d"
            "f1e4ef131080cb47fa3227048d07b11501281e03fa000000"
            "a406b00419080c11271e000001"
        )
        # USB BulkRead returns the 57-byte response body without the four-byte
        # BLE framing prefix.
        return frame[4:4 + length]

    def write_payload(self, data: bytes) -> None:
        self.payloads.append(data)

    def read_payload(self, length: int, *, timeout: float | None = None) -> bytes:
        return bytes(length)


def test_native_print_state_machine() -> None:
    transport = _FakeTransport()
    job = PrintJob(
        settings=PrintSettings(material_width=48, material_height=8, speed=40),
        pages=[PrintPage(width=48, height=8, objects=[DrawObject(1, 1, 20, 4, "USB")])],
    )
    printer = UsbPrinter("usb:1820:2073:*:*", transport=transport, timeout=1)
    assert printer.print(job)
    command_ids = [item[0] for item in transport.commands]
    assert command_ids[:5] == [CMD_RETURN_MAT, CMD_SET_MEDIA, CMD_CHECK_DEVICE, CMD_INQUIRY_STATUS, CMD_START_PRINT]
    assert CMD_TRANSFER_DATA in command_ids
    assert CMD_BUFFER_FULL in command_ids
    assert len(transport.payloads[0]) == 80
    assert len(transport.payloads[1]) <= 4096


def test_native_print_skips_optional_label_box_read_when_media_is_explicit() -> None:
    transport = _FakeTransport()
    job = PrintJob(
        settings=PrintSettings(material_width=48, material_height=8, gap=3, speed=40),
        pages=[PrintPage(width=48, height=8, objects=[DrawObject(1, 1, 20, 4, "USB")])],
    )
    assert UsbPrinter(transport=transport, timeout=1).print(job)
    assert transport.commands[0][0] == CMD_SET_MEDIA


def test_native_status_api() -> None:
    transport = _FakeTransport()
    printer = NativeUsbPrinter(transport=transport)
    status = printer.get_status()
    assert status.ready


def test_usb_read_label_box_uses_same_model_as_ble() -> None:
    transport = _FakeTransport()
    printer = UsbPrinter(transport=transport)

    box = printer.read_label_box()

    assert (box.width, box.height, box.gap) == (40, 30, 3)
    assert transport.commands[0][0] == CMD_RETURN_MAT


def test_usb_render_keeps_each_page_design_size() -> None:
    job = PrintJob(
        pages=[PrintPage(width=20, height=10), PrintPage(width=40, height=30)],
        settings=PrintSettings(material_width=48, material_height=30, gap=3),
    )

    images = NativeUsbPrinter._render_images(job)

    assert [image.size for image in images] == [(160, 80), (320, 240)]
