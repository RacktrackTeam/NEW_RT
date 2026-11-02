"""The warm worker replaces cv2.imread process-wide, so every library loaded
beside it — easyocr above all — reads its pictures through that replacement.
A stand-in for cv2.imread has to return what cv2.imread returns, shape
included: easyocr's get_image_list unpacks a grayscale read as
`maximum_y, maximum_x = img.shape` and dies on anything with a third axis.
It did, on every label read, and the server hid it behind a cold-process
fallback that cost 15-30 seconds a photo instead of failing out loud."""

import cv2
import numpy as np
import pytest

from pipeline.worker import _safe_imread


@pytest.fixture
def picture(tmp_path):
    """A small BGR JPEG on disk, mid-grey so no channel is clipped."""
    path = tmp_path / "rack.jpg"
    img = np.full((48, 64, 3), 128, dtype=np.uint8)
    img[:, 32:] = 200
    assert cv2.imwrite(str(path), img)
    return str(path)


def test_grayscale_read_is_two_dimensional(picture):
    """IMREAD_GRAYSCALE promises (h, w). A third axis broke every label read."""
    im = _safe_imread(picture, cv2.IMREAD_GRAYSCALE)
    assert im is not None
    assert im.ndim == 2
    assert im.shape == (48, 64)


def test_grayscale_read_unpacks_the_way_easyocr_unpacks_it(picture):
    """The exact line that raised: easyocr/utils.py get_image_list."""
    maximum_y, maximum_x = _safe_imread(picture, cv2.IMREAD_GRAYSCALE).shape
    assert (maximum_y, maximum_x) == (48, 64)


def test_colour_read_keeps_its_three_channels(picture):
    """The default, and what the ultralytics callers this patch exists for use."""
    im = _safe_imread(picture)
    assert im is not None
    assert im.shape == (48, 64, 3)


def test_it_matches_opencvs_own_decode(picture):
    """Same bytes, same flags, same array — the patch is a transport detail."""
    with open(picture, "rb") as f:
        expected = cv2.imdecode(np.frombuffer(f.read(), np.uint8), cv2.IMREAD_GRAYSCALE)
    assert np.array_equal(_safe_imread(picture, cv2.IMREAD_GRAYSCALE), expected)


def test_a_missing_file_is_none_not_an_exception(tmp_path):
    """cv2.imread returns None for what it cannot read; callers test for None."""
    assert _safe_imread(str(tmp_path / "nothing.jpg")) is None


def test_an_empty_file_is_none(tmp_path):
    """A truncated upload decodes to nothing rather than raising."""
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    assert _safe_imread(str(empty)) is None
