from __future__ import annotations

import lzma
from dataclasses import dataclass
from typing import Sequence

from PIL import Image, ImageOps

from .errors import CommunicationError, ValidationError
from .status import PrinterState, PrinterStatus


VID_SUPVAN = 0x1820

# Product IDs mapped to the T50 Plus / Pro / S code path by the vendor DLL.
T50_FAMILY_PIDS: dict[int, str] = {
    0x2072: "T50 Plus",
    0x2073: "T50 Pro",
    0x2074: "T50 S",
    0x2076: "T50 Pro",
    0x2077: "T50 Pro",
    0x207D: "T50 Pro",
    0x207F: "T50 Pro",
    0x2170: "T50 Pro",
}

CMD_BUFFER_FULL = 0x10
CMD_INQUIRY_STATUS = 0x11
CMD_RETURN_MAT = 0x30
CMD_CHECK_DEVICE = 0x12
CMD_START_PRINT = 0x13
CMD_STOP_PRINT = 0x14
CMD_TRANSFER_DATA = 0x5C
CMD_SET_MEDIA = 0x5D

FRAME_SIZE = 4096
FRAME_DATA_SIZE = 4074
HID_REPORT_DATA_SIZE = 64


@dataclass(frozen=True)
class T50StatusFlags:
    buffer_full: bool
    media_read_write_error: bool
    media_empty: bool
    media_mode_error: bool
    media_install_error: bool
    media_low: bool
    low_battery: bool
    device_busy: bool
    head_overheat: bool
    cover_open: bool
    printing: bool
    second_device_busy: bool
    media_not_installed: bool
    charging: bool
    printed_pages: int
    raw: bytes

    @classmethod
    def parse(cls, data: bytes | bytearray | Sequence[int]) -> "T50StatusFlags":
        raw = bytes(data)
        if len(raw) < 8:
            raise CommunicationError(f"T50 状态应为 8 字节，实际收到 {len(raw)} 字节")
        return cls(
            buffer_full=bool(raw[0] & 0x01),
            media_read_write_error=bool(raw[0] & 0x02),
            media_empty=bool(raw[0] & 0x04),
            media_mode_error=bool(raw[0] & 0x08),
            media_install_error=bool(raw[0] & 0x10),
            media_low=bool(raw[0] & 0x20),
            low_battery=bool(raw[0] & 0x40),
            device_busy=bool(raw[1] & 0x04),
            head_overheat=bool(raw[1] & 0x08),
            cover_open=bool(raw[2] & 0x08),
            printing=bool(raw[2] & 0x40),
            second_device_busy=bool(raw[2] & 0x80),
            media_not_installed=bool(raw[3] & 0x01),
            charging=bool(raw[3] & 0x80),
            printed_pages=raw[4] | (raw[5] << 8),
            raw=raw[:8],
        )

    def to_printer_status(self, *, total_pages: int = 0) -> PrinterStatus:
        state = PrinterState.READY
        error = ""
        if self.head_overheat:
            state, error = PrinterState.HEAD_OVERHEAT, "打印头温度过高"
        elif self.cover_open:
            state, error = PrinterState.COVER_OPEN, "请关闭耗材仓盖"
        elif self.media_install_error or self.media_not_installed:
            state, error = PrinterState.MEDIA_NOT_INSTALLED, "耗材未装好"
        elif self.media_low:
            state, error = PrinterState.MEDIA_LOW, "请检查耗材余量"
        elif self.media_read_write_error:
            state, error = PrinterState.MEDIA_NOT_DETECTED, "未检测到耗材"
        elif self.media_mode_error:
            state, error = PrinterState.MEDIA_UNRECOGNIZED, "未识别到耗材"
        elif self.media_empty:
            state, error = PrinterState.MEDIA_EMPTY, "耗材已用完"
        elif self.low_battery:
            state, error = PrinterState.BATTERY_LOW, "电量低，请充电"
        description = "打印中" if self.printing and not error else (error or "准备就绪")
        return PrinterStatus(
            state=state,
            description=description,
            error_message=error,
            printed_pages=self.printed_pages,
            total_pages=total_pages,
            raw=self.raw,
        )

    @property
    def fatal_error(self) -> str:
        status = self.to_printer_status()
        if status.state in (PrinterState.READY, PrinterState.BATTERY_LOW):
            return ""
        return status.error_message


def build_vendor_request(command: int, value1: int = 0, value2: int | None = None) -> bytes:
    if not 0 <= command <= 0xFF:
        raise ValidationError("USB 命令必须在 0-255 范围内")
    if not 0 <= value1 <= 0xFFFF or value2 is not None and not 0 <= value2 <= 0xFFFF:
        raise ValidationError("USB 命令参数必须在 0-65535 范围内")
    request = bytearray((0xC0, 0x40, value1 >> 8, value1 & 0xFF, command, 0, 8, 0))
    if value2 is not None:
        request.extend((value2 >> 8, value2 & 0xFF))
    return bytes(request)


_MEDIA_TEMPLATE = bytes(
    (
        48, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 121, 1, 1, 91,
        235, 93, 155, 179, 48, 117, 1, 50, 50, 1,
        3, 0, 224, 1, 0, 0, 164, 6, 176, 4,
        23, 8, 17, 11, 48, 57, 0, 0, 135, 220,
        151, 205, 1, 224, 159, 64, 149, 68, 77, 133,
        236, 167, 205, 0, 0, 0, 0, 0, 0, 2,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    )
)


def build_media_config(*, paper_type: int, gap: int, height: int) -> bytes:
    data = bytearray(_MEDIA_TEMPLATE)
    data[26] = paper_type if paper_type in (1, 2, 4, 5) else 1
    data[28] = min(max(int(height), 0), 120)
    data[30] = min(max(int(gap), 2), 8)
    return bytes(data)


def _page_flags(*, first: bool, last: bool, last_job: bool, density: int, paper_type: int) -> bytes:
    first_byte = (0x02 if first else 0) | (0x04 if last else 0) | (0x08 if last_job else 0)
    second_byte = ((paper_type & 0x03) << 6) | ((density & 0x0F) << 2)
    return bytes((first_byte, second_byte))


def prepare_t50_image(
    image: Image.Image,
    *,
    max_dots: int = 384,
    horizontal_offset: int = 0,
    vertical_offset: int = 0,
) -> Image.Image:
    source = image.convert("L")
    scale = min(1.0, max_dots / max(1, source.width))
    if scale < 1.0:
        source = source.resize(
            (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
            Image.Resampling.LANCZOS,
        )
    target = Image.new("L", (max_dots, source.height), 255)
    x = ((max_dots - source.width) // 2) + horizontal_offset
    y = vertical_offset
    target.paste(source, (x, y))
    return ImageOps.mirror(target)


def image_to_frames(
    image: Image.Image,
    *,
    density: int,
    paper_type: int,
    last_job_page: bool,
    threshold: int = 190,
) -> list[bytes]:
    gray = image.convert("L")
    bytes_per_row = (gray.width + 7) // 8
    rows_per_frame = FRAME_DATA_SIZE // bytes_per_row
    if rows_per_frame <= 0:
        raise ValidationError("图像宽度超过 T50 帧容量")

    pixels = gray.load()
    packed = bytearray(bytes_per_row * gray.height)
    for y in range(gray.height):
        row = y * bytes_per_row
        for x in range(gray.width):
            if pixels[x, y] < threshold:
                packed[row + x // 8] |= 1 << (x % 8)

    frames: list[bytes] = []
    total = max(1, (gray.height + rows_per_frame - 1) // rows_per_frame)
    for index in range(total):
        start_row = index * rows_per_frame
        row_count = min(rows_per_frame, gray.height - start_row)
        frame = bytearray(FRAME_SIZE)
        frame[2:4] = _page_flags(
            first=index == 0,
            last=index == total - 1,
            last_job=last_job_page and index == total - 1,
            density=density,
            paper_type=paper_type,
        )
        frame[4] = row_count & 0xFF
        frame[5] = (row_count >> 8) & 0xFF
        frame[6] = bytes_per_row
        frame[8] = 1
        frame[10] = 1
        data_length = row_count * bytes_per_row
        source_start = start_row * bytes_per_row
        frame[14 : 14 + data_length] = packed[source_start : source_start + data_length]

        used_length = 14 + data_length
        checksum = sum(frame[2:14])
        for position in range(255, used_length, 256):
            checksum += frame[position]
        frame[0] = checksum & 0xFF
        frame[1] = (checksum >> 8) & 0xFF
        frames.append(bytes(frame))
    return frames


_LZMA_FILTER = {
    "id": lzma.FILTER_LZMA1,
    "dict_size": 8192,
    "lc": 3,
    "lp": 0,
    "pb": 2,
    "mode": lzma.MODE_NORMAL,
    "nice_len": 128,
    "mf": lzma.MF_BT4,
}


def lzma_compress_frames(frames: Sequence[bytes]) -> bytes:
    source = b"".join(frames)
    compressed = lzma.compress(source, format=lzma.FORMAT_RAW, filters=[_LZMA_FILTER])
    # SevenZip LZMA-alone layout: five coder-property bytes, eight-byte source
    # length, followed by the raw LZMA1 stream.
    return b"\x5d\x00\x20\x00\x00" + len(source).to_bytes(8, "little") + compressed


def select_transfer_block(frames: Sequence[bytes], *, max_frames: int = 8, max_size: int = FRAME_SIZE) -> tuple[bytes, int]:
    count = min(max_frames, len(frames))
    while count > 0:
        compressed = lzma_compress_frames(frames[:count])
        if len(compressed) <= max_size:
            return compressed, count
        count -= 1
    raise CommunicationError("单个 4 KB 图像帧压缩后仍超过设备传输上限")
