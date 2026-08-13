from __future__ import annotations

from collections.abc import Callable

from PIL import Image

from .models import PrintJob, PrintPage
from .render import render_page

PageMapper = Callable[[PrintPage, PrintJob], PrintPage]
ImageMapper = Callable[[Image.Image, PrintJob], Image.Image]


def render_job_images(
    job: PrintJob,
    *,
    page_mapper: PageMapper | None = None,
    image_mapper: ImageMapper | None = None,
    copy_images: bool = False,
) -> list[Image.Image]:
    """Render a PrintJob into physical label images in the shared print order."""
    rendered_pages: list[tuple[Image.Image, int]] = []
    for page in job.pages:
        render_source = page_mapper(page, job) if page_mapper is not None else page
        image = render_page(render_source)
        if image_mapper is not None:
            image = image_mapper(image, job)
        rendered_pages.append((image, page.repeat))

    ordered: list[Image.Image] = []

    def append_image(image: Image.Image) -> None:
        ordered.append(image.copy() if copy_images else image)

    if job.settings.one_by_one:
        for _ in range(job.settings.copies):
            for image, repeat in rendered_pages:
                for _ in range(repeat):
                    append_image(image)
    else:
        for image, repeat in rendered_pages:
            for _ in range(repeat):
                for _ in range(job.settings.copies):
                    append_image(image)
    return ordered
