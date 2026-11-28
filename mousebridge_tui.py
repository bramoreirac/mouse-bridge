"""Interactive Windows capture and curve export."""

import argparse
import math
from pathlib import Path
import sys

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.filter import Monochrome, NoColor
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, Select, Static, TabbedContent, TabPane
from textual_plot import HiResMode, PlotWidget

import mousebridge as bridge


class CurvePreview(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self, windows_points, yeetmouse_points=None, *, pointer_points=None):
        super().__init__()
        self.windows_points = windows_points
        self.yeetmouse_points = yeetmouse_points
        self.pointer_points = pointer_points

    def compose(self) -> ComposeResult:
        yield Header()
        if self.pointer_points is None:
            yield PlotWidget(id="gain-plot")
        else:
            with TabbedContent():
                with TabPane("Pointer speed", id="pointer-tab"):
                    yield PlotWidget(id="pointer-plot")
                with TabPane("EPP gain", id="gain-tab"):
                    yield PlotWidget(id="gain-plot")
        yield Static("Modeled steady horizontal motion. Scroll to zoom, drag to pan.", classes="hint")
        yield Button("Back", id="preview-back")
        yield Footer()

    def on_mount(self):
        gain = self.query_one("#gain-plot", PlotWidget)
        gain.set_xlabel("Hand speed (cm/s)")
        gain.set_ylabel("Gain (px/raw count)" if self.pointer_points is not None else "Gain (output per raw count)")
        self.draw_curve(gain, self.windows_points, "cyan", "Windows EPP model")
        if self.yeetmouse_points is not None:
            self.draw_curve(gain, self.yeetmouse_points, "yellow", "Generated YeetMouse")
            gain.show_legend()
        if self.pointer_points is not None:
            pointer = self.query_one("#pointer-plot", PlotWidget)
            pointer.set_xlabel("Hand speed (cm/s)")
            pointer.set_ylabel("Pointer speed (px/s)")
            self.draw_curve(pointer, self.pointer_points, "cyan", "Windows EPP model")

    @staticmethod
    def draw_curve(plot, points, color, label):
        x, y = zip(*points)
        plot.plot(x, y, line_style=color, label=label, hires_mode=HiResMode.BRAILLE)

    @on(Button.Pressed, "#preview-back")
    def close_preview(self, event: Button.Pressed):
        event.stop()
        self.app.pop_screen()


class MouseBridgeApp(App):
    TITLE = "MouseBridge"
    CSS = """
    #workflow { padding: 0 2 1 2; }
    #workflow Label { margin-top: 1; }
    #workflow Input, #workflow Select { max-width: 72; }
    .actions { height: auto; max-width: 72; }
    .actions Button { margin-right: 1; }
    .hint { color: $text-muted; padding: 0 1; }
    #status { padding: 0 1; min-height: 2; }
    #status.error { color: $error; }
    CurvePreview TabbedContent, CurvePreview TabPane, CurvePreview PlotWidget { height: 1fr; }
    #preview-back { margin-left: 1; }
    """

    def __init__(self):
        super().__init__()
        self.windows_snapshot = None
        self.no_color_filter = NoColor()

    def get_line_filters(self):
        # The plot canvas emits unstyled segments, which Monochrome cannot handle.
        return [
            self.no_color_filter if isinstance(line_filter, Monochrome) else line_filter
            for line_filter in super().get_line_filters()
        ]

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="workflow"):
            yield Label("Windows snapshot to load")
            yield Input("windows-mouse.json", id="source")
            with Horizontal(classes="actions"):
                if sys.platform == "win32":
                    yield Button("Read Windows registry", id="capture", variant="primary")
                yield Button("Load snapshot", id="open", variant="primary")
            yield Static("Load a snapshot to begin.", id="snapshot-summary", markup=False)
            for key, label in bridge.SETUP_FIELDS:
                yield Label(label)
                yield Input(id=key)
            yield Label("Enhance Pointer Precision (EPP)")
            yield Select([("On", "on"), ("Off", "off")], prompt="Confirm the Windows checkbox", id="epp")
            yield Static("EPP must be on to preview or export an EPP-on curve.", classes="hint")

            yield Label("Snapshot output path")
            yield Input("windows-mouse.json", id="snapshot-path")
            yield Button("Save capture", id="save-capture")
            yield Label("YeetMouse profile output path")
            yield Input("yeetmouse-epp.conf", id="profile-path")
            with Horizontal(classes="actions"):
                yield Button("Preview gain curves", id="preview")
                yield Button("Save YeetMouse profile", id="save-profile")
            yield Label("PNG output path")
            yield Input("windows-curves.png", id="png-path")
            with Horizontal(classes="actions"):
                yield Button("Preview Windows curves", id="preview-windows")
                yield Button("Export Windows curves PNG", id="save-png")
        yield Static(id="status", markup=False)
        yield Footer()

    def report(self, message, *, error=False):
        status = self.query_one("#status", Static)
        status.set_class(error, "error")
        status.update(message)

    def input_path(self, field):
        value = self.query_one(f"#{field}", Input).value.strip()
        if not value:
            raise ValueError("Enter a file path.")
        return Path(value).expanduser()

    def show_snapshot(self, snapshot, source):
        self.windows_snapshot = snapshot
        setup = snapshot.get("setup", {})
        for key, _ in bridge.SETUP_FIELDS:
            self.query_one(f"#{key}", Input).value = str(setup[key]) if key in setup else ""
        epp = self.query_one("#epp", Select)
        if "epp_enabled" in setup:
            epp.value = "on" if setup["epp_enabled"] else "off"
        else:
            epp.clear()
        registry = snapshot["registry"]
        release = snapshot.get("release") or "unknown build"
        sensitivity = registry.get("MouseSensitivity")
        speed = f"{sensitivity}/20" if sensitivity is not None else "unavailable"
        self.query_one("#snapshot-summary", Static).update(
            f"{source} · Windows {release} · Pointer speed {speed}"
        )
        self.report("Review the setup fields before saving or exporting.")

    def current_capture(self):
        if self.windows_snapshot is None:
            raise ValueError("Load a Windows snapshot or read the Windows registry first.")
        setup = {}
        for key, label in bridge.SETUP_FIELDS:
            try:
                setup[key] = bridge.positive_number(self.query_one(f"#{key}", Input).value)
            except (argparse.ArgumentTypeError, ValueError) as error:
                self.query_one(f"#{key}", Input).focus()
                raise ValueError(f"{label} must be a finite positive number.") from error
        epp = self.query_one("#epp", Select)
        if epp.value is Select.NULL:
            epp.focus()
            raise ValueError("Confirm whether Enhance Pointer Precision is on or off.")
        setup["epp_enabled"] = epp.value == "on"
        return {**self.windows_snapshot, "setup": setup}

    @on(Button.Pressed)
    def handle_action(self, event: Button.Pressed):
        action = event.button.id
        try:
            if action == "open":
                source = self.input_path("source")
                self.show_snapshot(bridge.load_snapshot(source, "windows"), str(source))
            elif action == "capture":
                self.show_snapshot(bridge.collect_windows(), "Windows registry")
            else:
                self.export_capture(action)
        except FileExistsError as error:
            self.report(f"{error.filename} already exists. Choose a new output path.", error=True)
        except (OSError, ValueError) as error:
            self.report(str(error), error=True)

    def export_capture(self, action):
        capture = self.current_capture()
        registry, setup = capture["registry"], capture["setup"]
        if action == "save-capture":
            output = self.input_path("snapshot-path")
            bridge.write_snapshot(capture, output)
            self.windows_snapshot = capture
            message = f"Saved capture to {output}."
            if not setup["epp_enabled"]:
                message += " EPP is off, so EPP-on exports are unavailable."
            self.report(message)
            return

        bridge.require_epp_on(capture)
        scale, dpi, rate = setup["display_scale"], setup["dpi"], setup["polling_rate"]
        if action == "preview":
            curves = preview_points(registry, scale, dpi, rate)
            self.push_screen(CurvePreview(*curves))
        elif action == "preview-windows":
            xs, ys = bridge.decode_curve(registry)
            y_scale = bridge.epp_output_scale(registry, scale)
            speed_per_x = bridge.registry_x_to_hand_speed(dpi, rate)
            pointer = bridge.windows_pointer_points(xs, ys, y_scale, speed_per_x, rate, xs)
            gain = bridge.windows_gain_points(xs, ys, y_scale, speed_per_x, xs[-1])
            self.push_screen(CurvePreview(gain, pointer_points=pointer))
        elif action == "save-profile":
            output = self.input_path("profile-path")
            bridge.write_output(bridge.yeetmouse_config(registry, scale, rate), output)
            self.report(f"Saved YeetMouse profile to {output}.")
        elif action == "save-png":
            output = self.input_path("png-path")
            bridge.plot_windows_curves(registry, scale, dpi, rate, output)
            self.report(f"Saved Windows curves to {output}.")


def preview_points(registry, display_scale, dpi, polling_rate):
    xs, ys = bridge.decode_curve(registry)
    y_scale = bridge.epp_output_scale(registry, display_scale)
    speed_per_x = bridge.registry_x_to_hand_speed(dpi, polling_rate)
    windows = bridge.windows_gain_points(xs, ys, y_scale, speed_per_x, xs[-1])
    lut = bridge.yeetmouse_lut(registry, display_scale, polling_rate)
    yeetmouse = [(speed * (2540 / dpi), gain) for speed, gain in lut]
    if not all(math.isfinite(speed) for speed, _ in yeetmouse):
        raise ValueError("Mouse DPI must produce a finite YeetMouse speed range.")
    return windows, yeetmouse
