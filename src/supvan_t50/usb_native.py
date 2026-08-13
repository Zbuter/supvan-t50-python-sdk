from __future__ import annotations

import time
from typing import Any, Iterable

from PIL import Image

from .errors import CommunicationError, DeviceError
from .job_rendering import render_job_images
from .models import LabelBoxInfo, PrintJob
from .protocol import parse_label_box_data
from .status import JobState, PrinterStatus
from .usb_protocol import (
    CMD_BUFFER_FULL,
    CMD_CHECK_DEVICE,
    CMD_INQUIRY_STATUS,
    CMD_RETURN_MAT,
    CMD_SET_MEDIA,
    CMD_START_PRINT,
    CMD_STOP_PRINT,
    CMD_TRANSFER_DATA,
    T50StatusFlags,
    build_media_config,
    image_to_frames,
    prepare_t50_image,
    select_transfer_block,
)
from .usb_transport import PyUsbTransport


class NativeUsbPrinter:
    """使用 PyUSB/libusb 的跨平台原生 USB 打印机实现。"""

    def __init__(
        self,
        device_path: str = "",
        *,
        timeout: float = 120.0,
        transport: Any | None = None,
        poll_interval: float = 0.05,
    ):
        self.device_path = device_path
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.transport = transport or PyUsbTransport(device_path, timeout=min(timeout, 2.0))
        self._last_status: T50StatusFlags | None = None
        self._total_pages = 0

    @classmethod
    def list_devices(cls) -> list[str]:
        """列出支持的 USB 打印机设备标识。"""
        return PyUsbTransport.list_devices()

    def close(self) -> None:
        """关闭并释放 USB 传输。"""
        self.transport.close()

    def __enter__(self) -> "NativeUsbPrinter":
        self.transport.open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _command_status(self, command: int, value1: int = 0, value2: int | None = None) -> T50StatusFlags:
        flags = T50StatusFlags.parse(self.transport.command(command, value1, value2))
        self._last_status = flags
        return flags

    @staticmethod
    def _raise_device_error(flags: T50StatusFlags) -> None:
        if flags.fatal_error:
            raise DeviceError(flags.fatal_error)

    def get_status(self) -> PrinterStatus:
        """查询并返回统一的 USB 打印机状态。"""
        flags = self._command_status(CMD_INQUIRY_STATUS)
        status = flags.to_printer_status(total_pages=self._total_pages)
        return PrinterStatus(
            state=status.state,
            description=status.description,
            error_message=status.error_message,
            printed_pages=status.printed_pages,
            total_pages=status.total_pages,
            raw=status.raw,
            job_state=JobState.PRINTING if flags.printing else JobState.WAITING,
        )

    def read_label_box(self) -> LabelBoxInfo:
        """读取当前 USB 标签盒信息。"""
        payload = self.transport.command_payload(CMD_RETURN_MAT, 57)
        try:
            return parse_label_box_data(payload)
        except ValueError as exc:
            raise CommunicationError(f"USB 标签盒响应无效：{exc}") from exc

    @staticmethod
    def _rotate(image: Image.Image, direction: int) -> Image.Image:
        if direction == 1:
            return image.transpose(Image.Transpose.ROTATE_180)
        if direction == 2:
            return image.transpose(Image.Transpose.ROTATE_90)
        if direction == 3:
            return image.transpose(Image.Transpose.ROTATE_270)
        return image

    @classmethod
    def _render_images(cls, job: PrintJob) -> list[Image.Image]:
        settings = job.settings
        def map_image(rendered: Image.Image, job: PrintJob) -> Image.Image:
            if settings.dpi != 8.0:
                rendered = rendered.resize(
                    (
                        max(1, int(rendered.width * settings.dpi / 8.0)),
                        max(1, int(rendered.height * settings.dpi / 8.0)),
                    ),
                    Image.Resampling.BILINEAR,
                )
            return cls._rotate(rendered, settings.direction)

        return render_job_images(job, image_mapper=map_image, copy_images=True)

    @staticmethod
    def _build_frames(images: Iterable[Image.Image], job: PrintJob) -> list[bytes]:
        images = list(images)
        frames: list[bytes] = []
        settings = job.settings
        for index, image in enumerate(images):
            prepared = prepare_t50_image(
                image,
                max_dots=settings.max_dot_value,
                horizontal_offset=settings.horizontal_offset,
                vertical_offset=settings.vertical_offset,
            )
            frames.extend(
                image_to_frames(
                    prepared,
                    density=settings.density,
                    paper_type=int(settings.paper_type),
                    last_job_page=index == len(images) - 1,
                )
            )
        return frames

    def _wait_command_ready(self, deadline: float) -> T50StatusFlags:
        while time.monotonic() < deadline:
            flags = self._command_status(CMD_INQUIRY_STATUS)
            self._raise_device_error(flags)
            if not flags.device_busy:
                return flags
            time.sleep(max(self.poll_interval, 0.01))
        raise CommunicationError("等待 T50 执行设备检测命令超时")

    def print(self, job: PrintJob) -> bool:
        """渲染、传输并等待一个 USB 打印任务实际完成。"""
        if job.settings.needs_label_box:
            job = job.with_settings(job.settings.resolved(self.read_label_box()))
        images = self._render_images(job)
        if not images:
            raise DeviceError("无打印内容")
        frames = self._build_frames(images, job)
        self._total_pages = len(images)
        settings = job.settings
        deadline = time.monotonic() + self.timeout

        media = build_media_config(
            paper_type=int(settings.paper_type),
            gap=settings.gap,
            height=settings.material_height,
        )
        self.transport.command(CMD_SET_MEDIA, len(media))
        self.transport.write_payload(media)
        self.transport.read_payload(4)

        self._command_status(CMD_CHECK_DEVICE)
        flags = self._wait_command_ready(deadline)
        self._raise_device_error(flags)
        if flags.printing:
            raise DeviceError("打印机正在执行其他任务")

        flags = self._command_status(CMD_START_PRINT, 1)
        self._raise_device_error(flags)
        speed = min(max(settings.speed, 20), 60)
        frame_index = 0

        while frame_index < len(frames):
            if time.monotonic() >= deadline:
                self.stop()
                raise CommunicationError("T50 USB 图像传输超时")
            if flags.buffer_full:
                time.sleep(max(self.poll_interval, 0.005))
                flags = self._command_status(CMD_INQUIRY_STATUS)
                self._raise_device_error(flags)
                if not flags.printing:
                    raise DeviceError("打印任务在数据传输完成前被终止")
                continue

            compressed, consumed = select_transfer_block(frames[frame_index:])
            self.transport.command(CMD_TRANSFER_DATA, len(compressed))
            self.transport.write_payload(compressed)
            self.transport.read_payload(4)
            flags = self._command_status(CMD_BUFFER_FULL, len(compressed), speed)
            self._raise_device_error(flags)
            frame_index += consumed

        # The T50 can briefly report printingStation=0 between accepting the
        # final transfer block and starting the motor. Give it a bounded start
        # grace period instead of treating the first idle sample as failure.
        start_grace_deadline = min(deadline, time.monotonic() + 3.0)
        observed_printing = bool(flags.printing)
        while time.monotonic() < deadline:
            time.sleep(max(self.poll_interval, 0.01))
            flags = self._command_status(CMD_INQUIRY_STATUS)
            self._raise_device_error(flags)
            observed_printing = observed_printing or flags.printing
            if flags.printed_pages >= self._total_pages:
                return True
            if not flags.printing:
                if not observed_printing and time.monotonic() < start_grace_deadline:
                    continue
                raise DeviceError(
                    f"打印任务提前结束：已打印 {flags.printed_pages}/{self._total_pages} 页"
                )
        self.stop()
        raise CommunicationError("等待 T50 USB 打印完成超时")

    def stop(self) -> bool:
        """请求停止当前 USB 打印，并等待设备退出打印状态。"""
        flags = self._command_status(CMD_INQUIRY_STATUS)
        if not flags.printing:
            return True
        self._command_status(CMD_STOP_PRINT)
        deadline = time.monotonic() + min(self.timeout, 6.0)
        while time.monotonic() < deadline:
            time.sleep(max(self.poll_interval, 0.02))
            flags = self._command_status(CMD_INQUIRY_STATUS)
            if not flags.printing:
                return True
        return False
