from pathlib import Path

from supvan_t50 import (
    BluetoothPrinter,
    DrawObject,
    FontStyle,
    ObjectFormat,
    PrintJob,
    PrintPage,
    PrintSettings,
    preview_job, UsbPrinter,
)


def build_product_label() -> PrintJob:
    return PrintJob(
        settings=PrintSettings(material_width=40, material_height=30, density=3, gap=3,copies=2),
        pages=[
        #     PrintPage(width=40, height=30, objects=[
        #     DrawObject(1.5, 1, 25, 5, "精品咖啡豆", font_size=3.6, font_style=FontStyle.NORMAL),
        #     DrawObject(27, 1, 11.5, 5, "￥39.90元", font_size=3.4, font_style=FontStyle.NORMAL, align=2),
        #     DrawObject(1.5, 6, 25, 3.5, "规格：250g  中度烘焙", font_size=2.2),
        #     DrawObject(1.5, 10, 25, 3.5, "SKU: COFFEE-250", font_size=2.2),
        #     DrawObject(1.5, 14, 25, 10, "6901234567892", format=ObjectFormat.CODE_128),
        #     DrawObject(1.5, 24.5, 25, 3, "6901234567892", font_size=2, align=1),
        #     DrawObject(28, 8, 10, 10, "https://example.com/p/coffee-250", format=ObjectFormat.QR_CODE),
        #     DrawObject(27, 19, 12, 7, "扫码查看\n商品详情", font_size=2.1, align=1, auto_return=True),
        # ]),
               PrintPage(width=40, height=30, objects=[
                   DrawObject(1.5, 1, 25, 5, "精品咖啡豆", font_size=3.6, font_style=FontStyle.NORMAL),
                   DrawObject(27, 1, 11.5, 5, "￥39.90元", font_size=3.4, font_style=FontStyle.NORMAL, align=2),
                   DrawObject(1.5, 6, 25, 3.5, "规格：250g  中度烘焙", font_size=2.2),
                   DrawObject(1.5, 10, 25, 3.5, "SKU: COFFEE-250", font_size=2.2),
                   DrawObject(1.5, 14, 25, 10, "6901234567892", format=ObjectFormat.CODE_128),
                   DrawObject(1.5, 24.5, 25, 3, "6901234567892", font_size=2, align=1),
                   DrawObject(28, 8, 10, 10, "https://example.com/product/coffee-250", format=ObjectFormat.QR_CODE),
                   DrawObject(27, 19, 12, 7, "扫码查看\n商品详情", font_size=2.1, align=1, auto_return=True),
               ])
               ],
    )


def main() -> None:
    job = build_product_label()
    # preview_path = Path(__file__).with_name("product-label-preview.png")
    # preview_job(job, preview_path)
    # print("预览图:", preview_path)

    # devices = BluetoothPrinter.discover("T0")
    # if not devices:
    #     raise RuntimeError("未发现 T0 BLE 打印机")
    #
    # device = devices[0]
    # print(f"连接设备: {device.name} ({device.address}), RSSI={device.rssi}")
    #
    # with BluetoothPrinter.from_device(device) as printer:
    #     status = printer.get_status()
    #     print("蓝牙打印机状态:", status.description)
    #     box = printer.read_label_box()
    #     print("蓝牙标签盒:", box)
    #     # print("打印结果:", printer.print(job))


    print("可用的 USB 打印机设备:",UsbPrinter.list_devices())

    with UsbPrinter() as printer:
        status = printer.get_status()
        print("USB打印机状态:", status.description)
        box = printer.read_label_box()
        print("USB标签盒:", box)
        print("打印结果:", printer.print(job))

if __name__ == "__main__":
    main()
