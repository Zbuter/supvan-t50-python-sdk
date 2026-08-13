import pytest

from supvan_t50.errors import ValidationError
from supvan_t50.models import DrawObject, FontStyle, ObjectFormat, PrintJob, PrintPage, PrintSettings
from supvan_t50.protocol import parse_label_box


def test_parse_real_label_box_response_fields() -> None:
    frame = bytes.fromhex(
        "7e5a3900000355306d0a000000000000000000000000001d"
        "f1e4ef131080cb47fa3227048d07b11501281e03fa000000"
        "a406b00419080c11271e000001"
    )
    box = parse_label_box(frame)
    assert box.uuid_hex == "F1E4EF131080CB"
    assert box.code_hex == "47FA3227048D07B1"
    assert box.serial_number == 277
    assert box.type_code == 40
    assert box.width == 40
    assert box.height == 30
    assert box.raw_gap == 3
    assert box.gap == 3
    assert box.remaining == 250
    assert box.template_5mm == 1700
    assert box.template_40mm == 1200
    assert box.timestamp_digits == "081217393000"


def test_parse_label_box_with_shorter_response_prefix() -> None:
    frame = bytes.fromhex(
        "7e5a3900000355306d0a0000000000000000000000001d"
        "f1e4ef131080cb47fa3227048d07b11501281e03fa000000"
        "a406b00419080c11271e00000100"
    )
    box = parse_label_box(frame)
    assert box.uuid_hex == "F1E4EF131080CB"
    assert box.width == 40
    assert box.height == 30
    assert box.gap == 3
    assert box.remaining == 250


def test_format_aliases() -> None:
    assert ObjectFormat.parse("QRCODE") is ObjectFormat.QR_CODE
    assert ObjectFormat.parse("code128") is ObjectFormat.CODE_128


def test_job_total_pages() -> None:
    job = PrintJob([PrintPage(repeat=2), PrintPage(repeat=3)], PrintSettings(copies=2))
    assert job.total_pages == 10


def test_print_settings_resolves_only_missing_media_values() -> None:
    frame = bytes.fromhex(
        "7e5a3900000355306d0a000000000000000000000000001d"
        "f1e4ef131080cb47fa3227048d07b11501281e03fa000000"
        "a406b00419080c11271e000001"
    )
    box = parse_label_box(frame)
    settings = PrintSettings(material_width=40)

    resolved = settings.resolved(box)

    assert resolved.material_width == 40
    assert resolved.material_height == 30
    assert resolved.gap == 3
    assert settings.material_height is None


def test_invalid_copies() -> None:
    with pytest.raises(ValidationError):
        PrintSettings(copies=0)


def test_image_requires_source() -> None:
    with pytest.raises(ValidationError):
        DrawObject(0, 0, 1, 1, format="IMAGE")


def test_font_style_enum_supports_composition_and_integer_compatibility() -> None:
    combined = DrawObject(0, 0, 1, 1, font_style=FontStyle.BOLD | FontStyle.UNDERLINE)
    legacy = DrawObject(0, 0, 1, 1, font_style=1)
    assert combined.font_style == FontStyle.BOLD | FontStyle.UNDERLINE
    assert legacy.font_style is FontStyle.BOLD


def test_font_style_rejects_unknown_bits() -> None:
    with pytest.raises(ValidationError, match="样式位"):
        DrawObject(0, 0, 1, 1, font_style=2)
