import importlib.util
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import mousebridge as bridge

ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ROOT / "windows-mouse.json"

HAS_TUI = all(importlib.util.find_spec(name) is not None for name in ("textual", "textual_plot"))
if HAS_TUI:
    import mousebridge_tui as tui
    from textual.widgets import Input, Select, Static, TabbedContent


class PlotCommandTests(unittest.TestCase):
    @unittest.skipUnless(importlib.util.find_spec("PIL"), "requires the plot extra")
    def test_plot_command_and_no_overwrite(self):
        from PIL import Image

        flags = [str(WINDOWS), "--dpi", "2400", "--display-scale", "125", "--polling-rate", "125"]
        with TemporaryDirectory() as directory:
            output = Path(directory) / "windows-curves.png"
            command = [sys.executable, "-B", str(ROOT / "mousebridge.py"), "plot-wincurves", *flags]
            result = subprocess.run(command, cwd=directory, capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), output.name)
            with Image.open(output) as image:
                self.assertEqual(image.format, "PNG")
                image.verify()
            original = output.read_bytes()
            failure = subprocess.run(command, cwd=directory, capture_output=True, text=True)
            self.assertEqual(failure.returncode, 1)
            self.assertIn("windows-curves.png", failure.stderr)
            self.assertEqual(output.read_bytes(), original)

            custom_output = Path(directory) / "custom curves.png"
            subprocess.run([*command, "-o", str(custom_output)], cwd=directory, capture_output=True, check=True)
            self.assertEqual(custom_output.read_bytes(), original)


@unittest.skipUnless(HAS_TUI, "requires the tui extra")
class TuiTests(unittest.IsolatedAsyncioTestCase):
    async def press(self, pilot, widget):
        button = pilot.app.query_one(f"#{widget}")
        button.active_effect_duration = 0
        button.focus()
        await pilot.press("enter")
        await pilot.pause()

    def fill_setup(self, app, *, epp):
        for key, value in (("dpi", "2400"), ("display_scale", "125"), ("polling_rate", "125")):
            app.query_one(f"#{key}", Input).value = value
        app.query_one("#epp", Select).value = epp

    async def test_open_legacy_preview_and_save(self):
        registry = bridge.load_snapshot(WINDOWS, "windows")["registry"]
        config = bridge.yeetmouse_config(registry, 125, 125)
        data = next(line.removeprefix("LUT_data=") for line in config.splitlines()
                    if line.startswith("LUT_data="))
        lut = [tuple(map(float, pair.split(","))) for pair in data.rstrip(";").split(";")]
        self.assertEqual(len(lut), 51)
        self.assertEqual(lut[-2:], [(17.5, 3.1696), (35, 3.1696)])

        with TemporaryDirectory() as directory, patch.dict("os.environ", {"NO_COLOR": "1"}):
            app = tui.MouseBridgeApp()
            async with app.run_test(size=(80, 24)) as pilot:
                app.query_one("#source", Input).value = str(WINDOWS)
                await self.press(pilot, "open")
                self.assertEqual(app.query_one("#dpi", Input).value, "")
                self.assertIs(app.query_one("#epp", Select).value, Select.NULL)
                self.fill_setup(app, epp=Select.NULL)
                await self.press(pilot, "preview")
                self.assertIn("Confirm whether", str(app.query_one("#status", Static).render()))

                self.fill_setup(app, epp="on")
                await self.press(pilot, "preview")
                self.assertIsInstance(app.screen, tui.CurvePreview)
                self.assertEqual(app.screen.yeetmouse_points, [(speed * (2540 / 2400), gain) for speed, gain in lut])
                await pilot.press("escape")
                await pilot.pause()

                await self.press(pilot, "preview-windows")
                self.assertIsNone(app.screen.yeetmouse_points)
                hand_speed, pointer_speed = app.screen.pointer_points[-1]
                self.assertAlmostEqual(hand_speed, 18.5208333333)
                self.assertEqual(pointer_speed, 55468.75)
                tabs = app.screen.query_one(TabbedContent)
                self.assertEqual(tabs.active, "pointer-tab")
                await pilot.press("right")
                await pilot.pause()
                self.assertEqual(tabs.active, "gain-tab")
                await pilot.press("escape")
                await pilot.pause()

                output = Path(directory) / "test profile.conf"
                app.query_one("#profile-path", Input).value = str(output)
                await self.press(pilot, "save-profile")
                self.assertEqual(output.read_text(), config)
                if importlib.util.find_spec("PIL"):
                    png = Path(directory) / "Windows curves.png"
                    app.query_one("#png-path", Input).value = str(png)
                    await self.press(pilot, "save-png")
                    self.assertEqual(png.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
                await self.press(pilot, "save-profile")
                self.assertTrue(app.query_one("#status", Static).has_class("error"))
                self.assertEqual(output.read_text(), config)

    async def test_mocked_windows_capture_and_reopen(self):
        sample = bridge.load_snapshot(WINDOWS, "windows")["registry"]
        key = MagicMock()

        def read_value(handle, name):
            if name in bridge.CURVES:
                return bytes.fromhex(sample[name]), 3
            return str(sample[name]), 1

        winreg = SimpleNamespace(
            HKEY_CURRENT_USER=1, REG_SZ=1, REG_DWORD=4, REG_BINARY=3,
            OpenKey=MagicMock(return_value=key), QueryValueEx=MagicMock(side_effect=read_value),
        )
        with (
            TemporaryDirectory() as directory,
            patch.object(tui, "sys", SimpleNamespace(platform="win32")),
            patch.object(bridge, "sys", SimpleNamespace(platform="win32", float_info=sys.float_info)),
            patch.dict(sys.modules, winreg=winreg),
            patch.object(bridge.platform, "version", return_value="mock-build"),
        ):
            app = tui.MouseBridgeApp()
            async with app.run_test(size=(80, 24)) as pilot:
                await self.press(pilot, "capture")
                winreg.OpenKey.assert_called_once_with(1, bridge.REGISTRY_KEY)
                output = Path(directory) / "windows-mouse.json"
                app.query_one("#snapshot-path", Input).value = str(output)
                self.fill_setup(app, epp="off")
                app.query_one("#dpi", Input).value = "nan"
                await self.press(pilot, "save-capture")
                self.assertIn("Mouse DPI", str(app.query_one("#status", Static).render()))
                self.assertFalse(output.exists())

                self.fill_setup(app, epp="off")
                await self.press(pilot, "save-capture")
                saved = bridge.load_snapshot(output, "windows")
                self.assertEqual(saved["registry"], sample)
                self.assertEqual(saved["setup"], {
                    "dpi": 2400, "display_scale": 125, "polling_rate": 125, "epp_enabled": False,
                })

                app.query_one("#source", Input).value = str(output)
                await self.press(pilot, "open")
                self.assertEqual(app.query_one("#dpi", Input).value, "2400.0")
                self.assertEqual(app.query_one("#epp", Select).value, "off")
                await self.press(pilot, "preview")
                self.assertIn("is off", str(app.query_one("#status", Static).render()))
