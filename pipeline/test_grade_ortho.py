import unittest

import numpy as np

import argparse

from pipeline.grade_ortho import (
    GradeParams,
    boost_saturation,
    grade_image,
    gray_world_gains,
    params_from_args,
    stretch_levels,
)


def synthetic_aerial(seed: int = 3) -> np.ndarray:
    """Low-contrast, green-tinted noise — a stand-in for a hazy survey ortho."""
    rng = np.random.default_rng(seed)
    base = rng.normal(120, 18, (256, 256, 3))
    base[..., 1] += 10  # green cast
    return np.clip(base, 0, 255).astype(np.uint8)


class GradeOrthoTests(unittest.TestCase):
    def test_gray_world_gains_are_clamped(self):
        image = synthetic_aerial()
        r, g, b = gray_world_gains(image, limit=0.08)
        for gain in (r, g, b):
            self.assertGreaterEqual(gain, 0.92)
            self.assertLessEqual(gain, 1.08)
        # The green cast must pull the green gain BELOW neutral.
        self.assertLess(g, 1.0)

    def test_stretch_expands_low_contrast_luminance(self):
        image = synthetic_aerial()
        out = stretch_levels(image, GradeParams())
        self.assertEqual(out.shape, image.shape)
        self.assertEqual(out.dtype, np.uint8)
        self.assertGreater(float(out.std()), float(image.std()))

    def test_stretch_leaves_flat_image_untouched(self):
        flat = np.full((64, 64, 3), 128, dtype=np.uint8)
        out = stretch_levels(flat, GradeParams())
        np.testing.assert_array_equal(out, flat)

    def test_saturation_keeps_neutral_gray_neutral(self):
        gray = np.full((32, 32, 3), 100, dtype=np.uint8)
        out = boost_saturation(gray, GradeParams())
        diff = out.astype(int) - gray.astype(int)
        self.assertLessEqual(int(np.abs(diff).max()), 2)  # LAB roundtrip noise only

    def test_grade_is_shape_preserving_and_deterministic(self):
        image = synthetic_aerial()
        out1 = grade_image(image)
        out2 = grade_image(image)
        self.assertEqual(out1.shape, image.shape)
        self.assertEqual(out1.dtype, np.uint8)
        np.testing.assert_array_equal(out1, out2)

    def test_grade_increases_contrast_and_saturation(self):
        image = synthetic_aerial()
        out = grade_image(image)
        self.assertGreater(float(out.std()), float(image.std()))
        # Channel spread (a rough chroma proxy) should not shrink.
        chroma_in = float(np.abs(image.astype(int)[..., 1] - image.astype(int)[..., 2]).mean())
        chroma_out = float(np.abs(out.astype(int)[..., 1] - out.astype(int)[..., 2]).mean())
        self.assertGreaterEqual(chroma_out, chroma_in * 0.9)


class ParamsFromArgsTests(unittest.TestCase):
    def _ns(self, **kw):
        fields = ("wb_gain_limit", "black_percentile", "white_percentile",
                  "stretch_strength", "clahe_clip", "clahe_tile_px", "saturation")
        return argparse.Namespace(**{f: kw.get(f) for f in fields})

    def test_all_none_reproduces_defaults(self):
        self.assertEqual(params_from_args(self._ns()), GradeParams())

    def test_overrides_apply(self):
        params = params_from_args(self._ns(clahe_clip=3.0, saturation=1.25,
                                           clahe_tile_px=96))
        self.assertEqual(params.clahe_clip, 3.0)
        self.assertEqual(params.saturation, 1.25)
        self.assertEqual(params.clahe_tile_px, 96)
        # Untouched fields keep the dataclass default.
        self.assertEqual(params.stretch_strength, GradeParams().stretch_strength)


if __name__ == "__main__":
    unittest.main()
