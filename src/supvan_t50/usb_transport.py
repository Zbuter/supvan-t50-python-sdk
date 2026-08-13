from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .errors import CommunicationError, DependencyError
from .usb_protocol import HID_REPORT_DATA_SIZE, T50_FAMILY_PIDS, VID_SUPVAN, build_vendor_request


@dataclass(frozen=True)
class UsbDeviceInfo:
    """原生 USB 设备信息及可复用的设备选择器。"""
    vendor_id: int
    product_id: int
    bus: int | None
    address: int | None
    serial_number: str = ""
    product_name: str = ""

    @property
    def selector(self) -> str:
        """返回 ``usb:VID:PID:bus:address`` 格式的设备标识。"""
        bus = "*" if self.bus is None else str(self.bus)
        address = "*" if self.address is None else str(self.address)
        return f"usb:{self.vendor_id:04x}:{self.product_id:04x}:{bus}:{address}"

    def __str__(self) -> str:
        return self.selector


def _load_pyusb() -> tuple[Any, Any, Any | None]:
    try:
        import usb.core
        import usb.util
    except ImportError as exc:
        raise DependencyError("原生 USB 后端需要 PyUSB：pip install 'supvan-t50[usb]'") from exc
    backend = None
    try:
        import libusb_package

        backend = libusb_package.get_libusb1_backend()
    except ImportError:
        pass
    return usb.core, usb.util, backend


def _safe_string(usb_util: Any, device: Any, index: int | None) -> str:
    if not index:
        return ""
    try:
        return str(usb_util.get_string(device, index) or "")
    except Exception:
        return ""


def _iter_devices(usb_core: Any, backend: Any | None = None) -> Iterable[Any]:
    try:
        devices = usb_core.find(find_all=True, idVendor=VID_SUPVAN, backend=backend)
    except Exception as exc:
        raise DependencyError(
            "PyUSB 已安装但未找到 libusb 后端；请安装 libusb，Windows 推荐安装 libusb-package"
        ) from exc
    return devices or ()


def _parse_selector(selector: str) -> tuple[int | None, int | None, int | None]:
    if not selector:
        return None, None, None
    parts = selector.lower().split(":")
    if len(parts) < 3 or parts[0] != "usb":
        raise CommunicationError(f"无效的原生 USB 设备标识：{selector}")
    product_id = int(parts[2], 16)
    bus = None if len(parts) < 4 or parts[3] == "*" else int(parts[3])
    address = None if len(parts) < 5 or parts[4] == "*" else int(parts[4])
    return product_id, bus, address


class PyUsbTransport:
    """处理打印机 64 字节 HID 报告的 PyUSB/libusb 传输层。"""

    def __init__(self, selector: str = "", *, timeout: float = 2.0, device: Any | None = None):
        self.selector = selector
        self.timeout_ms = max(1, int(timeout * 1000))
        self.device = device
        self.interface_number = 0
        self.endpoint_in: Any | None = None
        self.endpoint_out: Any | None = None
        self._usb_util: Any | None = None
        self._detached_kernel_driver = False
        self._opened = False

    @classmethod
    def device_infos(cls) -> list[UsbDeviceInfo]:
        """枚举并返回支持的 USB 设备详细信息。"""
        usb_core, usb_util, backend = _load_pyusb()
        result: list[UsbDeviceInfo] = []
        for device in _iter_devices(usb_core, backend):
            product_id = int(device.idProduct)
            if product_id not in T50_FAMILY_PIDS:
                continue
            result.append(
                UsbDeviceInfo(
                    vendor_id=int(device.idVendor),
                    product_id=product_id,
                    bus=getattr(device, "bus", None),
                    address=getattr(device, "address", None),
                    serial_number=_safe_string(usb_util, device, getattr(device, "iSerialNumber", 0)),
                    product_name=_safe_string(usb_util, device, getattr(device, "iProduct", 0)) or T50_FAMILY_PIDS[product_id],
                )
            )
        return result

    @classmethod
    def list_devices(cls) -> list[str]:
        """枚举并返回可传给 ``UsbPrinter`` 的设备标识列表。"""
        return [info.selector for info in cls.device_infos()]

    def _find_device(self, usb_core: Any, backend: Any | None = None) -> Any:
        product_id, bus, address = _parse_selector(self.selector)
        for device in _iter_devices(usb_core, backend):
            if int(device.idProduct) not in T50_FAMILY_PIDS:
                continue
            if product_id is not None and int(device.idProduct) != product_id:
                continue
            if bus is not None and getattr(device, "bus", None) != bus:
                continue
            if address is not None and getattr(device, "address", None) != address:
                continue
            return device
        raise CommunicationError("未找到 T50 Plus/Pro USB 打印机")

    def open(self) -> None:
        """打开设备、选择 HID 接口并占用端点。"""
        if self._opened:
            return
        usb_core, usb_util, backend = _load_pyusb()
        self._usb_util = usb_util
        if self.device is None:
            self.device = self._find_device(usb_core, backend)
        try:
            try:
                configuration = self.device.get_active_configuration()
            except Exception:
                self.device.set_configuration()
                configuration = self.device.get_active_configuration()
            interface = None
            for candidate in configuration:
                if int(candidate.bInterfaceClass) == 0x03 and int(candidate.bInterfaceNumber) == 0:
                    interface = candidate
                    break
            if interface is None:
                for candidate in configuration:
                    if int(candidate.bInterfaceClass) == 0x03:
                        interface = candidate
                        break
            if interface is None:
                raise CommunicationError("设备没有可用的 HID 接口")
            self.interface_number = int(interface.bInterfaceNumber)

            try:
                if self.device.is_kernel_driver_active(self.interface_number):
                    self.device.detach_kernel_driver(self.interface_number)
                    self._detached_kernel_driver = True
            except Exception:
                pass

            usb_util.claim_interface(self.device, self.interface_number)
            for endpoint in interface:
                direction = usb_util.endpoint_direction(endpoint.bEndpointAddress)
                if direction == usb_util.ENDPOINT_IN and self.endpoint_in is None:
                    self.endpoint_in = endpoint
                elif direction == usb_util.ENDPOINT_OUT and self.endpoint_out is None:
                    self.endpoint_out = endpoint
            if self.endpoint_in is None:
                raise CommunicationError("T50 HID 接口没有输入端点")
            self._opened = True
        except CommunicationError:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise CommunicationError(
                "无法占用 T50 USB HID 接口；Linux 请检查 udev 权限，macOS 请检查 libusb 和接口占用"
            ) from exc

    def close(self) -> None:
        """释放接口、恢复驱动并清理 libusb 资源。"""
        if self.device is None or self._usb_util is None:
            self._opened = False
            return
        try:
            self._usb_util.release_interface(self.device, self.interface_number)
        except Exception:
            pass
        if self._detached_kernel_driver:
            try:
                self.device.attach_kernel_driver(self.interface_number)
            except Exception:
                pass
        try:
            self._usb_util.dispose_resources(self.device)
        except Exception:
            pass
        self._opened = False

    def __enter__(self) -> "PyUsbTransport":
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _write_report(self, report: bytes) -> None:
        if len(report) != HID_REPORT_DATA_SIZE:
            raise ValueError("HID 输出报告必须为 64 字节")
        try:
            if self.endpoint_out is not None:
                written = self.endpoint_out.write(report, self.timeout_ms)
            else:
                # HID SET_REPORT(Output), used when the descriptor has no interrupt OUT endpoint.
                written = self.device.ctrl_transfer(
                    0x21,
                    0x09,
                    0x0200,
                    self.interface_number,
                    report,
                    self.timeout_ms,
                )
        except Exception as exc:
            raise CommunicationError("T50 USB HID 写入失败") from exc
        # Windows HID stacks may account for the implicit report-id byte even
        # though libusb was given the 64-byte wire payload.
        if int(written) not in (len(report), len(report) + 1):
            raise CommunicationError(f"T50 USB HID 短写：{written}/{len(report)}")

    def write_payload(self, data: bytes) -> None:
        """将任意长度数据拆分为 64 字节 HID 输出报告。"""
        self.open()
        # Windows HidD WriteFile always sends floor(n/64)+1 reports, including a
        # final zero report when n is exactly divisible by 64. Preserve that quirk.
        report_count = len(data) // HID_REPORT_DATA_SIZE + 1
        for index in range(report_count):
            start = index * HID_REPORT_DATA_SIZE
            report = data[start : start + HID_REPORT_DATA_SIZE].ljust(HID_REPORT_DATA_SIZE, b"\x00")
            self._write_report(report)

    def read_payload(self, length: int, *, timeout: float | None = None) -> bytes:
        """读取指定长度的 HID 有效载荷。"""
        self.open()
        timeout_ms = self.timeout_ms if timeout is None else max(1, int(timeout * 1000))
        result = bytearray()
        while len(result) < length:
            try:
                report = bytes(self.endpoint_in.read(int(self.endpoint_in.wMaxPacketSize), timeout_ms))
            except Exception as exc:
                raise CommunicationError("T50 USB HID 读取失败或超时") from exc
            if len(report) < 2:
                raise CommunicationError("T50 USB HID 返回了过短的输入报告")
            # Windows ReadFile prepends report-id 0; the DLL then discards that
            # byte and the first on-wire protocol byte. libusb sees only the wire report.
            result.extend(report[1:])
        return bytes(result[:length])

    def command(self, command: int, value1: int = 0, value2: int | None = None, *, timeout: float | None = None) -> bytes:
        """发送一个 USB 厂商命令并返回 8 字节状态响应。"""
        self.write_payload(build_vendor_request(command, value1, value2))
        return self.read_payload(8, timeout=timeout)

    def command_payload(
        self,
        command: int,
        length: int,
        value1: int = 0,
        value2: int | None = None,
        *,
        timeout: float | None = None,
    ) -> bytes:
        """发送厂商命令并读取指定长度的非状态响应。"""
        self.write_payload(build_vendor_request(command, value1, value2))
        return self.read_payload(length, timeout=timeout)
