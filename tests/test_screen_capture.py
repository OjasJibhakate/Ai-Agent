"""Screen frames: PNG writer, downscaling, coordinate mapping, change detection (no desktop)."""

import struct
import zlib

import numpy as np
import pytest

from ragnarok.skills.screen.capture import Frame, downscale, encode_png


def decode_png(data: bytes) -> np.ndarray:
    """A minimal PNG reader for 8-bit RGB images with filter type 0 (what encode_png writes)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, chunks = 8, {}
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        tag, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        assert crc == zlib.crc32(tag + body), f"bad CRC in {tag!r}"
        chunks.setdefault(tag, []).append(body)
        pos += 12 + length
    w, h, depth, color, comp, filt, interlace = struct.unpack(">IIBBBBB", chunks[b"IHDR"][0])
    assert (depth, color, comp, filt, interlace) == (8, 2, 0, 0, 0)
    raw = zlib.decompress(b"".join(chunks[b"IDAT"]))
    rows = np.frombuffer(raw, np.uint8).reshape(h, 1 + w * 3)
    assert (rows[:, 0] == 0).all()  # every row uses filter type 0
    return rows[:, 1:].reshape(h, w, 3)


def test_png_round_trips_exactly():
    rng = np.random.default_rng(0)
    img = rng.integers(0, 256, (37, 53, 3), dtype=np.uint8)
    assert np.array_equal(decode_png(encode_png(img)), img)


def test_png_of_a_flat_screen_is_small():
    img = np.full((720, 1280, 3), 243, np.uint8)
    assert len(encode_png(img)) < 20_000  # a mostly empty desktop does not cost megabytes


def test_downscale_averages_instead_of_dropping_pixels():
    img = np.zeros((4, 4, 3), np.uint8)
    img[:, 2:] = 200  # left half black, right half grey
    small = downscale(img, 2)
    assert small.shape == (2, 2, 3)
    assert (small[:, 0] == 0).all() and (small[:, 1] == 200).all()
    checker = np.indices((4, 4)).sum(axis=0) % 2 * 200
    smooth = downscale(np.repeat(checker[:, :, None], 3, axis=2).astype(np.uint8), 2)
    assert (smooth == 100).all()  # the average of black and 200, not one or the other


@pytest.mark.parametrize(
    ("w", "h", "target"), [(1920, 1080, 1280), (2560, 1440, 1280), (1366, 768, 1280)]
)
def test_downscale_keeps_the_aspect_ratio(w, h, target):
    out = downscale(np.zeros((h, w, 3), np.uint8), target)
    assert out.shape[1] == target and abs(out.shape[0] - round(h * target / w)) <= 1


def test_small_images_are_left_alone():
    img = np.zeros((100, 200, 3), np.uint8)
    assert downscale(img, 1280) is img


def frame(left=0, top=0, w=1920, h=1080):
    return Frame(np.zeros((h // 2, w // 2, 3), np.uint8), left, top, w, h)


def test_to_screen_maps_the_0_1000_scale_to_physical_pixels():
    f = frame()
    assert f.to_screen(0, 0) == (0, 0)
    assert f.to_screen(1000, 1000) == (1919, 1079)  # the last pixel, not one past the edge
    assert f.to_screen(500, 500) == (960, 540)
    assert f.to_screen(100, 900) == (192, 971)


def test_to_screen_clamps_nonsense_and_adds_the_region_offset():
    f = frame(left=60, top=80, w=1000, h=650)
    assert f.to_screen(-50, 2000) == (60, 80 + 649)
    assert f.to_screen(0, 0) == (60, 80)
    assert f.to_screen(1000, 0) == (60 + 999, 80)


def test_change_detection_ignores_noise_but_sees_a_real_change():
    base = np.full((720, 1280, 3), 240, np.uint8)
    a = Frame(base, 0, 0, 1280, 720)
    noisy = base.copy()
    noisy[10, 10] = 0  # one dark pixel (a blinking cursor)
    assert a.looks_like(Frame(noisy, 0, 0, 1280, 720))
    changed = base.copy()
    changed[100:400, 200:800] = 30  # a window opened
    assert not a.looks_like(Frame(changed, 0, 0, 1280, 720))
    assert not a.looks_like(Frame(base[:360], 0, 0, 1280, 720))  # different size: not the same


def test_change_detection_sees_a_few_typed_words_but_not_a_blinking_cursor():
    base = np.full((720, 1280, 3), 245, np.uint8)
    a = Frame(base, 0, 0, 1280, 720)
    caret = base.copy()
    caret[300:318, 400:402] = 0  # a 2x18 text cursor appearing
    assert a.looks_like(Frame(caret, 0, 0, 1280, 720))
    typed = base.copy()
    for i in range(5):  # five "letters": thin dark strokes, about 150 pixels in all
        typed[300:316, 400 + i * 12 : 400 + i * 12 + 2] = 20
        typed[308:310, 400 + i * 12 : 400 + i * 12 + 8] = 20
    assert not a.looks_like(Frame(typed, 0, 0, 1280, 720))
    faint = base.copy()
    faint[0:200, 0:200] = 235  # a tiny colour drift everywhere: still the same picture
    assert a.looks_like(Frame(faint, 0, 0, 1280, 720))


def test_encoding_does_not_touch_the_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    encode_png(np.zeros((50, 50, 3), np.uint8))
    downscale(np.zeros((100, 100, 3), np.uint8), 50)
    assert list(tmp_path.iterdir()) == []
