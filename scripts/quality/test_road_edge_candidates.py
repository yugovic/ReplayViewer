import unittest
import numpy as np
from road_edge_candidates import select_white_road_run


class WhiteRoadCandidateTests(unittest.TestCase):
    def setUp(self):
        self.offsets = np.round(np.arange(-2, 5.01, .1), 3)

    def run_at(self, offset, count):
        start = int(np.flatnonzero(self.offsets == offset)[0])
        return np.arange(start, start + count)

    def test_reviewed_continuous_edge_wins_over_grid_box(self):
        grid_box = self.run_at(-1.4, 2)
        edge = self.run_at(1.1, 8)
        self.assertIs(select_white_road_run([grid_box, edge], self.offsets), grid_box)
        self.assertIs(select_white_road_run([grid_box, edge], self.offsets, (.6, 1.6)), edge)

    def test_absent_road_paint_is_not_replaced_by_an_unrelated_marking(self):
        transverse_mark = self.run_at(-.6, 5)
        self.assertIsNone(select_white_road_run([transverse_mark], self.offsets, (.8, 1.8)))

    def test_unreviewed_cross_section_keeps_existing_observation(self):
        narrow = self.run_at(.1, 3)
        broad = self.run_at(1.4, 11)
        self.assertIs(select_white_road_run([narrow, broad], self.offsets), narrow)
        self.assertIsNone(select_white_road_run([], self.offsets))


if __name__ == '__main__':
    unittest.main()
