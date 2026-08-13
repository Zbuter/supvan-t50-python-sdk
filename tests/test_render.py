import pytest

from supvan_t50.errors import ValidationError
from supvan_t50.models import DrawObject, PrintPage
from supvan_t50.render import load_font, render_page


def test_render_text_page() -> None:
    image = render_page(PrintPage(width=40, height=30, objects=[DrawObject(1, 1, 30, 5, "SUPVAN", font_size=4)]))
    assert image.size == (320, 240)
    assert image.getextrema()[0] < 255


def test_missing_cjk_font_is_not_silently_replaced() -> None:
    with pytest.raises(ValidationError, match="中文字形"):
        load_font("definitely-missing-font-xyz.ttf", 20, text="中文")


def test_missing_latin_font_can_use_safe_fallback() -> None:
    assert load_font("definitely-missing-font-xyz.ttf", 20, text="ABC")


def test_auto_return_supports_explicit_newlines() -> None:
    image = render_page(PrintPage(
        width=20,
        height=10,
        objects=[DrawObject(1, 1, 18, 8, "第一行\n第二行", auto_return=True)],
    ))
    assert image.getextrema()[0] < 255
