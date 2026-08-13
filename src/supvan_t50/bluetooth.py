from __future__ import annotations

import time
import warnings
from typing import Sequence

from PIL import Image

from .errors import CommunicationError, DeviceError, ValidationError
from .models import LabelBoxInfo, PrintJob, PrintSettings
from .job_rendering import render_job_images
from .protocol import (
    bulk_packets,
    build_data_query,
    build_r1,
    build_r2,
    compressed_batches,
    image_frames,
    make_parameter_block,
    parse_response,
    parse_label_box,
    parse_status,
)
from .status import PrinterStatus
from .transports import BleDeviceInfo, BleTransport, Transport


class BluetoothPrinter:
    """T50 BLE 高层打印机客户端。"""

    def __init__(self, transport: Transport, *, timeout: float = 3.0, print_timeout: float = 120.0):
        """使用指定 BLE 传输创建客户端，并设置命令和打印超时。"""
        self.transport = transport
        self.timeout = timeout
        self.print_timeout = print_timeout
        self.connected = False

    @classmethod
    def discover(cls, name_prefix: str = "T0", *, timeout: float = 8.0) -> list[BleDeviceInfo]:
        """扫描附近的 BLE 打印机，并按信号强度从高到低返回。"""
        return BleTransport.discover_devices(name_prefix=name_prefix, timeout=timeout)

    @classmethod
    def from_device(
        cls,
        device: BleDeviceInfo,
        *,
        timeout: float = 10.0,
        print_timeout: float = 120.0,
        chunk_size: int | None = None,
    ) -> "BluetoothPrinter":
        """使用扫描结果创建 BLE 打印机。"""
        transport = BleTransport.from_device(device, timeout=timeout, chunk_size=chunk_size)
        return cls(transport, timeout=timeout, print_timeout=print_timeout)

    @classmethod
    def from_name(
        cls,
        name: str,
        *,
        timeout: float = 10.0,
        scan_timeout: float = 8.0,
        print_timeout: float = 120.0,
        chunk_size: int | None = None,
    ) -> "BluetoothPrinter":
        """扫描并使用完整设备名创建 BLE 打印机。"""
        transport = BleTransport.from_name(
            name,
            timeout=timeout,
            scan_timeout=scan_timeout,
            chunk_size=chunk_size,
        )
        return cls(transport, timeout=timeout, print_timeout=print_timeout)

    @classmethod
    def from_address(
        cls,
        address: str,
        *,
        timeout: float = 10.0,
        print_timeout: float = 120.0,
        chunk_size: int | None = None,
    ) -> "BluetoothPrinter":
        """使用 BLE MAC 地址或平台设备地址创建打印机。"""
        transport = BleTransport.from_address(address, timeout=timeout, chunk_size=chunk_size)
        return cls(transport, timeout=timeout, print_timeout=print_timeout)

    def __enter__(self) -> "BluetoothPrinter":
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def connect(self) -> None:
        """连接打印机；重复调用由传输层安全处理。"""
        self.transport.connect()
        self.connected = True

    def close(self) -> None:
        """断开 BLE 连接并释放后台线程。"""
        self.transport.close()
        self.connected = False

    def disconnect(self) -> None:
        """断开连接，是 ``close()`` 的明确别名。"""
        self.close()

    def _read_frame(self, timeout: float | None = None) -> bytes:
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        data = bytearray()
        total: int | None = None
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            part = self.transport.read(512, remaining)
            if part:
                data.extend(part)
                if len(data) >= 4 and total is None:
                    total = (data[2] | (data[3] << 8)) + 4
                if total is not None and len(data) >= total:
                    return bytes(data[:total])
            else:
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
        raise CommunicationError(f"等待打印机响应超时，已收到 {len(data)} 字节")

    def _exchange(self, command: bytes, expected_command: int, timeout: float | None = None) -> bytes:
        if not self.connected:
            raise CommunicationError("打印机尚未连接")
        budget = self.timeout if timeout is None else timeout
        deadline = time.monotonic() + budget
        try:
            self.transport.write_chunked(command, 512, 0.01)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CommunicationError("等待打印机响应超时，已收到 0 字节")
            return parse_response(self._read_frame(remaining), expected_command)
        except CommunicationError:
            raise
        except ValueError as exc:
            raise CommunicationError(str(exc)) from exc
        except Exception as exc:
            raise CommunicationError("蓝牙控制命令传输失败") from exc

    def get_status(self, timeout: float | None = None) -> PrinterStatus:
        """查询设备状态，并保留原始响应与温度、电压等字段。"""
        frame = self._exchange(build_r1(0x11), 0x11, timeout)
        try:
            return parse_status(frame)
        except ValueError as exc:
            raise CommunicationError(str(exc)) from exc

    def read_label_box(self, timeout: float | None = None) -> LabelBoxInfo:
        """读取当前安装标签盒的类型、尺寸、余量和原始信息。"""
        frame = self._exchange(build_r1(0x30), 0x30, timeout)
        try:
            return parse_label_box(frame)
        except ValueError as exc:
            raise CommunicationError(str(exc)) from exc

    def configure(self, settings: PrintSettings) -> None:
        """兼容性配置接口；通常直接调用 ``print()`` 即可。"""
        warnings.warn(
            "configure() is retained for compatibility but is not part of the verified stable print flow",
            DeprecationWarning,
            stacklevel=2,
        )
        self._exchange(build_data_query(0x5D, make_parameter_block(settings)), 0x5D)

    def stop(self) -> None:
        """停止当前 BLE 打印任务。"""
        self._exchange(build_r1(0x14), 0x14)

    def _read_bulk_ack(self, timeout: float | None = None) -> None:
        """Read a complete framed bulk acknowledgement when required."""
        try:
            frame = self._read_frame(self.timeout if timeout is None else timeout)
            parse_response(frame, 0xBB)
        except CommunicationError:
            raise
        except ValueError as exc:
            raise CommunicationError(f"批量数据 ACK 无效：{exc}") from exc

    def _send_compressed(self, data: bytes) -> None:
        packet_count = (len(data) + 499) // 500
        self._exchange(build_r2(0x5C, 512, packet_count), 0x5C)
        for packet in bulk_packets(data):
            try:
                chunk_size = getattr(self.transport, "bulk_chunk_size", len(packet))
                chunk_delay = getattr(self.transport, "bulk_chunk_delay", 0.0)
                self.transport.write_chunked(packet, chunk_size, chunk_delay)
            except Exception as exc:
                raise CommunicationError("蓝牙传输写入失败") from exc
            if getattr(self.transport, "bulk_ack_required", True):
                settle = getattr(self.transport, "bulk_ack_settle", 0.0)
                if settle:
                    time.sleep(settle)
                self._read_bulk_ack()
            elif getattr(self.transport, "bulk_ack_optional", False):
                drain = getattr(self.transport, "drain_input", None)
                if drain is not None:
                    drain()
        self._exchange(build_r1(0x10, 0), 0x10, timeout=max(self.timeout, 5.0))

    def _wait_status(self, predicate, timeout: float, description: str) -> PrinterStatus:
        deadline = time.monotonic() + timeout
        last: PrinterStatus | None = None
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            last = self.get_status(timeout=remaining)
            if not last.ready:
                raise DeviceError(last.description)
            if predicate(last):
                return last
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        raise CommunicationError(f"等待{description}超时；最后状态：{last.description if last else '无响应'}")

    def print_images(self, images: Sequence[Image.Image], settings: PrintSettings) -> bool:
        """使用指定设置打印一组 Pillow 图像。"""
        if not images:
            raise ValueError("images 不能为空")
        if getattr(self.transport, "separate_physical_pages", False) and len(images) > 1:
            for image in images:
                self._print_images_once([image], settings)
            return True
        return self._print_images_once(images, settings)

    def _print_images_once(self, images: Sequence[Image.Image], settings: PrintSettings) -> bool:
        status = self.get_status()
        if not status.ready:
            raise DeviceError(status.description)
        page_batches = []
        for index, image in enumerate(images):
            frames = image_frames(image, settings, job_last_page=index == len(images) - 1)
            page_batches.append(list(compressed_batches(frames)))
        self._exchange(build_r1(0x13), 0x13)
        # Never merge frames from different physical labels into one compressed
        # batch. A single label may require multiple frames and is known to work
        # when those frames are grouped, while cross-page grouping causes the T0
        # BLE firmware to consume only the first label.
        first_batch = True
        for batches in page_batches:
            for batch in batches:
                if not first_batch:
                    self._wait_status(lambda value: not value.buffer_full, 10.0, "打印缓冲区就绪")
                self._send_compressed(batch.data)
                first_batch = False
        if getattr(self.transport, "print_completion_on_submit", False):
            return True
        initial_pages = status.printed_pages
        deadline = time.monotonic() + self.print_timeout
        observed_printing = False
        last = status
        while time.monotonic() < deadline:
            last = self.get_status(timeout=max(0.1, deadline - time.monotonic()))
            if not last.ready:
                raise DeviceError(last.description)
            observed_printing = observed_printing or last.printing or last.busy
            if last.printed_pages > initial_pages and not last.printing and not last.busy:
                return True
            if observed_printing and not last.printing and not last.busy:
                return True
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        raise CommunicationError(
            f"等待实际出纸超时；未观察到打印状态或页计数变化，最后页计数 {last.printed_pages}"
        )

    def print(self, job: PrintJob) -> bool:
        """渲染并打印一个 ``PrintJob``，完成时返回 ``True``。"""
        if job.settings.needs_label_box:
            job = job.with_settings(job.settings.resolved(self.read_label_box()))
        return self.print_images(render_job_images(job), job.settings)
