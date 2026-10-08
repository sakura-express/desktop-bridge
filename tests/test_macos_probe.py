"""Standard-library-only tests: no macOS frameworks or installed packages required."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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


class VisualReadinessTests(unittest.TestCase):
    def test_blank_initial_frame_retried_until_valid_mapping(self):
        color = probe.MARKERS['a']
        blank = [(255, 255, 255)] * (100 * 80)
        valid = ((1, 22, 1, 83), (1024, 768))

        def first_capture():
            probe.find_marker(blank, 100, 80, color)

        capture = Mock(side_effect=[first_capture, lambda: valid])
        with patch.object(probe.time, 'monotonic', side_effect=[0, .1]), \
                patch.object(probe.time, 'sleep') as sleep:
            result = probe.wait_for_visual_mapping(lambda: capture()())
        self.assertEqual(result, valid)
        self.assertEqual(capture.call_count, 2)
        sleep.assert_called_once_with(.5)

    def test_persistent_invalid_geometry_times_out_with_last_reason(self):
        capture = Mock(side_effect=probe.ProbeError('mapping: reflected geometry'))
        with patch.object(probe.time, 'monotonic', side_effect=[0, .8, 1]), \
                patch.object(probe.time, 'sleep') as sleep:
            with self.assertRaisesRegex(probe.ProbeError,
                                        'timeout after 2 captures: mapping: reflected geometry'):
                probe.wait_for_visual_mapping(capture, timeout=1)
        self.assertEqual(capture.call_count, 2)
        self.assertAlmostEqual(sleep.call_args.args[0], .2)

    def test_capture_errors_propagate_without_retry(self):
        capture = Mock(side_effect=PermissionError('screen capture denied'))
        with patch.object(probe.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError):
                probe.wait_for_visual_mapping(capture)
        capture.assert_called_once_with()
        sleep.assert_not_called()


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


class FixtureAndActivePortTests(unittest.TestCase):
    def test_prepare_fixture_content_and_url(self):
        with tempfile.TemporaryDirectory() as td:
            fixture_path, url = probe.prepare_fixture(Path(td))
            self.assertTrue(fixture_path.is_file())
            self.assertEqual(fixture_path.name, "fixture.html")
            self.assertEqual(fixture_path.read_bytes(), probe.HTML)
            self.assertTrue(url.startswith("file://"))
            self.assertTrue(url.endswith("fixture.html"))

    def test_devtools_active_port_valid(self):
        with tempfile.TemporaryDirectory() as td:
            port_file = Path(td) / "DevToolsActivePort"
            port_file.write_text(
                "54321\n/devtools/browser/d9b4b7a1-8d2a-4a2e-8c6f-3c5e7b8a9f01\n",
                encoding="utf-8",
            )
            endpoint = probe.read_devtools_active_port(Path(td))
            self.assertEqual(
                endpoint,
                "ws://127.0.0.1:54321/devtools/browser/d9b4b7a1-8d2a-4a2e-8c6f-3c5e7b8a9f01",
            )

    def test_devtools_active_port_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(probe.ProbeError):
                probe.read_devtools_active_port(Path(td))

    def test_devtools_active_port_truncated_or_empty(self):
        cases = ["", "54321\n", "\n\n", "   \n  \n"]
        for content in cases:
            with self.subTest(content=content):
                with tempfile.TemporaryDirectory() as td:
                    (Path(td) / "DevToolsActivePort").write_text(content, encoding="utf-8")
                    with self.assertRaises(probe.ProbeError):
                        probe.read_devtools_active_port(Path(td))

    def test_devtools_active_port_invalid_port(self):
        cases = ["not_a_number", "0", "-1", "65536", "70000"]
        for port in cases:
            with self.subTest(port=port):
                with tempfile.TemporaryDirectory() as td:
                    (Path(td) / "DevToolsActivePort").write_text(
                        f"{port}\n/devtools/browser/d9b4b7a1-8d2a-4a2e-8c6f-3c5e7b8a9f01\n",
                        encoding="utf-8",
                    )
                    with self.assertRaises(probe.ProbeError):
                        probe.read_devtools_active_port(Path(td))

    def test_devtools_active_port_invalid_browser_path(self):
        cases = [
            "/other/path/uuid",
            "/devtools/browser/",
            "/devtools/browser/invalid@uuid!",
            "/devtools/browser/-",
            "/devtools/browser/abc",
            "/devtools/browser/d9b4b7a18d2a4a2e8c6f3c5e7b8a9f01",
            "d9b4b7a1-8d2a-4a2e-8c6f-3c5e7b8a9f01",
        ]
        for path in cases:
            with self.subTest(path=path):
                with tempfile.TemporaryDirectory() as td:
                    (Path(td) / "DevToolsActivePort").write_text(
                        f"54321\n{path}\n", encoding="utf-8"
                    )
                    with self.assertRaises(probe.ProbeError):
                        probe.read_devtools_active_port(Path(td))


if __name__ == '__main__':
    unittest.main()
