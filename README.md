# MouseBridge

MouseBridge is a read-only Python CLI for comparing Windows mouse pointer settings with Ubuntu GNOME on Wayland. It requires Python 3.10 or newer and uses only the Python standard library at runtime. GNOME collection also uses the desktop's `gsettings` command.

## Collect your baseline

Use the same external mouse, DPI preset, and connection mode on both systems. Record the mouse model, active DPI and polling rate, and display scaling for each OS. Keep your preferred settings during the baseline capture.

1. In Windows, open PowerShell in this folder under your normal desktop account.

   ```powershell
   py -3 mousebridge.py export -o windows-mouse.json
   ```

   In Windows Mouse settings, open Additional mouse settings and check **Pointer Options > Enhance pointer precision**. Record whether the box is checked. If the `py` launcher is unavailable, use `python`.

2. Copy the MouseBridge folder and `windows-mouse.json` to Ubuntu. From a terminal inside your logged-in GNOME Wayland desktop, run these commands as your regular user.

   ```sh
   python3 mousebridge.py snapshot -o ubuntu-mouse.json
   python3 mousebridge.py analyze windows-mouse.json --gnome ubuntu-mouse.json
   ```

   Omit `--gnome ubuntu-mouse.json` to read the current GNOME settings directly. With both JSON files, analysis works offline on any supported Python host.

3. Keep both JSON files and the comparison output together. Snapshot output takes a new filename so earlier baselines are preserved. To provide data for the next phase, share the two JSON files, the Enhance pointer precision checkbox state, and your setup notes.

To install the CLI entry point, run `python -m pip install .` in a virtual environment and replace `python mousebridge.py` with `mousebridge`.

## Saved baseline

Enhance pointer precision (EPP) was enabled in Windows for the saved `windows-mouse.json` baseline, which records `MouseSensitivity=10` and `MouseSpeed=1`. The EPP checkbox state was confirmed separately. The export captures the registry settings and curve bytes.

The saved `ubuntu-mouse.json` baseline uses GNOME Wayland with speed `0.2` and the `flat` profile. For this pair of snapshots, the comparison suggests testing `adaptive` at `0.2` as a starting point for measuring pointer travel.

## Windows EPP curve

The saved Windows build is `10.0.26200`. The model uses the reported setup of **125% display scaling**, **pointer speed 10/20**, **2400 DPI**, and **125 Hz**.

Each registry curve value contains five 8-byte little-endian entries. Divide each entry by 65,536 to decode its 16.16 fixed-point coordinate, then pair X and Y entries by index. The registry function `f(x)` linearly interpolates between these points.

| Registry X | Registry Y | Modeled horizontal hand speed (cm/s) | Modeled gain (pointer px/raw count) |
| ---: | ---: | ---: | ---: |
| 0 | 0 | 0 | 0.7111 (low-speed limit) |
| 0.430007935 | 1.070266724 | 0.1991 | 0.7111 |
| 1.25 | 4.140625 | 0.5788 | 0.9464 |
| 3.860000610 | 18.984375 | 1.7873 | 1.4052 |
| 40 | 443.75 | 18.5208 | 3.1696 |

The published Windows 11 analysis scales the registry X coordinates by 3.5 and the Y coordinates by `(max(D, 96) / 120) × (S / 10)`, where `D` is the effective DPI of the display region containing the cursor and `S` is the Windows pointer-speed setting. At 125% scaling and speed 10/20, `D = 120` and the Y factor is 1. For each nonzero report, the model uses `r = max(|dx|, |dy|) + min(|dx|, |dy|) / 2` raw counts and gives a steady gain of approximately `f(r / 3.5) / r`. [Windows 11 reverse-engineering notes](https://github.com/junaga/windows-pointer-linux/blob/main/docs/research/windows-11.md)

For steady horizontal motion at hand speed `v` in cm/s, with evenly spaced reports:

```text
Raw counts per report ≈ (2400 / 2.54 / 125) × v ≈ 7.559055 × v
Pointer speed (px/s)  ≈ 125 × f(2.159730 × v)
Gain (px/raw count)   ≈ f(2.159730 × v) / (7.559055 × v), for v > 0
```

Pointer speed is zero at rest. The modeled gain crosses **1 px/count at 4.7500 counts per report**, equivalent to **0.6284 cm/s** under these assumptions. The endpoint at **18.5208 cm/s** is the last stored breakpoint.

The values and crossover were checked against the saved bytes. The published analysis audited builds `26100.3194` and `28000.2525`. A cursor trace from build `26200` is still needed to validate the model on this setup. Integer report counts, fixed-point rounding, subpixel remainders, and one-report gain blending when entering a higher segment can affect individual swipes.

### Recheck the Windows baseline

1. Confirm **Enhance pointer precision** is checked under **Additional mouse settings > Pointer Options**.
2. Confirm **125% display scaling** on the monitor used for testing and **2400 DPI / 125 Hz** in the active mouse profile.
3. Export to a fresh file with `py -3 mousebridge.py export -o windows-mouse-verification.json`. Compare the build, `MouseSensitivity`, `MouseSpeed`, and both curve blobs with the original snapshot.

## How to read the comparison

- The Windows export reads `HKCU\Control Panel\Mouse`. It records `MouseSensitivity` (pointer speed setting), `MouseSpeed` (acceleration flag), both thresholds, and the exact `SmoothMouseXCurve` and `SmoothMouseYCurve` bytes as hex. Missing or unusable values appear as JSON null. This is a registry snapshot of the current user's stored preferences.
- The GNOME snapshot reads `speed` and `accel-profile` from `org.gnome.desktop.peripherals.mouse`. These are desktop settings. A device may use its default profile when the configured one is unsupported. See the [GNOME schema](https://github.com/GNOME/gsettings-desktop-schemas/blob/master/schemas/org.gnome.desktop.peripherals.gschema.xml.in#L111-L132).
- The report keeps the current GNOME speed as a baseline. Its profile heuristic suggests testing `flat` for `MouseSpeed=0`, `adaptive` for `1` or `2`, and retaining the current profile for any other value. Verify the Windows checkbox before interpreting the hint. GNOME flat applies a constant device-specific factor. Adaptive responds to movement speed. Differences in gain and curve shape require measurement. See the [libinput profile documentation](https://wayland.freedesktop.org/libinput/doc/1.29.0/pointer-acceleration.html).

## Development

Run `python -m unittest discover -v` from the project folder. Tests use standard-library mocks for platform-specific reads.

The next phase will measure cursor travel for repeatable slow and fast physical movements on both desktops. Those measurements will guide profile and speed tuning. A later phase will add an explicit preview, confirmation, backup of the prior GNOME settings, and restore. v0.1 reads settings and writes snapshot files only.

Licensed under MIT.
