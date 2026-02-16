import io
import logging

from PIL import Image, ImageOps, UnidentifiedImageError

from app_context import AppContext

LOG = logging.getLogger(__name__)


def _is_blank_png(png_bytes: bytes) -> bool:
    try:
        with Image.open(io.BytesIO(png_bytes)) as image:
            rgba = image.convert("RGBA")
            if rgba.getchannel("A").getbbox() is None:
                return True
            return ImageOps.invert(rgba.convert("RGB")).getbbox() is None
    except (UnidentifiedImageError, OSError, ValueError):
        return False


async def generate_thumbnail_bytes(
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    collabora_url: str,
    collabora,
) -> bytes:
    png_list = await collabora.convert_document_to_png_collabora(
        file_bytes,
        filename=filename,
        content_type=content_type,
        collabora_base_url=collabora_url,
    )
    if not png_list:
        raise RuntimeError("No PNG pages returned by Collabora convert-to")
    for index, png_page in enumerate(png_list):
        if not _is_blank_png(png_page):
            if index > 0:
                LOG.info(
                    "Selected non-blank thumbnail page index=%s out of %s pages",
                    index,
                    len(png_list),
                )
            return png_page
    LOG.warning("All thumbnail pages appeared blank; falling back to first page")
    return png_list[0]


async def _generate_thumbnail_bytes(
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    collabora_url: str,
    ctx: AppContext | None = None,
    collabora=None,
) -> bytes:
    target_collabora = collabora or (ctx.services.collabora if ctx is not None else None)
    if target_collabora is None:
        raise RuntimeError("Collabora client is required")
    return await generate_thumbnail_bytes(
        file_bytes=file_bytes,
        filename=filename,
        content_type=content_type,
        collabora_url=collabora_url,
        collabora=target_collabora,
    )
