from __future__ import annotations

import lzma
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

from PIL import Image, ImageOps

from .models import LabelBoxInfo, PaperType, PrintSettings
from .status import PrinterState, PrinterStatus

# Bluetooth print-frame size used by the verified T0 BLE path.
FRAME_SIZE = 4096
PRINT_WIDTH_DOTS = 384
BYTES_PER_LINE = PRINT_WIDTH_DOTS // 8
MAX_LINES_PER_FRAME = (FRAME_SIZE - 14) // BYTES_PER_LINE
PARAMETER_TEMPLATE = bytes([
    48, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    121, 1, 1, 91, 235, 93, 155, 179, 48, 117, 1, 50, 50, 1, 3, 0,
    224, 1, 0, 0, 164, 6, 176, 4, 23, 8, 17, 11, 48, 57, 0, 0,
    135, 220, 151, 205, 1, 224, 159, 64, 149, 68, 77, 133, 236, 167, 205, 0,
    0, 0, 0, 0, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
])


def _checksum(data: bytes) -> tuple[int, int]:
    value = sum(data) & 0xFFFF
    return value & 0xFF, value >> 8


def _uint(value: int, bits: int, name: str) -> int:
    if not 0 <= value < (1 << bits):
        raise ValueError(f"{name} must fit uint{bits}")
    return value


def build_r1(command: int, value: int = 0) -> bytes:
    _uint(command, 8, "command")
    _uint(value, 16, "value")
    frame = bytearray(16)
    frame[:8] = bytes((0x7E, 0x5A, 12, 0, 0x10, 1, 0xAA, command & 0xFF))
    frame[10:16] = bytes((0, 1, value & 0xFF, value >> 8, 0, 0))
    frame[8], frame[9] = _checksum(frame[10:])
    return bytes(frame)


def build_r2(command: int, transfer_size: int, packet_count: int) -> bytes:
    _uint(command, 8, "command")
    _uint(transfer_size, 16, "transfer_size")
    _uint(packet_count, 16, "packet_count")
    frame = bytearray(16)
    frame[:8] = bytes((0x7E, 0x5A, 12, 0, 0x10, 1, 0xAA, command))
    frame[10:16] = bytes((0, 1, transfer_size & 0xFF, transfer_size >> 8, packet_count & 0xFF, packet_count >> 8))
    frame[8], frame[9] = _checksum(frame[10:])
    return bytes(frame)


def build_query(command: int, value: int = 0, value2: int = 0) -> bytes:
    """Compatibility builder; value2 selects the recovered R2 layout."""
    return build_r2(command, value, value2) if value2 else build_r1(command, value)


def build_u32_query(command: int, value: int, trailing: int) -> bytes:
    _uint(command, 8, "command")
    _uint(value, 32, "value")
    _uint(trailing, 8, "trailing")
    frame = bytearray(17)
    frame[:8] = bytes((0x7E, 0x5A, 13, 0, 0x10, 1, 0xAA, command & 0xFF))
    frame[10:17] = bytes((0, 1, value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF, (value >> 24) & 0xFF, trailing & 0xFF))
    frame[8], frame[9] = _checksum(frame[10:])
    return bytes(frame)


def build_data_query(command: int, data: bytes) -> bytes:
    frame = bytearray(len(data) + 12)
    payload_length = len(data) + 8
    frame[:8] = bytes((0x7E, 0x5A, payload_length & 0xFF, payload_length >> 8, 0x10, 1, 0xAA, command & 0xFF))
    frame[10:12] = b"\x00\x01"
    frame[12:] = data
    frame[8], frame[9] = _checksum(frame[10:])
    return bytes(frame)


def parse_response(frame: bytes, expected_command: int | None = None) -> bytes:
    if len(frame) < 12 or frame[:2] != b"\x7e\x5a":
        raise ValueError("invalid SUPVAN response frame")
    declared = int.from_bytes(frame[2:4], "little") + 4
    if declared != len(frame):
        raise ValueError(f"response length mismatch: declared {declared}, received {len(frame)}")
    if expected_command is not None and frame[7] != expected_command:
        raise ValueError(f"unexpected response command 0x{frame[7]:02X}")
    expected = frame[8] | (frame[9] << 8)
    if expected != (sum(frame[10:]) & 0xFFFF):
        raise ValueError("response checksum mismatch")
    return frame


def parse_status(frame: bytes) -> PrinterStatus:
    parse_response(frame, 0x11)
    if len(frame) < 26:
        raise ValueError("status response is shorter than verified fields")
    main0, main1, fixed0, fixed1, count_lo, count_hi = frame[14:20]
    state = PrinterState.READY
    if main1 & 0x08:
        state = PrinterState.HEAD_OVERHEAT
    elif fixed0 & 0x08:
        state = PrinterState.COVER_OPEN
    elif main0 & 0x10:
        state = PrinterState.MEDIA_NOT_INSTALLED
    elif main0 & 0x20:
        state = PrinterState.MEDIA_LOW
    elif main0 & 0x02:
        state = PrinterState.MEDIA_NOT_DETECTED
    elif main0 & 0x08:
        state = PrinterState.MEDIA_UNRECOGNIZED
    elif main0 & 0x04:
        state = PrinterState.MEDIA_EMPTY
    elif main0 & 0x40:
        state = PrinterState.BATTERY_LOW
    flags = bytes(frame[14:18])
    temperature = int.from_bytes(frame[22:24], "little", signed=True) / 10.0
    voltage = int.from_bytes(frame[24:26], "little") / 1000.0
    return PrinterStatus.from_state(
        state, printed_pages=count_lo | (count_hi << 8), raw=frame,
        raw_flags=flags, temperature_c=temperature, voltage_v=voltage,
        buffer_full=bool(main0 & 0x01), label_read_write_error=bool(main0 & 0x02),
        media_empty=bool(main0 & 0x04), media_unrecognized=bool(main0 & 0x08),
        media_not_installed=bool(main0 & 0x10), battery_low=bool(main0 & 0x40),
        busy=bool(main1 & 0x04), cover_open=bool(fixed0 & 0x08),
        usb_inserted=bool(fixed0 & 0x10), printing=bool(fixed0 & 0x40),
        second_device_busy=bool(fixed0 & 0x80), label_not_installed=bool(fixed1 & 0x01),
        charging=bool(fixed1 & 0x80),
    )


def _signed_byte(value: int) -> int:
    return value - 256 if value >= 128 else value


def parse_label_box_data(data: bytes) -> LabelBoxInfo:
    """解析包含 0x1D 标记的标签盒数据块。"""
    marker = data.find(b"\x1d")
    data_start = marker + 1
    if marker < 0 or len(data) < data_start + 35:
        raise ValueError("label-box response is shorter than verified fields")
    raw_width = data[data_start + 17]
    raw_height = data[data_start + 18]
    raw_gap = _signed_byte(data[data_start + 19])
    time_parts = tuple(_signed_byte(value) for value in data[data_start + 29:data_start + 35])
    timestamp_digits = "".join(f"0{value}" if value < 10 else str(value) for value in time_parts)
    return LabelBoxInfo(
        uuid_hex=data[data_start:data_start + 7].hex().upper(),
        code_hex=data[data_start + 7:data_start + 15].hex().upper(),
        serial_number=int.from_bytes(data[data_start + 15:data_start + 17], "little"),
        type_code=data[data_start + 17],
        raw_height=raw_height, height=min(raw_height, 50), width=raw_width,
        raw_gap=raw_gap, gap=raw_gap if raw_gap <= 8 else 3,
        remaining=int.from_bytes(data[data_start + 20:data_start + 24], "little"),
        template_5mm=int.from_bytes(data[data_start + 24:data_start + 26], "little"),
        template_40mm=int.from_bytes(data[data_start + 26:data_start + 28], "little"),
        timestamp_digits=timestamp_digits, raw=data,
    )


def parse_label_box(frame: bytes) -> LabelBoxInfo:
    """校验并解析 BLE 标签盒响应帧。"""
    parse_response(frame, 0x30)
    return parse_label_box_data(frame)


def build_print_bulk_packet(packet_index: int, packet_count: int, data: bytes) -> bytes:
    _uint(packet_index, 8, "packet_index")
    _uint(packet_count, 8, "packet_count")
    if len(data) > 500:
        raise ValueError("print bulk payload exceeds 500 bytes")
    packet = bytearray(506)
    packet[:2] = b"\xAA\xBB"
    packet[4:6] = bytes((packet_index, packet_count))
    packet[6:6 + len(data)] = data
    packet[2:4] = (sum(packet[4:]) & 0xFFFF).to_bytes(2, "little")
    return bytes(packet)


def build_bulk_outer_512(inner: bytes) -> bytes:
    if len(inner) != 506:
        raise ValueError("512-byte bulk outer frame requires a 506-byte inner packet")
    return b"\x7E\x5A\xFC\x01\x10\x02" + inner


def make_parameter_block(settings: PrintSettings) -> bytes:
    block = bytearray(PARAMETER_TEMPLATE)
    block[26] = int(settings.paper_type)
    block[27] = settings.material_width & 0xFF
    block[28] = settings.material_height & 0xFF
    block[30] = settings.gap & 0xFF
    block[31] = settings.tail_length & 0xFF
    return bytes(block)


def _prepare_canvas(image: Image.Image, settings: PrintSettings) -> Image.Image:
    height = settings.material_height * 8
    canvas = Image.new("L", (PRINT_WIDTH_DOTS, height), 255)
    source = ImageOps.exif_transpose(image).convert("L")
    rotations = {0: 0, 1: 90, 2: 180, 3: 270, 4: 360}
    angle = rotations[settings.rotate]
    if angle % 360:
        source = source.rotate(-angle, expand=True, fillcolor=255)
    scale = min(1.0, PRINT_WIDTH_DOTS / max(1, source.width))
    resized = source.resize((max(1, int(source.width * scale)), max(1, int(source.height * scale))), Image.Resampling.LANCZOS)
    x = ((PRINT_WIDTH_DOTS - resized.width) // 2) + settings.horizontal_offset
    y = ((height - resized.height) // 2) + settings.vertical_offset
    canvas.paste(resized, (x, y))
    return canvas.transpose(Image.Transpose.ROTATE_90)


def _pack_columns(image: Image.Image, threshold: int = 190) -> tuple[list[bytes], int, int]:
    pixels = image.load()
    packed: list[bytes] = []
    first = 0
    last = 0
    found = False
    for x in range(image.width):
        column = bytearray((image.height + 7) // 8)
        black = False
        for y in range(image.height):
            if pixels[x, y] < threshold:
                column[y // 8] |= 1 << (y % 8)
                black = True
        if black:
            if not found:
                first = x
                found = True
            last = x
        packed.append(bytes(column[:BYTES_PER_LINE]).ljust(BYTES_PER_LINE, b"\x00"))
    trailing = max(image.width - last - 4, 1)
    return packed, first, trailing


def _frame_flags(first: bool, last: bool, job_last: bool, density: int) -> bytes:
    byte0 = (0x02 if first else 0) | (0x04 if last else 0) | (0x08 if job_last else 0)
    byte1 = ((density & 0x0F) << 2) | 0x40
    return bytes((byte0, byte1))


def image_frames(image: Image.Image, settings: PrintSettings, *, job_last_page: bool = False) -> list[bytes]:
    prepared = _prepare_canvas(image, settings)
    columns, leading, trailing = _pack_columns(prepared)
    printable = max(0, len(columns) - leading - trailing)
    if printable == 0:
        printable = 1
    result: list[bytes] = []
    offset = 0
    while offset < printable:
        count = min(MAX_LINES_PER_FRAME, printable - offset)
        first = offset == 0
        last = offset + count >= printable
        frame = bytearray(FRAME_SIZE)
        frame[2:4] = _frame_flags(first, last, last and job_last_page, settings.density)
        frame[4:6] = count.to_bytes(2, "little")
        frame[6] = BYTES_PER_LINE
        if settings.paper_type == PaperType.BLACK_MARK:
            total_blank = leading + trailing
            if total_blank < 13:
                before, after = 12, 1
            elif leading < 12:
                before, after = 12, max(1, trailing - (12 - leading))
            elif trailing == 0:
                before, after = max(1, leading - 1), 1
            else:
                before, after = leading, trailing
        else:
            before, after = min(800, max(1, leading)), min(800, max(1, trailing))
        frame[8:10] = before.to_bytes(2, "little")
        frame[10:12] = after.to_bytes(2, "little")
        for index in range(count):
            frame[14 + index * BYTES_PER_LINE:14 + (index + 1) * BYTES_PER_LINE] = columns[leading + offset + index]
        used = 14 + count * BYTES_PER_LINE
        checksum = sum(frame[2:14])
        for boundary in range(255, used, 256):
            checksum += frame[boundary]
        frame[0:2] = (checksum & 0xFFFF).to_bytes(2, "little")
        result.append(bytes(frame))
        offset += count
    return result


def lzma_compress_frames(frames: Sequence[bytes]) -> bytes:
    raw = b"".join(frames)
    filters = [{
        "id": lzma.FILTER_LZMA1,
        "dict_size": 8192,
        "lc": 3,
        "lp": 0,
        "pb": 2,
        "mode": lzma.MODE_NORMAL,
        "nice_len": 128,
        "mf": lzma.MF_BT4,
    }]
    compressed = lzma.compress(raw, format=lzma.FORMAT_RAW, filters=filters)
    return b"\x5d\x00\x20\x00\x00" + len(raw).to_bytes(8, "little") + compressed


@dataclass(frozen=True)
class CompressedBatch:
    frame_count: int
    data: bytes
    speed: int


def compressed_batches(frames: Sequence[bytes], max_frames: int = 4) -> Iterator[CompressedBatch]:
    index = 0
    while index < len(frames):
        count = min(max_frames, len(frames) - index)
        while count > 0:
            data = lzma_compress_frames(frames[index:index + count])
            if len(data) <= FRAME_SIZE or count == 1:
                break
            count -= 1
        average = len(data) // count
        speed = 10 if average > 3000 else 15 if average > 2800 else 20 if average > 2500 else 25 if average > 2000 else 40 if average > 1500 else 45 if average > 1000 else 55 if average > 500 else 60
        yield CompressedBatch(count, data, speed)
        index += count


def bulk_packets(data: bytes) -> Iterator[bytes]:
    total = (len(data) + 499) // 500
    if total > 0xFF:
        raise ValueError("print bulk transfer exceeds 255 packets (127500 bytes)")
    for index in range(total):
        part = data[index * 500:(index + 1) * 500]
        yield build_bulk_outer_512(build_print_bulk_packet(index, total, part))
