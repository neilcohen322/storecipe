from __future__ import annotations

import hashlib
import io

from PIL import Image, ImageDraw

from catalog.media.image_processor import ImageLimits, normalize_image
from catalog.services.errors import ImageTooLarge, InvalidImage


def _solid_bytes(
    size: tuple[int, int], fmt: str, *, color: tuple[int, int, int] = (200, 40, 40)
) -> bytes:
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    save_kwargs: dict[str, object] = {"format": fmt}
    if fmt == "JPEG":
        save_kwargs["quality"] = 90
    image.save(buffer, **save_kwargs)
    return buffer.getvalue()


def jpeg_bytes(size: tuple[int, int]) -> bytes:
    return _solid_bytes(size, "JPEG")


def png_bytes(size: tuple[int, int]) -> bytes:
    return _solid_bytes(size, "PNG", color=(20, 120, 80))


def webp_bytes(size: tuple[int, int]) -> bytes:
    return _solid_bytes(size, "WEBP", color=(30, 60, 180))


def _exif_oriented_jpeg() -> bytes:
    image = Image.new("RGB", (100, 50), (255, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 20, 50), fill=(0, 255, 0))
    exif = image.getexif()
    exif[0x0112] = 6
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", exif=exif)
    return buffer.getvalue()


def test_normalizes_to_bounded_metadata_free_webp() -> None:
    result = normalize_image(io.BytesIO(jpeg_bytes((2400, 1200))), ImageLimits())
    assert result.data[:4] == b"RIFF"
    assert max(result.width, result.height) == 1600
    assert result.byte_size <= 1_572_864
    assert result.sha256 == hashlib.sha256(result.data).hexdigest()
    with Image.open(io.BytesIO(result.data)) as encoded:
        assert encoded.format == "WEBP"
        assert encoded.info.get("exif") in {None, b"", b"Exif\x00\x00"}
        assert not encoded.info.get("icc_profile")
        assert getattr(encoded, "n_frames", 1) == 1


def test_accepts_png_and_static_webp() -> None:
    png = normalize_image(io.BytesIO(png_bytes((800, 600))), ImageLimits())
    webp = normalize_image(io.BytesIO(webp_bytes((800, 600))), ImageLimits())
    assert png.data[:4] == b"RIFF"
    assert webp.data[:4] == b"RIFF"
    assert png.byte_size <= 1_572_864
    assert webp.byte_size <= 1_572_864


def test_applies_exif_orientation_then_strips_metadata() -> None:
    result = normalize_image(io.BytesIO(_exif_oriented_jpeg()), ImageLimits())
    assert (result.width, result.height) == (50, 100)
    with Image.open(io.BytesIO(result.data)) as encoded:
        assert encoded.getexif().get(0x0112) in {None, 1}


def test_rejects_pixel_bomb_before_full_processing() -> None:
    try:
        normalize_image(io.BytesIO(jpeg_bytes((4001, 3000))), ImageLimits())
    except ImageTooLarge:
        return
    raise AssertionError("expected ImageTooLarge")


def test_rejects_animation_and_corrupt_bytes() -> None:
    frames = [Image.new("RGB", (32, 32), (i * 40, 0, 0)) for i in range(2)]
    animated = io.BytesIO()
    frames[0].save(
        animated, format="WEBP", save_all=True, append_images=frames[1:], duration=100, loop=0
    )
    try:
        normalize_image(io.BytesIO(animated.getvalue()), ImageLimits())
    except InvalidImage:
        pass
    else:
        raise AssertionError("expected InvalidImage for animation")

    try:
        normalize_image(io.BytesIO(b"not-an-image"), ImageLimits())
    except InvalidImage:
        pass
    else:
        raise AssertionError("expected InvalidImage for corruption")

    try:
        normalize_image(
            io.BytesIO(b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"), ImageLimits()
        )
    except InvalidImage:
        return
    raise AssertionError("expected InvalidImage for svg")


def test_quality_fallback_keeps_hard_output_bound() -> None:
    noisy = Image.frombytes("RGB", (1600, 1600), os_urandom(1600 * 1600 * 3))
    buffer = io.BytesIO()
    noisy.save(buffer, format="JPEG", quality=95)
    result = normalize_image(io.BytesIO(buffer.getvalue()), ImageLimits())
    assert result.byte_size <= 1_572_864
    assert max(result.width, result.height) <= 1600


def os_urandom(size: int) -> bytes:
    import os

    return os.urandom(size)


def test_rejects_unachievable_output_bound() -> None:
    limits = ImageLimits(
        max_output_bytes=80,
        qualities=(70,),
        fallback_edges=(1600, 1400, 1200),
    )
    noisy = Image.frombytes("RGB", (400, 400), os_urandom(400 * 400 * 3))
    buffer = io.BytesIO()
    noisy.save(buffer, format="PNG")
    try:
        normalize_image(io.BytesIO(buffer.getvalue()), limits)
    except ImageTooLarge:
        return
    raise AssertionError("expected ImageTooLarge")


def test_rejects_input_over_eight_mib() -> None:
    class _Counted(io.BytesIO):
        pass

    payload = jpeg_bytes((32, 32)) + b"\x00" * (8 * 1024 * 1024)
    try:
        normalize_image(_Counted(payload), ImageLimits())
    except ImageTooLarge:
        return
    raise AssertionError("expected ImageTooLarge")
