"""Common high-level printer contract shared by Bluetooth and USB backends."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from PIL import Image

from .job_rendering import render_job_images
from .models import PrintJob
from .status import PrinterStatus


@runtime_checkable
class Printer(Protocol):
    """BLE 与原生 USB 共同实现的高层打印机接口。"""

    def get_status(self) -> PrinterStatus:
        """返回统一格式的当前打印机状态。"""

    def print(self, job: PrintJob) -> bool:
        """打印高层任务，并返回是否成功完成。"""

    def close(self) -> None:
        """释放当前连接和底层资源。"""

    def __enter__(self) -> "Printer":
        """连接打印机并返回当前实例。"""

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """退出上下文管理器时自动关闭打印机。"""


def print_job(printer: Printer, job: PrintJob) -> bool:
    """检查设备是否就绪，并通过统一接口打印任务。"""
    status = printer.get_status()
    if not status.ready:
        from .errors import DeviceError

        raise DeviceError(status.description or status.error_message or "打印机未就绪")
    return bool(printer.print(job))


def preview_job(job: PrintJob, output: str | Path | None = None) -> list[Image.Image]:
    """渲染打印任务；可将单页或多页预览保存为图片文件。"""
    images = render_job_images(job, copy_images=True)
    if output is None:
        return images

    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    if len(images) == 1:
        images[0].save(path)
        return images

    suffix = path.suffix or ".png"
    for index, image in enumerate(images, 1):
        image.save(path.with_name(f"{path.stem}-{index}{suffix}"))
    return images
