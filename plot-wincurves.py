"""Plot the stored Windows mouse curve and its modeled EPP-on gain."""

import argparse
import math
from pathlib import Path

from mousebridge import decode_curve, epp_output_scale, interpolate, load_snapshot, positive_number


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


def plot(xs, ys, sensitivity, y_scale, scaling, dpi, polling_rate, output):
    from PIL import Image, ImageDraw, ImageFont

    pointer_speed_per_y = y_scale * polling_rate
    reports_to_speed = polling_rate * 2.54 / dpi
    speed_per_x = 3.5 * reports_to_speed
    if not math.isfinite(xs[-1] * speed_per_x) or xs[1] * speed_per_x <= 0:
        raise ValueError("DPI and polling rate must produce a finite, positive speed range.")
    if not math.isfinite(y_scale * max(ys) / xs[1]):
        raise ValueError("Display scaling must produce a finite gain range.")
    if not math.isfinite(1.06 * max(ys) * pointer_speed_per_y):
        raise ValueError("Display scaling and polling rate must produce a finite pointer speed range.")

    def font(size):
        return ImageFont.load_default(size=size)

    def gain(x):
        if x == 0:
            return y_scale * ys[1] / (3.5 * xs[1])
        return y_scale * interpolate(xs, ys, x) / (3.5 * x)

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
        registry_knots = [(x * speed_per_x, y * pointer_speed_per_y) for x, y in zip(xs, ys) if x <= limit]
        speed_limit = limit * speed_per_x
        registry_line = registry_knots + [(speed_limit, interpolate(xs, ys, limit) * pointer_speed_per_y)]
        gain_knots = [(x * speed_per_x, gain(x)) for x in x_knots]
        samples = sorted(set(x_knots + [limit * i / 500 for i in range(501)]))
        gain_line = [(x * speed_per_x, gain(x)) for x in samples]
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("windows", type=Path, help="Windows snapshot JSON")
    parser.add_argument("--display-scale", required=True, type=positive_number, metavar="PERCENT")
    parser.add_argument("--dpi", required=True, type=positive_number)
    parser.add_argument("--polling-rate", required=True, type=positive_number, metavar="HZ")
    parser.add_argument("-o", "--output", type=Path, default=Path("windows-curves.png"),
                        help="New PNG file (default: windows-curves.png)")
    args = parser.parse_args(argv)
    try:
        registry = load_snapshot(args.windows, "windows")["registry"]
        xs, ys = decode_curve(registry)
        y_scale = epp_output_scale(registry, args.display_scale)
        plot(xs, ys, registry["MouseSensitivity"], y_scale,
             args.display_scale, args.dpi, args.polling_rate, args.output)
    except ImportError:
        parser.exit(1, "plot-wincurves: install Pillow 10.1 or newer to plot curves.\n")
    except (OSError, ValueError) as error:
        parser.exit(1, f"plot-wincurves: {error}\n")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
