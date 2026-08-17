from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum, IntFlag
from os import PathLike
from typing import Any

from .errors import ValidationError


class PaperType(IntEnum):
    """标签耗材定位方式。"""
    GAP = 1  #: 间隙纸，通过标签之间的空隙定位。
    BLACK_MARK = 2  #: 黑标纸，通过背面的黑色标记定位。
    BLACK_MARK_CARD = 5  #: 黑标卡纸，适用于较厚的黑标耗材。


class ObjectFormat(str, Enum):
    """标签绘制对象的内容格式。"""
    TEXT = "TEXT"  #: 普通文字。
    EAN_13 = "EAN_13"  #: EAN-13 商品条形码。
    CODE_128 = "CODE_128"  #: Code 128 条形码。
    QR_CODE = "QR_CODE"  #: 二维码。
    IMAGE = "IMAGE"  #: 图片。

    @classmethod
    def parse(cls, value: "ObjectFormat | str") -> "ObjectFormat":
        """把枚举或兼容字符串转换为 ``ObjectFormat``。"""
        if isinstance(value, cls):
            return value
        normalized = value.strip().upper().replace("-", "_")
        aliases = {"QRCODE": "QR_CODE", "CODE128": "CODE_128", "EAN13": "EAN_13"}
        return cls(aliases.get(normalized, normalized))


class FontStyle(IntFlag):
    """字体样式位，可使用 ``|`` 组合多个样式。"""
    NORMAL = 0  #: 常规字体。
    BOLD = 1  #: 粗体。
    UNDERLINE = 4  #: 下划线。
    STRIKEOUT = 8  #: 删除线。


@dataclass
class DrawObject:
    """标签上的一个绘制对象。

    坐标和宽高单位均为毫米；``format`` 决定内容按文字、条码、二维码
    或图片渲染。``align`` 为 0 左对齐、1 居中、2 右对齐。
    """
    x: float  #: 对象左上角横坐标，单位毫米。
    y: float  #: 对象左上角纵坐标，单位毫米。
    width: float  #: 对象可用区域宽度，单位毫米。
    height: float  #: 对象可用区域高度，单位毫米。
    content: str = ""  #: 文字、条码或二维码的内容；图片也可填写文件路径。
    format: ObjectFormat | str = ObjectFormat.TEXT  #: 内容类型，决定使用哪种渲染方式。
    font_name: str = "黑体"  #: 字体族名或字体文件路径，主要用于文字对象。
    font_size: float = 3.0  #: 字号，按毫米换算为打印像素。
    font_style: FontStyle | int = FontStyle.NORMAL  #: 字体样式，可组合粗体、下划线和删除线。
    align: int = 0  #: 水平对齐：0 左对齐、1 居中、2 右对齐。
    anti_color: bool = False  #: 是否反色显示，即黑底白字/白图。
    auto_return: bool = False  #: 是否允许文字在对象区域内自动换行。
    image: Any | PathLike[str] | str | None = None  #: 图片对象、图片路径或兼容的图像来源。

    def __post_init__(self) -> None:
        self.format = ObjectFormat.parse(self.format)
        try:
            self.font_style = FontStyle(self.font_style)
        except (TypeError, ValueError) as exc:
            raise ValidationError("font_style 必须是 FontStyle 或兼容整数") from exc
        if int(self.font_style) & ~int(FontStyle.BOLD | FontStyle.UNDERLINE | FontStyle.STRIKEOUT):
            raise ValidationError("font_style 包含不支持的样式位")
        if min(self.x, self.y, self.width, self.height) < 0 or self.width == 0 or self.height == 0:
            raise ValidationError("绘制对象的坐标不能为负，宽高必须大于 0")
        if self.align not in (0, 1, 2):
            raise ValidationError("align 必须是 0（左）、1（中）或 2（右）")
        if self.format == ObjectFormat.IMAGE and self.image is None and not self.content:
            raise ValidationError("IMAGE 对象必须提供 image 或 content 文件路径")


@dataclass
class PrintPage:
    """一张物理标签页面，可包含多个 ``DrawObject``。"""
    width: float = 48.0  #: 页面设计宽度，单位毫米。
    height: float = 30.0  #: 页面设计高度，单位毫米。
    repeat: int = 1  #: 当前页面连续重复打印的次数。
    objects: list[DrawObject] = field(default_factory=list)  #: 页面上的绘制对象列表。

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValidationError("页面宽高必须大于 0")
        if self.repeat < 1:
            raise ValidationError("repeat 必须大于等于 1")


@dataclass
class PrintSettings:
    """打印任务设置。

    包含耗材宽高、打印份数、浓度、速度、间隙、旋转和偏移等参数。
    """
    material_width: int | None = None  #: 实际标签宽度；None 表示从标签盒读取。
    material_height: int | None = None  #: 实际标签高度；None 表示从标签盒读取。
    copies: int = 1  #: 整个打印任务的份数，范围 1 到 99。
    rotate: int = 0  #: BLE 页面旋转值：0 不旋转，1/2/3 对应 90/180/270 度。
    density: int = 4  #: 打印浓度，范围 0 到 9，数值越大颜色通常越深。
    horizontal_offset: int = 0  #: 通用水平偏移，单位打印点，BLE 和 USB 均使用，范围 -9 到 9。
    vertical_offset: int = 0  #: 通用垂直偏移，单位打印点，BLE 和 USB 均使用，范围 -9 到 9。
    paper_type: PaperType | int = PaperType.GAP  #: 标签定位方式，如间隙纸或黑标纸。
    gap: int | None = None  #: 标签间隙；None 表示从标签盒读取。
    one_by_one: bool = True  #: 多页多份时，True 按整套页面顺序打印，False 按页面集中打印。
    tail_length: int = 0  #: 打印结束后的尾部走纸长度，单位由设备协议定义。
    direction: int = 0  #: 原生 USB 图像方向：0 原方向，1/2/3 为设备支持的旋转方向。
    speed: int = 40  #: 打印速度，单位 mm/s，范围 20 到 60。
    max_dot_value: int = 384  #: 打印头最大有效点数，T50 默认 384 点。
    dpi: float = 8.0  #: 每毫米像素数，默认 8，约等于 203 DPI。
    label_sn: str = ""  #: 标签耗材序列号；普通打印通常留空。
    cut_type: int = 0  #: 切纸方式；无切刀的机型保持 0。

    def __post_init__(self) -> None:
        self.paper_type = PaperType(int(self.paper_type))
        if not 1 <= self.copies <= 99:
            raise ValidationError("copies 范围为 1-99")
        if self.rotate not in (0, 1, 2, 3, 4):
            raise ValidationError("rotate 必须是 0-4")
        if not 0 <= self.density <= 9:
            raise ValidationError("density 范围为 0-9")
        if not -9 <= self.horizontal_offset <= 9 or not -9 <= self.vertical_offset <= 9:
            raise ValidationError("水平/垂直偏移范围为 -9 到 9 个打印点")
        if self.gap is not None and not 0 <= self.gap <= 8:
            raise ValidationError("gap 范围为 0-8 mm")
        if not 20 <= self.speed <= 60:
            raise ValidationError("speed 范围为 20-60 mm/s")
        if self.direction not in (0, 1, 2, 3):
            raise ValidationError("direction 必须是 0-3")
        if self.max_dot_value <= 0 or self.max_dot_value > 384:
            raise ValidationError("max_dot_value 范围为 1-384")
        if self.dpi <= 0:
            raise ValidationError("dpi 必须大于 0")
        if self.material_width is not None and self.material_width <= 0:
            raise ValidationError("耗材宽高必须大于 0")
        if self.material_height is not None and self.material_height <= 0:
            raise ValidationError("耗材宽高必须大于 0")

    @property
    def needs_label_box(self) -> bool:
        """是否有介质参数需要从当前标签盒补齐。"""
        return self.material_width is None or self.material_height is None or self.gap is None

    def resolved(self, label_box: "LabelBoxInfo") -> "PrintSettings":
        """返回用标签盒补齐介质参数的新设置，显式参数保持不变。"""
        return replace(
            self,
            material_width=self.material_width if self.material_width is not None else label_box.width,
            material_height=self.material_height if self.material_height is not None else label_box.height,
            gap=self.gap if self.gap is not None else label_box.gap,
        )


@dataclass
class PrintJob:
    """可提交给 BLE 或原生 USB 打印机的完整打印任务。"""
    pages: list[PrintPage]  #: 要打印的页面列表，至少包含一页。
    settings: PrintSettings = field(default_factory=PrintSettings)  #: 应用于整个任务的打印设置。

    def __post_init__(self) -> None:
        if not self.pages:
            raise ValidationError("打印任务至少需要一个页面")

    @property
    def total_pages(self) -> int:
        """返回计入页面重复次数和任务份数后的总标签数。"""
        return self.settings.copies * sum(page.repeat for page in self.pages)

    def with_settings(self, settings: PrintSettings) -> "PrintJob":
        """返回替换设置的新任务，不修改页面或原任务。"""
        return replace(self, settings=settings)


@dataclass(frozen=True)
class LabelBoxInfo:
    """BLE 标签盒读取结果，包含类型、尺寸、余量和原始数据。"""
    uuid_hex: str  #: 标签盒 UUID，使用大写十六进制字符串表示。
    code_hex: str  #: 标签盒产品/识别编码，使用大写十六进制字符串表示。
    serial_number: int  #: 标签盒序列号。
    type_code: int  #: 标签类型代码，可用于区分耗材类别。
    raw_height: int  #: 设备原始高度字段，未做安全裁剪。
    height: int  #: 解析后的标签高度，单位毫米。
    width: int  #: 解析后的标签宽度，单位毫米。
    raw_gap: int  #: 设备原始标签间隙字段。
    gap: int  #: 解析后的标签间隙，单位毫米。
    remaining: int  #: 设备报告的标签余量原始数值。
    template_5mm: int  #: 标签盒内与 5 mm 模板/刻度相关的原始字段。
    template_40mm: int  #: 标签盒内与 40 mm 模板/刻度相关的原始字段。
    timestamp_digits: str  #: 标签盒时间字段拼接后的数字字符串。
    raw: bytes  #: 完整的标签盒原始响应，供诊断或后续协议分析使用。

    # Compatibility aliases for the first implementation draft.
    @property
    def uuid(self) -> str:
        """返回标签盒 UUID 的十六进制字符串。"""
        return self.uuid_hex

    @property
    def code(self) -> str:
        """返回标签盒编码的十六进制字符串。"""
        return self.code_hex

    @property
    def label_type(self) -> int:
        """返回标签类型代码。"""
        return self.type_code

    @property
    def height_mm(self) -> int:
        """返回标签高度，单位毫米。"""
        return self.height

    @property
    def width_mm(self) -> int:
        """返回标签宽度，单位毫米。"""
        return self.width

    @property
    def gap_mm(self) -> int:
        """返回标签间隙，单位毫米。"""
        return self.gap

