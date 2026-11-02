"""Rack-level models see a copy shrunk by a whole factor; boxes come back full size.

A stand-in model records what it was given and answers boxes in THAT image's
pixels, which is exactly what a real YOLO model does. The tests check the shrink
itself and, more importantly, that every box is multiplied back, because a box
left in the small image's pixels would put every device in the wrong place on
the photo and crop the wrong ports.
"""

import numpy as np

from pipeline.detection import (
    INFERENCE_LONG_SIDE,
    detect_devices_seg,
    shrink_for_inference,
)


def test_a_3000_by_2000_photo_is_shrunk_by_three():
    img = np.zeros((2000, 3000, 3), np.uint8)
    small, factor = shrink_for_inference(img)
    assert factor == 3
    assert small.shape[:2] == (666, 1000)


def test_a_phone_photo_is_shrunk_by_four():
    img = np.zeros((3024, 4032, 3), np.uint8)
    small, factor = shrink_for_inference(img)
    assert factor == 4
    assert small.shape[:2] == (756, 1008)


def test_a_normal_photo_is_left_exactly_as_it_is():
    img = np.zeros((1859, 1395, 3), np.uint8)
    small, factor = shrink_for_inference(img)
    assert factor == 1
    assert small is img, "not even copied"


def test_the_threshold_is_twice_the_target():
    just_under = np.zeros((10, 2 * INFERENCE_LONG_SIDE - 1, 3), np.uint8)
    assert shrink_for_inference(just_under)[1] == 1
    at = np.zeros((10, 2 * INFERENCE_LONG_SIDE, 3), np.uint8)
    assert shrink_for_inference(at)[1] == 2


class _Tensor:
    def __init__(self, a):
        self._a = np.asarray(a, dtype=float)

    def cpu(self):
        return self

    def numpy(self):
        return self._a


class _Boxes:
    def __init__(self, xyxy, cls, conf):
        self.xyxy = _Tensor(xyxy)
        self.cls = _Tensor(cls)
        self.conf = _Tensor(conf)

    def __len__(self):
        return len(self.xyxy.numpy())


class _Result:
    def __init__(self, boxes):
        self.boxes = boxes


class _Model:
    """Answers one switch covering the middle half of whatever it is shown."""

    names = {0: "Switch"}

    def __init__(self):
        self.seen = None

    def __call__(self, img, conf=0.25, iou=0.5):
        self.seen = img.shape[:2]
        h, w = img.shape[:2]
        box = [w * 0.25, h * 0.25, w * 0.75, h * 0.75]
        return [_Result(_Boxes([box], [0], [0.9]))]


def test_the_model_sees_the_small_copy_and_the_box_comes_back_full_size():
    img = np.zeros((3024, 4032, 3), np.uint8)
    model = _Model()
    [dev] = detect_devices_seg(img, model)
    assert model.seen == (756, 1008), "the model was given the shrunk copy"
    x1, y1, x2, y2 = dev["box"]
    # The middle half of the ORIGINAL, less the 2px inset detect_devices_seg
    # applies to every box.
    assert abs(x1 - 1008) <= 4 and abs(x2 - 3024) <= 4
    assert abs(y1 - 756) <= 4 and abs(y2 - 2268) <= 4
    assert dev["center"] == [(x1 + x2) // 2, (y1 + y2) // 2]


def test_a_normal_photo_goes_to_the_model_untouched():
    img = np.zeros((1859, 1395, 3), np.uint8)
    model = _Model()
    [dev] = detect_devices_seg(img, model)
    assert model.seen == (1859, 1395)
    assert abs(dev["box"][0] - 1395 * 0.25) <= 3
