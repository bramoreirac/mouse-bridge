"""Read-only Windows and GNOME mouse settings snapshots and comparison."""

import argparse
from bisect import bisect_right
import math
import json
import os
import platform
from pathlib import Path
import re
import struct
import subprocess
import sys

VERSION = 1
REGISTRY_KEY = r"Control Panel\Mouse"
REGISTRY_NUMBERS = ("MouseSensitivity", "MouseSpeed", "MouseThreshold1", "MouseThreshold2")
CURVES = ("SmoothMouseXCurve", "SmoothMouseYCurve")
SCHEMA = "org.gnome.desktop.peripherals.mouse"
PROFILES = ("default", "flat", "adaptive", "custom")
SETUP_FIELDS = (
    ("dpi", "Mouse DPI"),
    ("display_scale", "Windows display scaling (%)"),
    ("polling_rate", "Polling rate (Hz)"),
)


def snapshot(kind, **fields):
    return {"schema_version": VERSION, "kind": kind, **fields}


def collect_windows():
    if sys.platform != "win32":
        raise ValueError("Run export in Windows as the user whose settings you want to read.")

    import winreg

    values = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_KEY) as key:
        for name in (*REGISTRY_NUMBERS, *CURVES):
            try:
                value, value_type = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                values[name] = None
                continue

            if name in CURVES and value_type == winreg.REG_BINARY:
                values[name] = value.hex()
            elif name in REGISTRY_NUMBERS and value_type in (winreg.REG_SZ, winreg.REG_DWORD):
                if re.fullmatch(r"[+-]?[0-9]+", str(value).strip()):
                    values[name] = int(value)
                else:
                    values[name] = None
            else:
                values[name] = None
    return snapshot("windows", release=platform.version(), registry=values)


def read_gsetting(key):
    try:
        result = subprocess.run(
            ["gsettings", "get", SCHEMA, key],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except FileNotFoundError as error:
        raise ValueError("gsettings was not found. Use a terminal in Ubuntu GNOME.") from error
    except subprocess.CalledProcessError as error:
        raise ValueError(f"Cannot read GNOME {key}: {error.stderr.strip()}") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"Reading GNOME {key} timed out.") from error
    return result.stdout.strip()


def collect_gnome():
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    session = os.environ.get("XDG_SESSION_TYPE", "")
    if (
        sys.platform != "linux"
        or "gnome" not in desktop.lower().split(":")
        or session.lower() != "wayland"
    ):
        raise ValueError("Run this command in your GNOME Wayland desktop terminal, without sudo.")

    profile = re.fullmatch(r"'([a-z-]+)'", read_gsetting("accel-profile"))
    data = snapshot(
        "gnome", desktop=desktop, session_type=session.lower(),
        speed=float(read_gsetting("speed")), accel_profile=profile[1] if profile else None,
    )
    validate(data, "gnome")
    return data


def validate(data, kind):
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != VERSION
        or data.get("kind") != kind
    ):
        raise ValueError(f"Expected a MouseBridge {kind} snapshot with schema_version {VERSION}.")

    if kind == "windows":
        values = data.get("registry")
        if not isinstance(values, dict):
            raise ValueError("Windows snapshot must contain a registry object.")
        for name in REGISTRY_NUMBERS:
            value = values.get(name)
            if value is not None and type(value) is not int:
                raise ValueError(f"{name} must be an integer or null.")
        for name in CURVES:
            value = values.get(name)
            if value is not None and (
                not isinstance(value, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2})*", value)
            ):
                raise ValueError(f"{name} must be a hex byte string or null.")
        if "setup" in data:
            validate_windows_setup(data["setup"])
    else:
        speed = data.get("speed")
        if type(speed) not in (int, float) or not -1 <= speed <= 1:
            raise ValueError("GNOME speed must be a number from -1 to 1.")
        if data.get("accel_profile") not in PROFILES:
            raise ValueError("GNOME accel-profile must be default, flat, adaptive, or custom.")
        if data.get("session_type") != "wayland":
            raise ValueError("Expected a GNOME Wayland snapshot.")
    return data


def validate_windows_setup(setup):
    if not isinstance(setup, dict):
        raise ValueError("Windows setup must be an object.")
    for key, label in SETUP_FIELDS:
        if key in setup:
            value = setup[key]
            if type(value) not in (int, float) or not 0 < value <= sys.float_info.max:
                raise ValueError(f"{label} must be a finite positive number.")
    if "epp_enabled" in setup and type(setup["epp_enabled"]) is not bool:
        raise ValueError("Enhance Pointer Precision must be on or off.")


def load_snapshot(path, kind):
    try:
        return validate(json.loads(path.read_text(encoding="utf-8-sig")), kind)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error


def decode_curve(registry):
    axes = []
    for key in CURVES:
        raw = bytes.fromhex(registry.get(key) or "")
        if len(raw) != 40:
            raise ValueError(f"{key} must contain five 8-byte coordinates.")
        values = struct.unpack("<5Q", raw)
        if any(value > 0xFFFFFFFF for value in values):
            raise ValueError(f"{key} contains a coordinate outside the 16.16 range.")
        axes.append([value / 65536 for value in values])
    xs, ys = axes
    if (xs[0], ys[0]) != (0, 0) or any(a >= b for a, b in zip(xs, xs[1:])):
        raise ValueError("The curve must start at (0, 0) and have increasing X coordinates.")
    return xs, ys


def interpolate(xs, ys, x):
    index = min(bisect_right(xs, x) - 1, len(xs) - 2)
    fraction = (x - xs[index]) / (xs[index + 1] - xs[index])
    return ys[index] + fraction * (ys[index + 1] - ys[index])


def positive_number(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("expected a finite positive number")
    return number


def epp_output_scale(registry, display_scale):
    sensitivity = registry.get("MouseSensitivity")
    if sensitivity not in range(1, 21):
        raise ValueError("MouseSensitivity must be present and between 1 and 20.")
    effective_dpi = 96 * (display_scale / 100)
    return (max(effective_dpi, 96) / 120) * (sensitivity / 10)


def registry_x_to_hand_speed(dpi, polling_rate):
    scale = 3.5 * (polling_rate * 2.54 / dpi)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("DPI and polling rate must produce a finite, positive speed range.")
    return scale


def epp_gain(xs, ys, y_scale, x):
    if x == 0:
        return y_scale * ys[1] / (3.5 * xs[1])
    return y_scale * interpolate(xs, ys, x) / (3.5 * x)


def windows_pointer_points(xs, ys, y_scale, speed_per_x, polling_rate, samples):
    pointer_speed_per_y = y_scale * polling_rate
    points = [
        (x * speed_per_x, interpolate(xs, ys, x) * pointer_speed_per_y)
        for x in samples
    ]
    if not all(math.isfinite(x) and math.isfinite(1.06 * y) for x, y in points):
        raise ValueError("Setup values must produce a finite pointer speed curve.")
    return points


def windows_gain_points(xs, ys, y_scale, speed_per_x, limit):
    samples = sorted({x for x in xs if x <= limit} | {limit * i / 500 for i in range(501)})
    points = [(x * speed_per_x, epp_gain(xs, ys, y_scale, x)) for x in samples]
    if not all(math.isfinite(x) and math.isfinite(y) for x, y in points):
        raise ValueError("Setup values must produce a finite gain curve.")
    return points


def libinput_curve(registry, display_scale, polling_rate):
    xs, ys = decode_curve(registry)
    y_scale = epp_output_scale(registry, display_scale)
    reports_per_ms = polling_rate / 1000
    # Sample through the last bend, then include one point on the final line
    # so libinput extrapolates that line at higher speeds.
    registry_step = xs[-2] / 62
    step = registry_step * 3.5 * reports_per_ms
    speeds = [
        interpolate(xs, ys, registry_step * index) * y_scale * reports_per_ms
        for index in range(64)
    ]
    if not math.isfinite(step) or step <= 0 or not all(map(math.isfinite, speeds)):
        raise ValueError("Display scaling and polling rate must produce a finite curve with a positive step.")
    return {"pointer-step": step, "pointer-speeds": speeds}


def yeetmouse_lut(registry, display_scale, polling_rate):
    xs, ys = decode_curve(registry)
    y_scale = epp_output_scale(registry, display_scale)
    speed_per_x = 3.5 * polling_rate / 1000
    if not math.isfinite(speed_per_x * xs[-1]) or speed_per_x * xs[1] <= 0:
        raise ValueError("Polling rate must produce a finite, positive YeetMouse speed range.")

    # The first segment has constant gain. Sample the remaining segments
    # geometrically because the gain varies most near their lower ends.
    samples = [xs[1]]
    for left, right in zip(xs[1:], xs[2:]):
        samples.extend(left * (right / left) ** (index / 16) for index in range(1, 16))
        samples.append(right)
    gains = [epp_gain(xs, ys, y_scale, x) for x in samples]
    speeds = [x * speed_per_x for x in samples]
    # YeetMouse extrapolates the last two points. A level tail caps the gain
    # at the last modeled value instead of extending the final LUT slope.
    speeds = [0.0, *speeds, 2 * speeds[-1]]
    gains = [gains[0], *gains, gains[-1]]
    if not all(0.0001 <= value < 100000 for value in (*speeds[1:], *gains)):
        raise ValueError("Curve values are outside the supported YeetMouse export range.")

    # Match yeetmousectl's precision when it serializes points to the driver.
    points = [(float(f"{speed:.5g}"), float(f"{gain:.5g}")) for speed, gain in zip(speeds, gains)]
    if any(left[0] >= right[0] for left, right in zip(points, points[1:])):
        raise ValueError("Two curve speeds coincide at YeetMouse's precision.")
    return points


def yeetmouse_config(registry, display_scale, polling_rate):
    points = yeetmouse_lut(registry, display_scale, polling_rate)
    data = "".join(f"{speed:.5g},{gain:.5g};" for speed, gain in points)
    return (
        "sens=1\nratioYX=1\noutCap=0\ninCap=0\noffset=0\naccel=0.1\n"
        "exponent=1.8\nmidpoint=1.3\nmotivity=1.5\npreScale=1\n"
        "accelMode=AccelMode_Lut\nuseSmoothing=0\nrotation=0\n"
        "as_threshold=0\nas_angle=0\n"
        f"LUT_size={len(points)}\nLUT_data={data}\n"
    )


def draw_panel(draw, font, bounds, title, labels, x_end, y_end, line, knots, unity=False):
    left, top, right, bottom = bounds
    ink, muted, blue = "#18273b", "#526479", "#1663bb"
    draw.rounded_rectangle((left - 58, top - 64, right + 30, bottom + 73), 15, fill="white")
    draw.text((left, top - 49), title, font=font(21), fill=ink)
    draw.text((left, top - 25), labels[1], font=font(15), fill=muted)
    draw.text(((left + right) / 2, bottom + 49), labels[0], anchor="mm", font=font(16), fill=ink)

    def position(x, y):
        return left + (right - left) * x / x_end, bottom - (bottom - top) * y / y_end

    for index in range(6):
        x, y = x_end * index / 5, y_end * index / 5
        col, row = position(x, y)
        draw.line((col, top, col, bottom), fill="#e6edf4")
        draw.line((left, row, right, row), fill="#e6edf4")
        draw.text((col, bottom + 10), f"{x:.3g}", anchor="mt", font=font(14), fill=muted)
        draw.text((left - 8, row), f"{y:.3g}", anchor="rm", font=font(14), fill=muted)
    if unity and y_end >= 1:
        _, row = position(0, 1)
        draw.line((left, row, right, row), fill="#a0afc0")
    draw.line((left, top, left, bottom, right, bottom), fill=muted)
    draw.line([position(x, y) for x, y in line], fill=blue, width=3)
    for x, y in knots:
        col, row = position(x, y)
        draw.ellipse((col - 4, row - 4, col + 4, row + 4), fill=blue, outline="white")


def plot_windows_curves(registry, scaling, dpi, polling_rate, output):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as error:
        raise ValueError('PNG export requires Pillow. Install it with pip install ".[plot]".') from error

    xs, ys = decode_curve(registry)
    y_scale = epp_output_scale(registry, scaling)
    sensitivity = registry["MouseSensitivity"]
    speed_per_x = registry_x_to_hand_speed(dpi, polling_rate)

    if not math.isfinite(xs[-1] * speed_per_x) or xs[1] * speed_per_x <= 0:
        raise ValueError("DPI and polling rate must produce a finite, positive speed range.")
    if not math.isfinite(y_scale * max(ys) / xs[1]):
        raise ValueError("Display scaling must produce a finite gain range.")

    def font(size):
        return ImageFont.load_default(size=size)

    image = Image.new("RGB", (1200, 960), "#f6f8fb")
    draw = ImageDraw.Draw(image)
    draw.text((42, 29), "Windows mouse acceleration", font=font(34), fill="#18273b")
    draw.text(
        (44, 76), f"Modeled EPP on | Pointer speed {sensitivity}/20 | {scaling:g}% scaling | {dpi:g} DPI | {polling_rate:g} Hz",
        font=font(18), fill="#526479",
    )
    for left, suffix, limit in (
        (105, "full stored range", xs[-1]),
        (690, "low-speed detail", min(xs[-1], xs[-2] * 1.1)),
    ):
        right = left + 414
        x_knots = [x for x in xs if x <= limit]
        speed_limit = limit * speed_per_x
        registry_line = windows_pointer_points(xs, ys, y_scale, speed_per_x, polling_rate, [*x_knots, limit])
        registry_knots = registry_line[:-1]
        gain_knots = [(x * speed_per_x, epp_gain(xs, ys, y_scale, x)) for x in x_knots]
        gain_line = windows_gain_points(xs, ys, y_scale, speed_per_x, limit)
        draw_panel(
            draw, font, (left, 174, right, 429), f"Registry curve | {suffix}",
            ("Horizontal hand speed (cm/s)", "Modeled pointer speed (px/s)"),
            speed_limit, max(1, 1.06 * max(y for _, y in registry_line)), registry_line, registry_knots,
        )
        draw_panel(
            draw, font, (left, 575, right, 830), f"EPP gain | {suffix}",
            ("Horizontal hand speed (cm/s)", "Gain (pointer pixels per raw count)"),
            speed_limit, max(1.1, 1.06 * max(y for _, y in gain_line)),
            gain_line, gain_knots, unity=True,
        )
    draw.text(
        (44, 926), "Hand speed assumes steady horizontal motion. Gray line marks 1 pixel per count.",
        font=font(16), fill="#526479",
    )
    with output.open("xb") as file:
        image.save(file, format="PNG")


def comparison(windows, gnome):
    values = windows["registry"]
    mouse_speed = values.get("MouseSpeed")
    candidate = {0: "flat", 1: "adaptive", 2: "adaptive"}.get(mouse_speed)

    lines = ["MouseBridge comparison", "======================", "", "Windows registry"]
    for name in REGISTRY_NUMBERS:
        value = values.get(name)
        lines.append(f"  {name}: {value if value is not None else 'unavailable'}")
    for name in CURVES:
        value = values.get(name)
        size = f"{len(value) // 2} bytes captured" if value is not None else "unavailable"
        lines.append(f"  {name}: {size}")

    lines.extend([
        "",
        "GNOME Wayland",
        f"  speed: {gnome['speed']}",
        f"  accel-profile: {gnome['accel_profile']}",
        "",
        "Starting point for calibration",
        f"  speed: keep {gnome['speed']} as the baseline.",
    ])
    if candidate is None:
        lines.append(f"  accel-profile: keep {gnome['accel_profile']}; Windows acceleration is unclear.")
    else:
        verb = "keep" if candidate == gnome["accel_profile"] else "test"
        lines.append(f"  accel-profile: {verb} {candidate} (based on registry MouseSpeed={mouse_speed}).")
    lines.extend([
        "",
        "The profile suggestion is a heuristic. Confirm Enhance pointer precision in Windows.",
        "GNOME may fall back to the device default if the profile is unsupported.",
        "Use libinput-curve to preview a custom EPP-on profile. Measure pointer travel before judging similarity.",
    ])
    return "\n".join(lines)


def write_snapshot(data, path):
    write_output(json.dumps(data, indent=2, allow_nan=False) + "\n", path)


def write_output(payload, path):
    if path is None:
        print(payload, end="")
    else:
        with path.open("x", encoding="utf-8") as output:
            output.write(payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("export", "Export Windows registry settings"),
        ("snapshot", "Capture GNOME Wayland settings"),
    ):
        capture = commands.add_parser(command, help=help_text)
        capture.add_argument("-o", "--output", type=Path, help="Write JSON to a new file")

    analyze = commands.add_parser("analyze", help="Compare Windows and GNOME settings")
    analyze.add_argument("windows", type=Path, help="Windows export JSON")
    analyze.add_argument("--gnome", type=Path, help="GNOME snapshot JSON for offline comparison")

    commands.add_parser("tui", help="Open the interactive MouseBridge workflow")
    plot = commands.add_parser("plot-wincurves", help="Export the Windows mouse curves as PNG")
    plot.add_argument("windows", type=Path, help="Windows snapshot JSON")
    plot.add_argument("--display-scale", required=True, type=positive_number, metavar="PERCENT")
    plot.add_argument("--dpi", required=True, type=positive_number)
    plot.add_argument("--polling-rate", required=True, type=positive_number, metavar="HZ")
    plot.add_argument("-o", "--output", type=Path, default=Path("windows-curves.png"),
                      help="New PNG file (default: windows-curves.png)")

    for name, help_text in (
        ("libinput-curve", "Generate a GNOME custom EPP-on curve as JSON"),
        ("yeetmouse-curve", "Generate an EPP-on YeetMouse config without applying it"),
    ):
        curve = commands.add_parser(name, help=help_text)
        curve.add_argument("windows", type=Path, help="Windows export JSON with EPP confirmed separately")
        curve.add_argument("--display-scale", required=True, type=positive_number, metavar="PERCENT",
                           help="Windows display scaling percentage")
        curve.add_argument("--polling-rate", required=True, type=positive_number, metavar="HZ")
        curve.add_argument("-o", "--output", type=Path, help="Write the curve to a new file")

    args = parser.parse_args(argv)
    try:
        if args.command == "tui":
            try:
                from mousebridge_tui import MouseBridgeApp
            except ModuleNotFoundError as error:
                if error.name not in {"textual", "textual_plot"}:
                    raise
                raise ValueError('The TUI requires optional dependencies. Install with pip install ".[tui]".') from error
            MouseBridgeApp().run()
        elif args.command == "plot-wincurves":
            registry = load_snapshot(args.windows, "windows")["registry"]
            plot_windows_curves(registry, args.display_scale, args.dpi, args.polling_rate, args.output)
            print(args.output)
        elif args.command == "analyze":
            windows = load_snapshot(args.windows, "windows")
            gnome = load_snapshot(args.gnome, "gnome") if args.gnome else collect_gnome()
            print(comparison(windows, gnome))
        elif args.command == "libinput-curve":
            windows = load_snapshot(args.windows, "windows")
            write_snapshot(libinput_curve(windows["registry"], args.display_scale, args.polling_rate), args.output)
        elif args.command == "yeetmouse-curve":
            windows = load_snapshot(args.windows, "windows")
            write_output(yeetmouse_config(windows["registry"], args.display_scale, args.polling_rate), args.output)
        else:
            data = collect_windows() if args.command == "export" else collect_gnome()
            write_snapshot(data, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"mousebridge: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
