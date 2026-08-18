"""Decode, resize, strip metadata, and encode a bounded static WebP cover."""

from __future__ import annotations

import hashlib
import io
import os
from dataclasses import dataclass
from typing import BinaryIO, Final, Literal

from PIL import Image, ImageOps, UnidentifiedImageError

from catalog.services.errors import ImageTooLarge, InvalidImage

_ACCEPTED_FORMATS: Final[frozenset[str]] = frozenset({"JPEG", "PNG", "WEBP"})


@dataclass(frozen=True, slots=True)
class ImageLimits:
    max_input_bytes: int = 8 * 1024 * 1024
    max_pixels: int = 12_000_000
    max_edge: int = 1_600
    max_output_bytes: int = 1_572_864
    qualities: tuple[int, ...] = (82, 76, 70)
    fallback_edges: tuple[int, ...] = (1_600, 1_400, 1_200)


@dataclass(frozen=True, slots=True)
class NormalizedImage:
    data: bytes
    width: int
    height: int
    byte_size: int
    sha256: str
    content_type: Literal["image/webp"] = "image/webp"


def normalize_image(file: BinaryIO, limits: ImageLimits | None = None) -> NormalizedImage:
    """Return a metadata-free static WebP within ``limits``, or raise a domain error."""
    bounds = limits or ImageLimits()
    _reject_oversized_input(file, bounds)

    previous_max_pixels = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = None
    try:
        return _normalize_opened(file, bounds)
    finally:
        Image.MAX_IMAGE_PIXELS = previous_max_pixels


def _reject_oversized_input(file: BinaryIO, limits: ImageLimits) -> None:
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > limits.max_input_bytes:
        raise ImageTooLarge()
    if size <= 0:
        raise InvalidImage()


def _normalize_opened(file: BinaryIO, limits: ImageLimits) -> NormalizedImage:
    try:
        with Image.open(file) as verified:
            verified.verify()
        file.seek(0)
        with Image.open(file) as image:
            if image.format not in _ACCEPTED_FORMATS:
                raise InvalidImage()
            frames = getattr(image, "n_frames", 1) or 1
            if frames != 1:
                raise InvalidImage()
            width, height = image.size
            if width <= 0 or height <= 0:
                raise InvalidImage()
            if width * height > limits.max_pixels:
                raise ImageTooLarge()
            image.load()
            oriented = ImageOps.exif_transpose(image) or image
            rgb = oriented.convert("RGB")
            return _encode_bounded(rgb, limits)
    except (ImageTooLarge, InvalidImage):
        raise
    except Image.DecompressionBombError:
        raise ImageTooLarge() from None
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, TypeError):
        raise InvalidImage() from None


def _encode_bounded(image: Image.Image, limits: ImageLimits) -> NormalizedImage:
    for edge in limits.fallback_edges:
        resized = _resize_to_edge(image, edge)
        for quality in limits.qualities:
            data = _save_webp(resized, quality)
            if len(data) <= limits.max_output_bytes:
                width, height = resized.size
                return NormalizedImage(
                    data=data,
                    width=width,
                    height=height,
                    byte_size=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                )
    raise ImageTooLarge()


def _resize_to_edge(image: Image.Image, max_edge: int) -> Image.Image:
    width, height = image.size
    longest = max(width, height)
    if longest <= max_edge:
        return image
    scale = max_edge / longest
    new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def _save_webp(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(
        buffer,
        format="WEBP",
        quality=quality,
        method=4,
        exif=b"",
        icc_profile=b"",
    )
    return buffer.getvalue()
