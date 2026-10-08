"""Standard-library-only tests: no macOS frameworks or installed packages required."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).parents[1] / "scripts" / "macos_probe.py"
spec = importlib.util.spec_from_file_location("macos_probe", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class MappingTests(unittest.TestCase):
    def test_retina_and_nonuniform_scaling(self):
        dom = [(42, 42), (342, 42), (42, 342)]
        screen = [(184, 326), (784, 326), (184, 1226)]
        mapping = probe.axis_mapping(dom, screen)
        self.assertEqual(mapping, (2, 100, 3, 200))
        self.assertEqual(probe.to_quartz((100, 100), mapping, (2000, 1600),
                                         (0, 0, 1000, 800)), (150, 250))

    def test_normal_density_nonzero_origin(self):
        mapping = probe.axis_mapping([(0, 0), (100, 0), (0, 100)],
                                     [(10, 80), (110, 80), (10, 180)])
        self.assertEqual(probe.to_quartz((20, 30), mapping, (1000, 800),
                                         (-100, 20, 1000, 800)), (-70, 130))

    def test_reflected_inconsistent_collinear_and_missing(self):
        cases = [([(0, 0), (10, 0), (0, 10)], [(10, 0), (0, 0), (10, 10)]),
                 ([(0, 0), (100, 0), (0, 100)], [(0, 0), (100, 20), (0, 100)]),
                 ([(0, 0), (10, 10), (20, 20)], [(0, 0), (10, 10), (20, 20)]),
                 ([(0, 0)], [(0, 0)])]
        for dom, screen in cases:
            with self.subTest(dom=dom, screen=screen), self.assertRaises(probe.ProbeError):
                probe.axis_mapping(dom, screen)

    def test_outside_display_and_invalid_dimensions(self):
        for point, pixels in [((-1, 0), (100, 100)), ((100, 1), (100, 100)),
                              ((1, 1), (0, 100))]:
            with self.subTest(point=point), self.assertRaises(probe.ProbeError):
                probe.to_quartz(point, (1, 0, 1, 0), pixels, (0, 0, 100, 100))


class MarkerTests(unittest.TestCase):
    def image(self, rectangles):
        width, height = 100, 80
        pixels = [(255, 255, 255)] * (width * height)
        for left, top, size, color in rectangles:
            for y in range(top, top + size):
                for x in range(left, left + size):
                    pixels[y * width + x] = color
        return pixels, width, height

    def test_tolerance_and_noise_filter(self):
        color = probe.MARKERS['a']
        adjusted = tuple(v + 3 for v in color)
        pixels, w, h = self.image([(20, 10, 24, adjusted), (80, 60, 2, color)])
        self.assertEqual(probe.find_marker(pixels, w, h, color), (32, 22))

    def test_absent_and_ambiguous_rejected(self):
        color = probe.MARKERS['a']
        for rects in ([], [(5, 5, 24, color), (50, 40, 24, color)]):
            with self.subTest(rects=rects), self.assertRaises(probe.ProbeError):
                probe.find_marker(*self.image(rects), color)

    def test_nonrectangular_noise_rejected(self):
        color = probe.MARKERS['b']
        pixels, w, h = self.image([(10, 10, 24, color)])
        for y in range(15, 30):
            for x in range(15, 30):
                pixels[y * w + x] = (255, 255, 255)
        with self.assertRaises(probe.ProbeError):
            probe.find_marker(pixels, w, h, color)


class PlatformTests(unittest.TestCase):
    def test_non_macos_rejected(self):
        for platform in ('win32', 'linux'):
            with self.subTest(platform=platform), self.assertRaisesRegex(
                    probe.ProbeError, 'requires macOS'):
                probe.require_macos(platform)

    def test_failure_report_without_third_party_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(probe, 'require_macos', side_effect=probe.ProbeError('not macOS')):
                self.assertEqual(probe.run(Path(directory)), 1)
            report = json.loads((Path(directory) / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['result'], 'fail')
            self.assertEqual(report['stages'], [
                {'stage': 'platform', 'status': 'fail', 'reason': 'not macOS'}])
            self.assertFalse((Path(directory) / 'before.png').exists())

    @unittest.skipIf(sys.platform == 'darwin', 'CLI test deliberately uses a non-macOS host')
    def test_actual_cli_nonzero_and_platform_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(SCRIPT), '--output', directory],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 1)
            self.assertIn('requires macOS', result.stderr)
            report = json.loads((Path(directory) / 'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['stages'][0]['stage'], 'platform')


if __name__ == '__main__':
    unittest.main()
