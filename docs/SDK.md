# SDK 使用说明

## 创建打印任务

```python
from supvan_t50 import (
    DrawObject, FontStyle, ObjectFormat,
    PrintJob, PrintPage, PrintSettings,
)

job = PrintJob(
    settings=PrintSettings(
        material_width=40,
        material_height=30,
        density=4,
        gap=3,
        speed=40,
    ),
    pages=[PrintPage(width=40, height=30, objects=[
        DrawObject(2, 2, 36, 5, "测试商品", font_size=3.5,
                   font_style=FontStyle.BOLD, align=1),
        DrawObject(2, 10, 24, 10, "6901234567892",
                   format=ObjectFormat.CODE_128),
        DrawObject(28, 10, 10, 10, "GOODS-001",
                   format=ObjectFormat.QR_CODE),
    ])],
)
```

## 使用 BLE

```python
from supvan_t50 import BluetoothPrinter

with BluetoothPrinter.from_name("T0123") as printer:
    print(printer.get_status())
    print(printer.read_label_box())
    printer.print(job)
```

T0 BLE 使用服务 `E0FF`，控制写入特征为 `FFE9`，批量写入/通知特征为 `FFE1`。

## 预览标签

```python
from supvan_t50 import preview_job

images = preview_job(job)
preview_job(job, "label-preview.png")
```

`preview_job()` 返回按实际打印顺序渲染的 Pillow 图片列表。指定输出路径时，单页写入该文件；多页自动写成 `label-preview-1.png`、`label-preview-2.png` 等文件。

## 使用原生 USB

```python
from supvan_t50 import UsbPrinter

devices = UsbPrinter.list_devices()
if not devices:
    raise RuntimeError("未发现 USB 打印机")

with UsbPrinter(devices[0]) as printer:
    print(printer.get_status())
    printer.print(job)
```

Windows 使用 `libusb-package` 提供运行库。SDK 直接访问 HID 接口，不调用桥接 EXE 或 .NET DLL。

## 高层对象

| 对象 | 用途 |
| --- | --- |
| `PrintSettings` | 标签宽高、浓度、速度、间隙、份数和方向 |
| `PrintPage` | 一张物理标签及其绘制对象 |
| `DrawObject` | 文字、条码、二维码或图片 |
| `FontStyle` | `NORMAL`、`BOLD`、`UNDERLINE`、`STRIKEOUT`，可组合 |
| `BluetoothPrinter` | BLE 打印机客户端 |
| `UsbPrinter` | 原生 USB 打印机客户端 |
| `print_job()` | 可选的 BLE/USB 通用辅助函数，会先检查设备状态 |

`font_name` 可以使用字体族名或本机字体文件路径；跨机器部署时应随应用管理字体文件。

##  API 参考

### BLE 设备发现与连接

#### `BluetoothPrinter.discover(name_prefix="T0", timeout=8.0)`

扫描附近的 BLE 打印机。

- `name_prefix`：设备名前缀，T50 系列通常使用 `T0`。
- `timeout`：扫描秒数。
- 返回：`list[BleDeviceInfo]`，已按信号强度从高到低排序。

#### `BluetoothPrinter.from_device(device, timeout=10.0, print_timeout=120.0, chunk_size=None)`

使用扫描结果创建 BLE 打印机。

#### `BluetoothPrinter.from_name(name, timeout=10.0, scan_timeout=8.0, print_timeout=120.0, chunk_size=None)`

扫描并按完整设备名创建 BLE 打印机。名称匹配不区分大小写；存在同名设备时选择信号最强的一个。

#### `BluetoothPrinter.from_address(address, timeout=10.0, print_timeout=120.0, chunk_size=None)`

使用已知 BLE MAC 地址或平台设备地址创建 BLE 打印机。

#### `BluetoothPrinter(transport, timeout=3.0, print_timeout=120.0)`

使用自定义传输创建 BLE 打印机，主要供协议调试和高级扩展使用。普通调用应使用上述 `from_*()` 方法。

- `transport`：`BleTransport` 实例。
- `timeout`：普通控制命令超时秒数。
- `print_timeout`：等待打印完成的最大秒数。

#### `BluetoothPrinter.connect()` / `close()`

连接或断开打印机。推荐使用 `with BluetoothPrinter(...) as printer:` 自动管理连接。

#### `BluetoothPrinter.get_status(timeout=None)`

读取设备状态，返回 `PrinterStatus`。可查看温度、电压、上盖、耗材、忙碌和打印中等字段。

#### `BluetoothPrinter.read_label_box(timeout=None)`

读取标签盒，返回 `LabelBoxInfo`，包括标签类型、宽高、间隙、余量和原始数据。

#### `BluetoothPrinter.print(job)`

打印 `PrintJob`。完成返回 `True`；设备异常抛出 `DeviceError`，通信超时抛出 `CommunicationError`。

### 原生 USB

#### `UsbPrinter.list_devices()`

列出当前支持的 USB 打印机，返回类似 `usb:1820:2076:2:5` 的设备标识。

#### `UsbPrinter(device_path="", timeout=120.0)`

创建原生 USB 客户端。`device_path` 为空时选择首个支持设备。

#### `UsbPrinter.get_status()`

读取并返回 `PrinterStatus`。

#### `UsbPrinter.print(job)`

打印 `PrintJob`，传输并等待设备实际完成。成功返回 `True`。

#### `UsbPrinter.stop()`

停止当前任务。停止成功或设备原本空闲时返回 `True`。

#### `UsbPrinter.close()`

释放 USB HID 接口。推荐使用 `with UsbPrinter(...) as printer:`。

### 打印任务模型

#### `PrintSettings`

| 参数 | 中文说明 |
| --- | --- |
| `material_width` / `material_height` | 标签宽高，单位毫米 |
| `copies` | 整个任务打印份数，1 到 99 |
| `density` | 打印浓度，0 到 9 |
| `speed` | 打印速度，20 到 60 mm/s |
| `paper_type` | `GAP` 间隙纸、`BLACK_MARK` 黑标纸等 |
| `gap` | 标签间隙，0 到 8 mm |
| `rotate` | 页面旋转值 |
| `horizontal_offset` / `vertical_offset` | BLE 和 USB 通用图像偏移，单位打印点，范围 -9 到 9 |
| `direction` | 原生 USB 页面方向 |
| `one_by_one` | 多页多份时是否按整套顺序打印 |

#### `PrintPage(width, height, repeat=1, objects=[])`

表示一张物理标签。`repeat` 控制该页重复次数，`objects` 是绘制对象列表。

#### `DrawObject`

| 参数 | 中文说明 |
| --- | --- |
| `x` / `y` | 左上角坐标，单位毫米 |
| `width` / `height` | 对象区域宽高，单位毫米 |
| `content` | 文字、条码或二维码内容 |
| `format` | `TEXT`、`EAN_13`、`CODE_128`、`QR_CODE`、`IMAGE` |
| `font_name` | 字体族名或字体文件路径 |
| `font_size` | 字号，按毫米渲染 |
| `font_style` | `FontStyle`，支持组合 |
| `align` | 0 左、1 中、2 右 |
| `anti_color` | 是否反色 |
| `image` | 图片对象或图片路径 |

#### `PrintJob(pages, settings=PrintSettings())`

完整打印任务。`total_pages` 属性返回计算份数和重复次数后的总标签数。

#### `print_job(printer, job)`

BLE 和 USB 共用入口。它会先检查设备是否就绪，再调用具体打印机的 `print()`。

### 状态与异常

- `PrinterStatus.ready`：设备是否可以接收新任务。
- `PrinterStatus.state`：统一设备状态枚举。
- `PrinterStatus.printed_pages`：设备报告的已打印页数。
- `PrinterStatus.raw`：原始设备响应，适合诊断。
- `DependencyError`：缺少 bleak、PyUSB 或 libusb。
- `ValidationError`：任务参数不合法。
- `CommunicationError`：连接、读写或等待响应超时。
- `DeviceError`：上盖、耗材、温度或打印任务异常。

## 真机注意事项

- BLE 设备名通常以 `T0` 开头，不需要在系统设置中配对。
- USB 必须使用数据线；仅充电线无法枚举设备。
- 二维码应保留足够尺寸和空白边界，避免内容过长导致模块过密。
- 条码区域应预留足够宽度；EAN-13 内容必须符合格式要求。
- 打印成功应以实际出纸和设备完成状态共同确认。

## 断点调试 BLE

BLE 命令依赖后台事件线程接收 GATT 通知。调试时应注意：

- 不要在 Watch、悬停提示或 Debug Console 中执行 `printer.read_label_box()`、`get_status()`、`print()` 等硬件 I/O 方法。
- 应将调用写成普通代码，例如 `box = printer.read_label_box()`，在下一行设置断点，然后使用“单步跳过”执行。
- 断点的暂停方式应选择仅暂停当前线程，避免冻结 BLE 通知线程。
- 项目已提供 `.vscode/launch.json`，会延长调试求值超时，并允许等待时临时恢复其他线程。

推荐调试代码：

```python
with BluetoothPrinter.from_address(address) as printer:
    status = printer.get_status()
    label_box = printer.read_label_box()
    # 在这一行设置断点，查看 status 和 label_box；不要在 Watch 中重新调用方法。
    print(status, label_box)
```
