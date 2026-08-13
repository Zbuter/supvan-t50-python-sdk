# 硕方 T50 Python SDK

纯 Python SDK，仅包含两种已验证的连接方式：

- BLE GATT：设备发现、连接、状态查询、标签盒读取和打印。
- Native USB：通过 PyUSB/libusb 直接通信，支持状态、标签盒读取和打印。

## 安装

```powershell
python -m pip install -e ".[all]"
```

只使用 BLE：

```powershell
python -m pip install -e ".[ble]"
```

只使用原生 USB：

```powershell
python -m pip install -e ".[usb]"
```

## 快速测试

```powershell
python examples/ble_print.py --print
python examples/usb_native_print.py --execute
python examples/unified_print.py --backend ble --print
python examples/unified_print.py --backend usb --print
```

完整说明见 [docs/SDK.md](docs/SDK.md)。

`PrintSettings` 的 `material_width`、`material_height`、`gap` 默认从打印机当前
标签盒读取；显式填写的值优先。`PrintPage` 始终保留每页自己的设计尺寸和绘制对象，
不会被标签盒尺寸改写。

## 支持范围

- 文字、中文、字体大小、粗体/下划线/删除线
- Code 128、EAN-13、二维码和图片
- 打印浓度、速度、标签宽高、间隙、多页和份数
- BLE 与原生 USB 状态、标签盒读取、打印和停止
