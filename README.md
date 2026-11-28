# MouseBridge

Recreate Windows Enhance Pointer Precision (EPP) on Ubuntu GNOME Wayland with YeetMouse. MouseBridge reads the Windows registry curve, plots the modeled gain, and generates a YeetMouse profile.

## Workflow

Install from the project directory in a Python 3.10+ virtual environment. The `tui` extra adds the interactive preview, and `plot` adds PNG export.

```sh
python -m pip install ".[tui,plot]"
mousebridge tui
```

1. **Capture on Windows.** Choose **Read Windows registry** in Windows Terminal. Enter the active mouse DPI, Windows display scaling, and polling rate, then confirm EPP is on under **Additional mouse settings > Pointer Options**. Save the capture as `windows-mouse.json`. To estimate polling rate, move the mouse in circles for a few seconds and record the **Likely rate** in Hz. [Mouse Polling Rate Test](https://webcammictest.com/mouse/polling-rate/)
2. **Preview and export.** Choose **Load snapshot** in the TUI on Windows or Linux. Use **Preview Windows curves** to see Windows pointer speed and EPP gain, or **Preview gain curves** to compare Windows and YeetMouse. Use **Save YeetMouse profile** to generate `yeetmouse-epp.conf` or **Export Windows curves PNG** to save `windows-curves.png`. Profile generation runs offline on either platform.
3. **Try the profile on Linux.** Install and load YeetMouse using its upstream instructions. Use the same mouse, DPI preset, and polling rate as on Windows. From the directory containing the generated profile, record the current GNOME settings and save the current YeetMouse configuration to a fresh filename before applying the trial. [YeetMouse installation](https://github.com/AndyFilter/YeetMouse#installation--uninstallation)

```sh
schema=org.gnome.desktop.peripherals.mouse
previous_profile=$(gsettings get "$schema" accel-profile)
previous_speed=$(gsettings get "$schema" speed)
printf 'Previous profile %s, previous speed %s\n' "$previous_profile" "$previous_speed"
yeetmousectl save yeetmouse-before-epp.conf
gsettings set "$schema" accel-profile flat
gsettings set "$schema" speed 0
yeetmousectl apply yeetmouse-epp.conf
yeetmousectl dump
```

Check for `accelMode=AccelMode_Lut` in the dump. Apply the generated profile at runtime before deciding whether to use it at boot. [Restore the previous settings](docs/baseline.md#restore-the-settings-after-a-trial)

For CLI use, run `mousebridge --help`. The main commands are `export`, `yeetmouse-curve`, and `plot-wincurves`. The [saved baseline and calibration notes](docs/baseline.md) include the captured values, GNOME comparison, and libinput alternatives.

## Curve functions

Each registry value, `SmoothMouseXCurve` and `SmoothMouseYCurve`, contains five little-endian 16.16 fixed-point coordinates stored in eight bytes each. Divide each entry by 65,536, pair the X and Y values, and linearly interpolate between the points. Call this function `f(x)`.

For Windows display scaling `P` in percent, pointer sensitivity `S`, and raw report deltas `dx` and `dy`, the EPP model uses:

```text
D = 96 × P / 100
A = (max(D, 96) / 120) × (S / 10)
r = max(|dx|, |dy|) + min(|dx|, |dy|) / 2
Windows gain(r) ≈ A × f(r / 3.5) / r, for r > 0
```

For steady horizontal motion at `v` cm/s with mouse DPI `C` and polling rate `H` Hz:

```text
Raw counts per report ≈ v × C / (2.54 × H)
Pointer speed (px/s)  ≈ H × A × f(r / 3.5)
YeetMouse input (counts/ms) ≈ v × C / 2540
```

The YeetMouse lookup table samples the Windows **gain** at those input speeds. It interpolates between samples and holds the last gain above the final Windows breakpoint. At zero input, the curve uses its low-speed gain limit and pointer speed is zero.

## Referenced repositories

- [Windows Pointer Linux](https://github.com/junaga/windows-pointer-linux/blob/main/docs/research/windows-11.md) documents the Windows 11 EPP algorithm used for the model.
- [YeetMouse](https://github.com/AndyFilter/YeetMouse) applies the gain lookup table on Linux. The profile was tested with [0.9.5 at commit `78dcd0d`](https://github.com/AndyFilter/YeetMouse/tree/78dcd0d573bedd5dd7b9e29e9162b28c9eb2fd7b).
- [GNOME desktop schemas](https://github.com/GNOME/gsettings-desktop-schemas/blob/main/schemas/org.gnome.desktop.peripherals.gschema.xml.in) define the desktop acceleration settings used in the comparison.
- [maccel](https://github.com/Gnarus-G/maccel) was evaluated as a Linux acceleration driver during development.

## Result

The applied YeetMouse gain closely follows the modeled Windows EPP curve through the final registry breakpoint. The two panels show the full range and the low-speed detail.

![Applied YeetMouse gain overlaid with the Windows EPP model](docs/yeetmouse-applied-curve.png)
