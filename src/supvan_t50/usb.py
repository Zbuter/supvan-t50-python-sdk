from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import LabelBoxInfo, PrintJob
from .status import PrinterStatus
from .usb_native import NativeUsbPrinter


class UsbPrinter:
    """原生 USB 打印机高层客户端，不使用任何桥接程序。"""

    def __init__(self, device_path: str = "", *, timeout: float = 120.0, transport: Any | None = None):
        """创建客户端；设备标识为空时由底层选择首个支持的设备。"""
        self._backend = NativeUsbPrinter(device_path, timeout=timeout, transport=transport)

    @classmethod
    def list_devices(cls) -> list[str]:
        """列出当前可用的硕方原生 USB 设备标识。"""
        return NativeUsbPrinter.list_devices()

    def get_status(self) -> PrinterStatus:
        """查询并返回统一的打印机状态。"""
        return self._backend.get_status()

    def read_label_box(self) -> LabelBoxInfo:
        """读取当前安装的 USB 标签盒信息。"""
        return self._backend.read_label_box()

    def print(self, job: PrintJob) -> bool:
        """打印一个高层任务，实际完成时返回 ``True``。"""
        return self._backend.print(job)

    def stop(self) -> bool:
        """停止当前 USB 打印任务；停止或原本空闲时返回 ``True``。"""
        return self._backend.stop()

    def close(self) -> None:
        """释放 USB 接口和相关系统资源。"""
        self._backend.close()

    def __enter__(self) -> "UsbPrinter":
        self._backend.__enter__()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
