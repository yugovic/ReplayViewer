import math
import unittest

import cv2
import numpy as np

from pipeline.enhance_track_corridor import (
    build_corridor_alpha,
    corridor_shift_stats,
    local_to_pixel,
    lr_consistency_psnr,
    pre_sharpen,
    select_strength,
    shift_probe_points,
)


class CorridorEnhancementTests(unittest.TestCase):
    def test_local_origin_maps_inside_fuji_image(self):
        meta = {
            "imageWidth": 8014,
            "imageHeight": 8192,
            "mercator": True,
            "bbox": {
                "minLng": 138.91723261,
                "maxLng": 138.93658159,
                "minLat": 35.36246909,
                "maxLat": 35.37859744,
            },
        }
        px, py = local_to_pixel(0, 0, {"lat": 35.3717, "lng": 138.9256}, meta)
        self.assertGreater(px, 0)
        self.assertLess(px, meta["imageWidth"])
        self.assertGreater(py, 0)
        self.assertLess(py, meta["imageHeight"])

    def test_corridor_alpha_has_core_fade_and_zero_exterior(self):
        alpha = build_corridor_alpha(
            101, 101, [(10, 50), (90, 50)], metres_per_pixel=1,
            core_metres=5, outer_metres=15,
        )
        self.assertEqual(int(alpha[50, 50]), 255)
        self.assertGreater(int(alpha[60, 50]), 0)
        self.assertLess(int(alpha[60, 50]), 255)
        self.assertEqual(int(alpha[80, 50]), 0)
        self.assertEqual(alpha.dtype, np.uint8)


class PreSharpenTests(unittest.TestCase):
    def test_zero_amount_is_identity(self):
        rng = np.random.default_rng(5)
        img = rng.integers(0, 255, (48, 48, 3), dtype=np.uint8)
        np.testing.assert_array_equal(pre_sharpen(img, 0.0, 1.0), img)

    def test_positive_amount_raises_local_contrast(self):
        rng = np.random.default_rng(5)
        img = cv2.GaussianBlur(
            rng.integers(0, 255, (64, 64, 3), dtype=np.uint8), (0, 0), 1.5)
        sharp = pre_sharpen(img, 0.6, 1.0)
        self.assertEqual(sharp.shape, img.shape)
        self.assertEqual(sharp.dtype, np.uint8)
        # Unsharp masking expands the tonal range (edge overshoot/undershoot).
        self.assertGreater(float(sharp.std()), float(img.std()))


class PerceptualGateTests(unittest.TestCase):
    def test_select_strength_prefers_lowest_dists(self):
        curve = [
            {"strength": 0.0, "dists": 0.20, "lpips": 0.30},
            {"strength": 0.5, "dists": 0.12, "lpips": 0.22},
            {"strength": 1.0, "dists": 0.15, "lpips": 0.18},
        ]
        strength, rule = select_strength(curve)
        self.assertEqual(strength, 0.5)
        self.assertIn("DISTS", rule)

    def test_select_strength_breaks_ties_with_lpips(self):
        curve = [
            {"strength": 0.6, "dists": 0.10, "lpips": 0.25},
            {"strength": 0.8, "dists": 0.10, "lpips": 0.20},
        ]
        strength, _rule = select_strength(curve)
        self.assertEqual(strength, 0.8)

    def test_lr_consistency_high_for_faithful_upscale(self):
        rng = np.random.default_rng(7)
        native = cv2.GaussianBlur(
            rng.integers(0, 255, (96, 96, 3), dtype=np.uint8), (0, 0), 2.0,
        )
        enhanced = cv2.resize(native, None, fx=2, fy=2, interpolation=cv2.INTER_LANCZOS4)
        self.assertGreater(lr_consistency_psnr(enhanced, native), 35.0)

    def test_lr_consistency_low_for_invented_structure(self):
        rng = np.random.default_rng(7)
        native = np.full((96, 96, 3), 128, dtype=np.uint8)
        enhanced = rng.integers(0, 255, (192, 192, 3), dtype=np.uint8)
        self.assertLess(lr_consistency_psnr(enhanced, native), 20.0)

    def test_corridor_shift_detects_known_translation(self):
        rng = np.random.default_rng(11)
        base = cv2.GaussianBlur(
            rng.integers(0, 255, (256, 256, 3), dtype=np.uint8), (0, 0), 1.5,
        )
        shifted = np.roll(base, 2, axis=1)  # 2 px to the right
        stats = corridor_shift_stats(base, shifted, [(128.0, 128.0)], window=128)
        self.assertEqual(stats["windows"], 1)
        self.assertTrue(math.isclose(stats["p95Px"], 2.0, abs_tol=0.3))

    def test_corridor_shift_near_zero_for_identical_images(self):
        rng = np.random.default_rng(13)
        base = cv2.GaussianBlur(
            rng.integers(0, 255, (256, 256, 3), dtype=np.uint8), (0, 0), 1.5,
        )
        stats = corridor_shift_stats(base, base.copy(), [(128.0, 128.0)], window=128)
        self.assertLess(stats["p95Px"], 0.05)

    def test_corridor_shift_skips_out_of_bounds_points(self):
        base = np.zeros((64, 64, 3), dtype=np.uint8)
        stats = corridor_shift_stats(base, base, [(1.0, 1.0)], window=128)
        self.assertEqual(stats["windows"], 0)
        self.assertIsNone(stats["p95Px"])

    def test_shift_probe_points_stay_inside_image(self):
        points = shift_probe_points(1024, 128)
        self.assertEqual(len(points), 9)
        for x, y in points:
            self.assertGreaterEqual(x - 64, 0)
            self.assertLessEqual(x + 64, 1024)
            self.assertGreaterEqual(y - 64, 0)
            self.assertLessEqual(y + 64, 1024)


if __name__ == "__main__":
    unittest.main()
