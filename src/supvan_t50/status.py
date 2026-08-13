from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class PrinterState(IntEnum):
    """统一的打印机设备状态。"""
    READY = 0
    HEAD_OVERHEAT = 1
    COVER_OPEN = 2
    MEDIA_NOT_INSTALLED = 3
    MEDIA_LOW = 4
    MEDIA_NOT_DETECTED = 5
    MEDIA_UNRECOGNIZED = 6
    MEDIA_EMPTY = 7
    BATTERY_LOW = 8
    COMMUNICATION_ERROR = 9


class JobState(IntEnum):
    """原生 USB 打印任务执行状态。"""
    WAITING = 0
    CHECK_DEVICE = 1
    PRINTING = 2
    ABORTED = 3
    COMPLETED = 4
    RESET_DEVICE = 5


_DESCRIPTIONS = {
    PrinterState.READY: "准备就绪",
    PrinterState.HEAD_OVERHEAT: "打印头温度过高",
    PrinterState.COVER_OPEN: "上盖未关好",
    PrinterState.MEDIA_NOT_INSTALLED: "耗材未装好",
    PrinterState.MEDIA_LOW: "耗材余量不足",
    PrinterState.MEDIA_NOT_DETECTED: "未检测到耗材",
    PrinterState.MEDIA_UNRECOGNIZED: "未识别到耗材",
    PrinterState.MEDIA_EMPTY: "耗材已用完",
    PrinterState.BATTERY_LOW: "电池电压低",
    PrinterState.COMMUNICATION_ERROR: "通信异常",
}


@dataclass(frozen=True)
class PrinterStatus:
    """统一的打印机状态结果。

    除状态与描述外，还包含页数、温度、电压、打印中、上盖打开、
    耗材异常等已解析字段；``raw`` 保存设备原始响应。
    """
    state: PrinterState
    description: str = ""
    error_message: str = ""
    printed_pages: int = 0
    total_pages: int = 0
    raw: bytes | None = None
    job_state: JobState | None = None
    raw_flags: bytes = b""
    temperature_c: float | None = None
    voltage_v: float | None = None
    buffer_full: bool = False
    label_read_write_error: bool = False
    media_empty: bool = False
    media_unrecognized: bool = False
    media_not_installed: bool = False
    battery_low: bool = False
    busy: bool = False
    cover_open: bool = False
    usb_inserted: bool = False
    printing: bool = False
    second_device_busy: bool = False
    label_not_installed: bool = False
    charging: bool = False

    @property
    def ready(self) -> bool:
        """设备是否处于可接受新打印任务的就绪状态。"""
        return self.state == PrinterState.READY and self.job_state in (None, JobState.WAITING)

    @classmethod
    def from_state(cls, state: int, **kwargs: object) -> "PrinterStatus":
        """根据整数状态码创建带中文描述的状态对象。"""
        try:
            parsed = PrinterState(state)
        except ValueError:
            parsed = PrinterState.COMMUNICATION_ERROR
        return cls(parsed, _DESCRIPTIONS.get(parsed, "未知状态"), **kwargs)
