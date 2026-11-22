# MouseBridge

MouseBridge is a read-only Python CLI for comparing Windows mouse pointer settings with Ubuntu GNOME on Wayland. It requires Python 3.10 or newer and uses only the Python standard library at runtime. GNOME collection also uses the desktop's `gsettings` command.

## Collect your baseline

Use the same external mouse, DPI preset, and connection mode on both systems. Recd the mouse model, DPI if knorown, and display scaling for each OS. Keep your preferred settings during the baseline capture.

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

## How to read the comparison

- The Windows export reads `HKCU\Control Panel\Mouse`. It records `MouseSensitivity` (pointer speed setting), `MouseSpeed` (acceleration flag), both thresholds, and the exact `SmoothMouseXCurve` and `SmoothMouseYCurve` bytes as hex. Missing or unusable values appear as JSON null. This is a registry snapshot of the current user's stored preferences.
- The GNOME snapshot reads `speed` and `accel-profile` from `org.gnome.desktop.peripherals.mouse`. These are desktop settings. A device may use its default profile when the configured one is unsupported. See the [GNOME schema](https://github.com/GNOME/gsettings-desktop-schemas/blob/master/schemas/org.gnome.desktop.peripherals.gschema.xml.in#L111-L132).
- The report keeps the current GNOME speed as a baseline. Its profile heuristic suggests testing `flat` for `MouseSpeed=0`, `adaptive` for `1` or `2`, and retaining the current profile for any other value. Verify the Windows checkbox before interpreting the hint. GNOME flat applies a constant device-specific factor. Adaptive responds to movement speed. Differences in gain and curve shape require measurement. See the [libinput profile documentation](https://wayland.freedesktop.org/libinput/doc/1.29.0/pointer-acceleration.html).

## Development

Run `python -m unittest discover -v` from the project folder. Tests use standard-library mocks for platform-specific reads.

The next phase will measure cursor travel for repeatable slow and fast physical movements on both desktops. Those measurements will guide profile and speed tuning. A later phase will add an explicit preview, confirmation, backup of the prior GNOME settings, and restore. v0.1 reads settings and writes snapshot files only.

Licensed under MIT.
