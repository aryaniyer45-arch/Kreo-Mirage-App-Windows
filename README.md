# Kreo Mirage Tool

A small Windows 10/11 app for the **Kreo Mirage** Bluetooth controller. It turns the controller off with one click or a button combo, shows its battery, and gives you a live view of every input.

![icon](app_icon.ico)

---

## Features

| | |
|---|---|
| **One-click turn off** | Disconnects the controller over Bluetooth, which makes it go to sleep. It stays paired, so pressing Home turns it back on. |
| **Button combos** | Turn the controller off from the controller itself (see below). |
| **Global hotkey** | **Ctrl + Alt + F12** turns the controller off from anywhere, including in games. |
| **Battery** | Exact % and charging status in PS4 mode. |
| **Live input** | Sticks, triggers, D-pad, face buttons, bumpers and Home, shown live. |
| **Mode aware** | Detects Xbox or PS4 mode automatically. The accent colour changes: Xbox green or PlayStation blue. |
| **Mode-switch hint** | Shows which buttons switch the controller to the other mode. |
| **Rumble test** | Works in both modes. |
| **Tray icon** | Closing the window keeps the app running in the tray. |
| **No admin needed** | Uses standard Windows Bluetooth functions. |

## Turning the controller off

| Mode | Controller combo | Other ways |
|---|---|---|
| **PS4 (DInput)** | Hold **Home** for 3 s | Button in the app, tray icon, Ctrl+Alt+F12 |
| **Xbox (XInput)** | Hold **Menu + R3** (right stick pressed in) for 3 s | Button in the app, tray icon, Ctrl+Alt+F12 |

Xbox mode uses Menu + R3 because, in that mode, Windows keeps the Home button to itself and apps can't read it.

You can turn the controller-combo option on or off with the **Hold combo** switch at the bottom of the app.

## Switching modes (on the controller)

| From | Hold |
|---|---|
| PS4 → Xbox | **Share + Options** |
| Xbox → PS4 | **View + Menu** |

The app shows the right combo in the top-right corner of the **Live input** panel.

## Battery

- **PS4 mode:** shows the exact % and whether it's charging. On its own, the controller only sends basic data with no battery level. The app automatically asks it for its full data (the same thing Steam does) a few seconds after it connects. You won't feel anything.
- **Xbox mode:** the Mirage doesn't send its battery level in this mode, so no app can show it.

## Lighting

The Mirage's RGB is controlled only by the **RGB button** on the controller. There is no PC command for it. According to Kreo's manual:

| Action | Result |
|---|---|
| Double-press RGB | Next lighting mode (3 modes) |
| Single-press RGB | Static / breathing |
| RGB + D-pad ↑ | Brighter |
| RGB + D-pad ↓ | Dimmer |

---

## Install & run

### Option A: run the script

1. Install **Python 3.10+** from python.org (tick *Add to PATH*).
2. Install the dependencies:
   ```
   pip install pystray pillow hidapi
   ```
3. Run it:
   ```
   python kreo_mirage_tool.py
   ```
   To run it without a terminal window, rename it to `kreo_mirage_tool.pyw` and double-click it.

### Option B: build a standalone .exe

1. Put these in one folder: `kreo_mirage_tool.py`, `setup_and_build.bat`, `app_icon.ico`.
2. Double-click **`setup_and_build.bat`**.
3. Your app is at **`dist\KreoMirageTool.exe`**. It opens without a terminal and doesn't need Python on the PC.

Rebuild after every change to the script, because the .exe is a snapshot of the script at build time.

### Start with Windows

Press **Win + R**, type `shell:startup`, and put a shortcut to `KreoMirageTool.exe` in that folder.

## Command line

| Command | What it does |
|---|---|
| `KreoMirageTool.exe --disconnect` | Turns the controller off and exits. Useful for a desktop shortcut or a macro key. |
| `python kreo_mirage_tool.py --list` | Lists your paired Bluetooth devices. |

---

## How it works

- **Finding the controller:** it looks for a paired device named *Xbox Wireless Controller* (Xbox mode) or *Wireless Controller* (PS4 mode), or with *Mirage*/*Kreo* in the name, and picks whichever is connected.
- **Disconnecting:** it sends Windows' Bluetooth "disconnect device" command to the adapter. If that doesn't work, it switches the controller's Bluetooth input (HID) service off and back on.
- **PS4 mode input & battery:** it reads the controller's raw data in the PS4 controller format, the same way Steam and DS4Windows do.
- **Xbox mode input:** it uses Windows' standard Xbox controller support (XInput).
- **Settings:** stored in `BUILTIN_CONFIG` at the top of `kreo_mirage_tool.py`. Nothing is written to disk.

## Settings you can change in the script

| Setting | Where | Default |
|---|---|---|
| Hold time for the combos | `HOME_HOLD_SECONDS` | `3.0` |
| Xbox / PS4 accent colours | `MODE_ACCENTS` | Xbox green / PlayStation blue |
| Neutral accent (controller off) | `NEUTRAL` | Soft blue |
| PS4-mode Home button location | `home_maps` in `BUILTIN_CONFIG` | Learned from your controller |

**Learn Home** (button in the app) re-learns where the controller sends the Home button in PS4 mode. Only use it in PS4 mode. It copies the result to your clipboard so you can paste it into `home_maps`.

## Troubleshooting

| Problem | Fix |
|---|---|
| Battery shows "Checking…" for a long time | Wait about 10 s after connecting. If Steam is open, it may be talking to the controller too. |
| Some older games lose the controller in PS4 mode | That happens after the app (or Steam) switches the controller to its full data. Reconnect the controller. |
| .exe still shows the old icon | Windows is using a saved copy of the old icon. Rename the .exe or move it to another folder. |
| Windows warns when opening the .exe | That's normal for home-made .exe files. Click *More info → Run anyway*. |
| Changes don't show up | Quit the app from the tray (right-click → Quit) and reopen it. If you use the .exe, rebuild it first. |

---

## Files

| File | Purpose |
|---|---|
| `kreo_mirage_tool.py` | The app |
| `setup_and_build.bat` | Installs dependencies and builds the .exe |
| `app_icon.ico` | App / tray / .exe icon |
| `README.md` | This file |

*Not affiliated with Kreo. Kreo Mirage is a product of Kreo Tech.*
