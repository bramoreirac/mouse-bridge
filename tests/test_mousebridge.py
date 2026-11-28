import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

import mousebridge as bridge


class EppExportTests(unittest.TestCase):
    def test_cli_refuses_exports_when_snapshot_confirms_epp_is_off(self):
        with tempfile.TemporaryDirectory() as directory:
            windows = Path(directory) / "windows.json"
            output = Path(directory) / "export"
            bridge.write_snapshot(bridge.snapshot("windows", registry={}, setup={"epp_enabled": False}), windows)
            for command, extra_flags in (
                ("yeetmouse-curve", []),
                ("libinput-curve", []),
                ("plot-wincurves", ["--dpi", "2400"]),
            ):
                with self.subTest(command=command):
                    args = [
                        command, str(windows), "--display-scale", "125", "--polling-rate", "125",
                        "-o", str(output), *extra_flags,
                    ]
                    with contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as failure:
                        bridge.main(args)
                    self.assertEqual(failure.exception.code, 1)
                    self.assertIn("Enhance Pointer Precision is off", error.getvalue())
                    self.assertFalse(output.exists())


class WindowsTests(unittest.TestCase):
    def test_registry_types_missing_fields_and_curve_bytes(self):
        key = MagicMock()
        entries = {
            "MouseSensitivity": ("10", 1),
            "MouseSpeed": (0, 4),
            "MouseThreshold1": ("invalid", 1),
            "SmoothMouseXCurve": (b"\x00\xff", 3),
            "SmoothMouseYCurve": ("wrong type", 1),
        }

        def query(handle, name):
            if name not in entries:
                raise FileNotFoundError(name)
            return entries[name]

        registry = types.SimpleNamespace(
            HKEY_CURRENT_USER=1, REG_SZ=1, REG_DWORD=4, REG_BINARY=3,
            OpenKey=MagicMock(return_value=key), QueryValueEx=MagicMock(side_effect=query),
        )
        with (
            patch.object(sys, "platform", "win32"),
            patch.dict(sys.modules, winreg=registry),
            patch.object(bridge.platform, "version", return_value="test-build"),
        ):
            data = bridge.collect_windows()

        registry.OpenKey.assert_called_once_with(1, r"Control Panel\Mouse")
        self.assertEqual(data["registry"], {
            "MouseSensitivity": 10, "MouseSpeed": 0, "MouseThreshold1": None,
            "MouseThreshold2": None, "SmoothMouseXCurve": "00ff", "SmoothMouseYCurve": None,
        })


class GnomeTests(unittest.TestCase):
    def test_reads_only_the_mouse_keys(self):
        with (
            patch.object(sys, "platform", "linux"),
            patch.dict(bridge.os.environ, XDG_CURRENT_DESKTOP="ubuntu:GNOME", XDG_SESSION_TYPE="wayland"),
            patch.object(bridge.subprocess, "run", side_effect=[
                subprocess.CompletedProcess([], 0, "'adaptive'\n"),
                subprocess.CompletedProcess([], 0, "-0.25\n"),
            ]) as run,
        ):
            data = bridge.collect_gnome()

        self.assertEqual(data["speed"], -0.25)
        self.assertEqual(data["accel_profile"], "adaptive")
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ["gsettings", "get", bridge.SCHEMA, "accel-profile"],
            ["gsettings", "get", bridge.SCHEMA, "speed"],
        ])

    def test_rejects_x11_before_reading_settings(self):
        with (
            patch.object(sys, "platform", "linux"),
            patch.dict(bridge.os.environ, XDG_CURRENT_DESKTOP="GNOME", XDG_SESSION_TYPE="x11"),
            patch.object(bridge.subprocess, "run") as run,
        ):
            with self.assertRaisesRegex(ValueError, "GNOME Wayland"):
                bridge.collect_gnome()
        run.assert_not_called()

    def test_schema_failure_explains_which_key_failed(self):
        failure = subprocess.CalledProcessError(1, "gsettings", stderr="No such schema")
        with patch.object(bridge.subprocess, "run", side_effect=failure):
            with self.assertRaisesRegex(ValueError, "speed: No such schema"):
                bridge.read_gsetting("speed")


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.gnome = bridge.snapshot("gnome", session_type="wayland", speed=0.25, accel_profile="default")

    def test_acceleration_hints_and_speed_baseline(self):
        for flag, hint in [(0, "test flat"), (1, "test adaptive"), (2, "test adaptive"),
                           (3, "keep default"), (None, "keep default")]:
            with self.subTest(flag=flag):
                windows = bridge.snapshot("windows", registry={"MouseSpeed": flag})
                report = bridge.comparison(windows, self.gnome)
                self.assertIn(hint, report)
                self.assertIn("speed: keep 0.25", report)
                self.assertIn("SmoothMouseXCurve: unavailable", report)

    def test_offline_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            windows = Path(directory) / "windows.json"
            gnome = Path(directory) / "gnome.json"
            bridge.write_snapshot(bridge.snapshot("windows", registry={"MouseSpeed": 0}), windows)
            bridge.write_snapshot(self.gnome, gnome)
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(bridge.main(["analyze", str(windows), "--gnome", str(gnome)]), 0)
            self.assertIn("test flat", output.getvalue())

    def test_invalid_snapshot_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps({"schema_version": True, "kind": "windows"}))
            with contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as exit:
                bridge.main(["analyze", str(path)])
            self.assertEqual(exit.exception.code, 1)
            self.assertIn("Expected a MouseBridge windows snapshot", error.getvalue())

    def test_snapshot_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gnome.json"
            path.write_text("baseline")
            with self.assertRaises(FileExistsError):
                bridge.write_snapshot(self.gnome, path)
            self.assertEqual(path.read_text(), "baseline")

    def test_validation_rejects_bad_settings(self):
        for speed in (True, "0", float("nan"), 1.01, 10**400):
            with self.subTest(speed=speed), self.assertRaisesRegex(ValueError, "GNOME speed"):
                bridge.validate({**self.gnome, "speed": speed}, "gnome")
        windows = bridge.snapshot("windows", registry={"SmoothMouseXCurve": "abc"})
        with self.assertRaisesRegex(ValueError, "hex byte string"):
            bridge.validate(windows, "windows")

