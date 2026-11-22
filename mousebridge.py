"""Read-only Windows and GNOME mouse settings snapshots and comparison."""

import argparse
import json
import os
import platform
from pathlib import Path
import re
import subprocess
import sys

VERSION = 1
REGISTRY_KEY = r"Control Panel\Mouse"
REGISTRY_NUMBERS = ("MouseSensitivity", "MouseSpeed", "MouseThreshold1", "MouseThreshold2")
CURVES = ("SmoothMouseXCurve", "SmoothMouseYCurve")
SCHEMA = "org.gnome.desktop.peripherals.mouse"
PROFILES = ("default", "flat", "adaptive")


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
    else:
        speed = data.get("speed")
        if type(speed) not in (int, float) or not -1 <= speed <= 1:
            raise ValueError("GNOME speed must be a number from -1 to 1.")
        if data.get("accel_profile") not in PROFILES:
            raise ValueError("GNOME accel-profile must be default, flat, or adaptive.")
        if data.get("session_type") != "wayland":
            raise ValueError("Expected a GNOME Wayland snapshot.")
    return data


def load_snapshot(path, kind):
    try:
        return validate(json.loads(path.read_text(encoding="utf-8-sig")), kind)
    except ValueError as error:
        raise ValueError(f"{path}: {error}") from error


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
        "Windows curve bytes are archival. Measure pointer travel before judging similarity.",
    ])
    return "\n".join(lines)


def write_snapshot(data, path):
    payload = json.dumps(data, indent=2, allow_nan=False) + "\n"
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

    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            windows = load_snapshot(args.windows, "windows")
            gnome = load_snapshot(args.gnome, "gnome") if args.gnome else collect_gnome()
            print(comparison(windows, gnome))
        else:
            data = collect_windows() if args.command == "export" else collect_gnome()
            write_snapshot(data, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"mousebridge: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
