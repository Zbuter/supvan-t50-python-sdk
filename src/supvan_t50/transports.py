from __future__ import annotations

import queue
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .errors import CommunicationError, DependencyError


@dataclass(frozen=True)
class BleDeviceInfo:
    """BLE 扫描结果，包含设备名、地址和信号强度。"""
    name: str
    address: str
    rssi: int


class Transport(ABC):
    """打印机传输层抽象接口。"""
    @abstractmethod
    def connect(self) -> None: ...
    @abstractmethod
    def close(self) -> None: ...
    @abstractmethod
    def write(self, data: bytes) -> None: ...
    @abstractmethod
    def read(self, size: int = 512, timeout: float = 2.0) -> bytes: ...

    def write_chunked(self, data: bytes, chunk_size: int = 512, delay: float = 0.01) -> None:
        for offset in range(0, len(data), chunk_size):
            self.write(data[offset:offset + chunk_size])
            if delay:
                time.sleep(delay)


class BleTransport(Transport):
    """T0 系列打印机的 BLE GATT 传输实现。"""
    SERVICE_UUID = "0000e0ff-3c17-d293-8e48-14fe2e4da212"
    WRITE_UUID = "0000ffe9-0000-1000-8000-00805f9b34fb"
    NOTIFY_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
    BULK_NOTIFY_UUID = "0000ffea-0000-1000-8000-00805f9b34fb"
    bulk_ack_required = False
    separate_physical_pages = True

    def __init__(self, address: str, *, timeout: float = 10.0, chunk_size: int | None = None):
        """创建指定 BLE 地址的传输；默认按 20 字节分片写入。"""
        self.address = address
        self.timeout = timeout
        self.chunk_size = chunk_size
        self._client = None
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
        self._received: queue.Queue[bytes] = queue.Queue()
        self._buffer = bytearray()
        self._startup_error = None

    @classmethod
    def discover_devices(cls, name_prefix: str = "T0", timeout: float = 8.0) -> list[BleDeviceInfo]:
        """扫描名称以指定前缀开头的 BLE 打印机，并按信号强度排序。"""
        try:
            import asyncio
            from bleak import BleakScanner
        except ImportError as exc:
            raise DependencyError("BLE 需要 bleak：pip install 'supvan-t50[ble]'") from exc

        async def scan():
            found = await BleakScanner.discover(timeout=timeout, return_adv=True)
            result = []
            for address, (device, advertisement) in found.items():
                name = device.name or advertisement.local_name or ""
                if name.upper().startswith(name_prefix.upper()):
                    result.append(BleDeviceInfo(name, address, advertisement.rssi))
            return sorted(result, key=lambda item: (-item.rssi, item.name, item.address))

        return asyncio.run(scan())

    @classmethod
    def from_device(cls, device: BleDeviceInfo, *, timeout: float = 10.0, chunk_size: int | None = None):
        """根据 ``BleDeviceInfo`` 扫描结果创建传输实例。"""
        if not isinstance(device, BleDeviceInfo):
            raise TypeError("device 必须是 BleDeviceInfo")
        return cls.from_address(device.address, timeout=timeout, chunk_size=chunk_size)

    @classmethod
    def from_address(cls, address: str, *, timeout: float = 10.0, chunk_size: int | None = None):
        """根据 BLE MAC 地址或平台设备地址创建传输实例。"""
        if not isinstance(address, str) or not address.strip():
            raise ValueError("address 不能为空")
        return cls(address.strip(), timeout=timeout, chunk_size=chunk_size)

    @classmethod
    def from_name(
        cls,
        name: str,
        *,
        timeout: float = 10.0,
        scan_timeout: float = 8.0,
        chunk_size: int | None = None,
    ):
        """扫描并根据完整设备名创建传输；同名时选择信号最强的设备。"""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name 不能为空")
        target_name = name.strip()
        devices = cls.discover_devices(name_prefix=target_name, timeout=scan_timeout)
        for device in devices:
            if device.name.casefold() == target_name.casefold():
                return cls.from_device(device, timeout=timeout, chunk_size=chunk_size)
        raise CommunicationError(f"未发现名为 {target_name!r} 的 BLE 设备")

    def _run_loop(self) -> None:
        import asyncio
        # debugpy/pydevd 在断点处默认暂停所有线程。BLE 命令依赖此后台
        # 事件线程接收 GATT 通知，因此必须让调试器保持该线程运行。
        current_thread = threading.current_thread()
        current_thread.pydev_do_not_trace = True
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_async())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            return
        self._ready.set()
        self._loop.run_forever()
        self._loop.run_until_complete(self._close_async())
        self._loop.close()

    async def _connect_async(self) -> None:
        try:
            from bleak import BleakClient
        except ImportError as exc:
            raise DependencyError("BLE 需要 bleak：pip install 'supvan-t50[ble]'") from exc
        self._client = BleakClient(self.address, timeout=self.timeout)
        await self._client.connect()
        service = self._client.services.get_service(self.SERVICE_UUID)
        if service is None:
            raise CommunicationError("设备未提供 T50 BLE 服务")
        await self._client.start_notify(self.NOTIFY_UUID, self._on_notify)
        if service.get_characteristic(self.BULK_NOTIFY_UUID) is not None:
            await self._client.start_notify(self.BULK_NOTIFY_UUID, self._on_notify)

    def _on_notify(self, _sender, data: bytearray) -> None:
        if data:
            self._received.put(bytes(data))

    def _submit(self, coroutine, timeout: float | None = None):
        import asyncio
        if self._loop is None or self._client is None:
            raise CommunicationError("BLE 尚未连接")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(self.timeout if timeout is None else timeout)
        except Exception as exc:
            future.cancel()
            raise CommunicationError(f"BLE GATT 操作失败：{exc}") from exc

    def connect(self) -> None:
        """连接设备并订阅打印机通知特征。"""
        if self._thread is not None:
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        if not self._ready.wait(self.timeout + 2):
            self.close()
            raise CommunicationError("BLE 连接超时")
        if self._startup_error is not None:
            error = self._startup_error
            self._thread = None
            raise CommunicationError(f"BLE 连接失败：{error}") from error

    async def _close_async(self) -> None:
        if self._client is not None and self._client.is_connected:
            await self._client.disconnect()
        self._client = None

    def close(self) -> None:
        """断开 BLE 连接并清空接收缓存。"""
        if self._loop is not None and self._thread is not None and self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(self.timeout + 2)
        self._loop = self._thread = self._client = None
        self._buffer.clear()
        while True:
            try:
                self._received.get_nowait()
            except queue.Empty:
                break

    def write(self, data: bytes) -> None:
        """按打印机要求的 GATT 特征和分片大小写入数据。"""
        if not data:
            return
        if self._client is None:
            raise CommunicationError("BLE 尚未连接")
        size = self.chunk_size or 20
        target_uuid = self.NOTIFY_UUID if len(data) == 512 else self.WRITE_UUID

        async def send() -> None:
            import asyncio
            for offset in range(0, len(data), size):
                await self._client.write_gatt_char(target_uuid, data[offset:offset + size], response=False)
                if len(data) > size:
                    await asyncio.sleep(0.01)

        self._submit(send(), timeout=max(self.timeout, len(data) / 1000 + 2))

    def read(self, size: int = 512, timeout: float = 2.0) -> bytes:
        """从通知缓存读取最多指定长度的数据，超时返回空字节串。"""
        if self._client is None:
            raise CommunicationError("BLE 尚未连接")
        deadline = time.monotonic() + timeout
        while not self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return b""
            try:
                self._buffer.extend(self._received.get(timeout=remaining))
            except queue.Empty:
                return b""
        result = bytes(self._buffer[:size])
        del self._buffer[:size]
        return result
