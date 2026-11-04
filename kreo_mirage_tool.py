"""
Kreo Mirage Tool — one-click Bluetooth disconnect + extras for Windows 10/11.

Features
  * Finds your paired Bluetooth controller (auto-picks anything named "Mirage"/"Kreo").
  * Disconnect in one click (the controller drops the link and goes to sleep).
  * Global hotkey: Ctrl+Alt+F12 = disconnect.
  * Controller combo: hold Home for 3 s (PS4 mode) or Menu + R3 for 3 s (Xbox mode) = disconnect.
  * Battery level + rumble test (only when the controller is in XInput/Xbox mode).
  * Tray icon: closing the window keeps it in the tray.
  * Command line:  python kreo_mirage_tool.py --disconnect   (disconnect and exit,
    great for a desktop shortcut or a Stream Deck button).

No admin rights needed for the main disconnect method.
Dependencies: pip install pystray pillow     (tray icon; app still works without them)
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

APP_NAME = "Kreo Mirage Tool"
# Names the Mirage shows up as: "Xbox Wireless Controller" (Xbox mode) / "Wireless Controller" (PS4 mode)
AUTO_MATCH = ("xbox wireless controller", "wireless controller", "mirage", "kreo")
# Settings are built in — nothing is written to disk.
# home_maps = where your Kreo Mirage reports the Home button (learned on your PC):
#   054C:09CC = PS4 mode ("Wireless Controller"), 045E:02E0 = Xbox mode ("Xbox Wireless Controller")
BUILTIN_CONFIG = {
    "address": 236058065986947,
    "combo": True,
    "hotkey": True,
    "home_maps": [
        # PS4 mode: Home = byte 7 bit 0 in the normal report (learned), byte 9 bit 0 in the extended 0x11 report
        {"vid": 1356, "pid": 2508, "usage_page": 1, "usage": 5, "report_id": 1, "length": 128, "byte": 7, "mask": 1},
        {"vid": 1356, "pid": 2508, "usage_page": 1, "usage": 5, "report_id": 17, "length": 128, "byte": 9, "mask": 1},
        # Xbox mode: Windows hides Home from apps, so Menu + R3 is used instead (no entry here)
    ],
}

# --------------------------------------------------------------------------------------
# Win32 Bluetooth bindings
# --------------------------------------------------------------------------------------

class SYSTEMTIME(ctypes.Structure):
    _fields_ = [(n, wt.WORD) for n in
                ("wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute", "wSecond", "wMilliseconds")]


class BLUETOOTH_DEVICE_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("Address", ctypes.c_ulonglong),
        ("ulClassofDevice", wt.ULONG),
        ("fConnected", wt.BOOL),
        ("fRemembered", wt.BOOL),
        ("fAuthenticated", wt.BOOL),
        ("stLastSeen", SYSTEMTIME),
        ("stLastUsed", SYSTEMTIME),
        ("szName", ctypes.c_wchar * 248),
    ]


class BLUETOOTH_DEVICE_SEARCH_PARAMS(ctypes.Structure):
    _fields_ = [
        ("dwSize", wt.DWORD),
        ("fReturnAuthenticated", wt.BOOL),
        ("fReturnRemembered", wt.BOOL),
        ("fReturnUnknown", wt.BOOL),
        ("fReturnConnected", wt.BOOL),
        ("fIssueInquiry", wt.BOOL),
        ("cTimeoutMultiplier", ctypes.c_ubyte),
        ("hRadio", wt.HANDLE),
    ]


class BLUETOOTH_FIND_RADIO_PARAMS(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD)]


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD), ("Data4", ctypes.c_ubyte * 8)]


# HID service class: 00001124-0000-1000-8000-00805F9B34FB
HID_SERVICE_GUID = GUID(0x00001124, 0x0000, 0x1000, (ctypes.c_ubyte * 8)(0x80, 0x00, 0x00, 0x80, 0x5F, 0x9B, 0x34, 0xFB))
BLUETOOTH_SERVICE_DISABLE = 0
BLUETOOTH_SERVICE_ENABLE = 1

# bthioctl.h: BTH_CTL(BTH_IOCTL_BASE + 0x03) = CTL_CODE(FILE_DEVICE_BLUETOOTH, 3, METHOD_BUFFERED, FILE_ANY_ACCESS)
IOCTL_BTH_DISCONNECT_DEVICE = 0x0041000C

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    try:
        _bt = ctypes.WinDLL("BluetoothApis.dll")
    except OSError:
        _bt = ctypes.WinDLL("bthprops.cpl")
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _bt.BluetoothFindFirstRadio.argtypes = [ctypes.POINTER(BLUETOOTH_FIND_RADIO_PARAMS), ctypes.POINTER(wt.HANDLE)]
    _bt.BluetoothFindFirstRadio.restype = ctypes.c_void_p
    _bt.BluetoothFindNextRadio.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.HANDLE)]
    _bt.BluetoothFindNextRadio.restype = wt.BOOL
    _bt.BluetoothFindRadioClose.argtypes = [ctypes.c_void_p]
    _bt.BluetoothFindRadioClose.restype = wt.BOOL

    _bt.BluetoothFindFirstDevice.argtypes = [ctypes.POINTER(BLUETOOTH_DEVICE_SEARCH_PARAMS),
                                             ctypes.POINTER(BLUETOOTH_DEVICE_INFO)]
    _bt.BluetoothFindFirstDevice.restype = ctypes.c_void_p
    _bt.BluetoothFindNextDevice.argtypes = [ctypes.c_void_p, ctypes.POINTER(BLUETOOTH_DEVICE_INFO)]
    _bt.BluetoothFindNextDevice.restype = wt.BOOL
    _bt.BluetoothFindDeviceClose.argtypes = [ctypes.c_void_p]
    _bt.BluetoothFindDeviceClose.restype = wt.BOOL

    _bt.BluetoothSetServiceState.argtypes = [wt.HANDLE, ctypes.POINTER(BLUETOOTH_DEVICE_INFO),
                                             ctypes.POINTER(GUID), wt.DWORD]
    _bt.BluetoothSetServiceState.restype = wt.DWORD

    _k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p,
                                     wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
    _k32.DeviceIoControl.restype = wt.BOOL
    _k32.CloseHandle.argtypes = [wt.HANDLE]
    _k32.CloseHandle.restype = wt.BOOL


def fmt_addr(addr: int) -> str:
    return ":".join(f"{(addr >> (8 * i)) & 0xFF:02X}" for i in range(5, -1, -1))


def _open_radios():
    """Return a list of radio HANDLEs (caller must close)."""
    radios = []
    params = BLUETOOTH_FIND_RADIO_PARAMS(ctypes.sizeof(BLUETOOTH_FIND_RADIO_PARAMS))
    h = wt.HANDLE()
    find = _bt.BluetoothFindFirstRadio(ctypes.byref(params), ctypes.byref(h))
    if not find:
        return radios
    radios.append(h.value)
    while True:
        h2 = wt.HANDLE()
        if not _bt.BluetoothFindNextRadio(find, ctypes.byref(h2)):
            break
        radios.append(h2.value)
    _bt.BluetoothFindRadioClose(find)
    return radios


def _close_radios(radios):
    for r in radios:
        _k32.CloseHandle(r)


def list_devices():
    """Paired/remembered Bluetooth devices: list of dicts."""
    out = {}
    radios = _open_radios()
    try:
        for radio in radios or [None]:
            sp = BLUETOOTH_DEVICE_SEARCH_PARAMS()
            sp.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_SEARCH_PARAMS)
            sp.fReturnAuthenticated = True
            sp.fReturnRemembered = True
            sp.fReturnConnected = True
            sp.fReturnUnknown = False
            sp.fIssueInquiry = False
            sp.cTimeoutMultiplier = 0
            sp.hRadio = radio
            info = BLUETOOTH_DEVICE_INFO()
            info.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_INFO)
            find = _bt.BluetoothFindFirstDevice(ctypes.byref(sp), ctypes.byref(info))
            if not find:
                continue
            while True:
                out[info.Address] = {
                    "address": info.Address,
                    "name": info.szName or fmt_addr(info.Address),
                    "connected": bool(info.fConnected),
                }
                info = BLUETOOTH_DEVICE_INFO()
                info.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_INFO)
                if not _bt.BluetoothFindNextDevice(find, ctypes.byref(info)):
                    break
            _bt.BluetoothFindDeviceClose(find)
    finally:
        _close_radios(radios)
    return sorted(out.values(), key=lambda d: (not d["connected"], d["name"].lower()))


def _ioctl_disconnect(addr: int):
    radios = _open_radios()
    if not radios:
        return False, "No Bluetooth adapter found (is Bluetooth turned on?)"
    last_err = 0
    try:
        for r in radios:
            a = ctypes.c_ulonglong(addr)
            ret = wt.DWORD(0)
            if _k32.DeviceIoControl(r, IOCTL_BTH_DISCONNECT_DEVICE, ctypes.byref(a), 8, None, 0,
                                    ctypes.byref(ret), None):
                return True, "Disconnected"
            last_err = ctypes.get_last_error()
    finally:
        _close_radios(radios)
    return False, f"IOCTL failed (Windows error {last_err})"


def _service_toggle_disconnect(addr: int):
    """Fallback: turn the HID service off (forces the link down) then back on so it can reconnect later."""
    radios = _open_radios()
    if not radios:
        return False, "No Bluetooth adapter found"
    try:
        info = BLUETOOTH_DEVICE_INFO()
        info.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_INFO)
        info.Address = addr
        r = radios[0]
        err = _bt.BluetoothSetServiceState(r, ctypes.byref(info), ctypes.byref(HID_SERVICE_GUID),
                                           BLUETOOTH_SERVICE_DISABLE)
        if err != 0:
            return False, f"Service toggle failed (error {err}; try running as administrator)"
        time.sleep(1.5)
        _bt.BluetoothSetServiceState(r, ctypes.byref(info), ctypes.byref(HID_SERVICE_GUID),
                                     BLUETOOTH_SERVICE_ENABLE)
        return True, "Disconnected (service toggle)"
    finally:
        _close_radios(radios)


def is_connected(addr: int) -> bool:
    return any(d["address"] == addr and d["connected"] for d in list_devices())


def disconnect(addr: int):
    ok, msg = _ioctl_disconnect(addr)
    if ok:
        time.sleep(0.8)
        if not is_connected(addr):
            return True, msg
    ok2, msg2 = _service_toggle_disconnect(addr)
    if ok2:
        return True, msg2
    return False, f"{msg}; {msg2}"


# --------------------------------------------------------------------------------------
# XInput (works only when the Mirage is in XInput / Xbox mode)
# --------------------------------------------------------------------------------------

class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [("wButtons", wt.WORD), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short),
                ("sThumbRX", ctypes.c_short), ("sThumbRY", ctypes.c_short)]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wt.DWORD), ("Gamepad", XINPUT_GAMEPAD)]


class XINPUT_VIBRATION(ctypes.Structure):
    _fields_ = [("wLeftMotorSpeed", wt.WORD), ("wRightMotorSpeed", wt.WORD)]


class XINPUT_BATTERY_INFORMATION(ctypes.Structure):
    _fields_ = [("BatteryType", ctypes.c_ubyte), ("BatteryLevel", ctypes.c_ubyte)]


XINPUT_GUIDE = 0x0400  # only visible through the hidden XInputGetStateEx (ordinal 100)
HOME_HOLD_SECONDS = 3.0  # how long Home must be held to disconnect
XINPUT_MENU, XINPUT_R3 = 0x0010, 0x0080   # Xbox mode can't read Home, so Menu + R3 (right stick click) is used


def xinput_menu_r3_held():
    for i in xinput_pads():
        st = xinput_state(i)
        if st and (st.Gamepad.wButtons & (XINPUT_MENU | XINPUT_R3)) == (XINPUT_MENU | XINPUT_R3):
            return True
    return False

BATTERY_TYPES = {0: "disconnected", 1: "wired", 2: "alkaline", 3: "NiMH", 0xFF: "unknown"}
BATTERY_LEVELS = {0: "Empty", 1: "Low", 2: "Medium", 3: "Full"}

_xi = None
if IS_WINDOWS:
    for dll in ("xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"):
        try:
            _xi = ctypes.WinDLL(dll)
            break
        except OSError:
            pass


def xinput_pads():
    if not _xi:
        return []
    pads = []
    for i in range(4):
        st = XINPUT_STATE()
        if _xi.XInputGetState(i, ctypes.byref(st)) == 0:
            pads.append(i)
    return pads


def xinput_state(i):
    st = XINPUT_STATE()
    return st if _xi and _xi.XInputGetState(i, ctypes.byref(st)) == 0 else None


class XINPUT_STATE_EX(ctypes.Structure):
    _fields_ = [("dwPacketNumber", wt.DWORD), ("Gamepad", XINPUT_GAMEPAD), ("dwPadding", wt.DWORD)]


_xi_ex = None
if _xi:
    try:
        _xi_ex = _xi[100]          # XInputGetStateEx, reports the Guide/Home button
        _xi_ex.argtypes = [wt.DWORD, ctypes.POINTER(XINPUT_STATE_EX)]
        _xi_ex.restype = wt.DWORD
    except Exception:
        _xi_ex = None


def xinput_home_pressed():
    if not _xi_ex:
        return False
    for i in range(4):
        st = XINPUT_STATE_EX()
        if _xi_ex(i, ctypes.byref(st)) == 0 and st.Gamepad.wButtons & XINPUT_GUIDE:
            return True
    return False


def xinput_battery(i):
    """Returns (text, trustworthy). Many 3rd-party pads always report 'Empty', so flag that as unreliable."""
    if not _xi or not hasattr(_xi, "XInputGetBatteryInformation"):
        return None, False
    b = XINPUT_BATTERY_INFORMATION()
    if _xi.XInputGetBatteryInformation(i, 0, ctypes.byref(b)) != 0:
        return None, False
    if b.BatteryType == 1:
        return "Wired / charging", True
    if b.BatteryType in (0, 0xFF) or b.BatteryLevel == 0:
        return f"not reported (XInput says type={b.BatteryType}, level={b.BatteryLevel})", False
    return BATTERY_LEVELS.get(b.BatteryLevel, "?"), True


def windows_battery_percent(name, addr=None):
    """Battery % that Windows itself stores for the Bluetooth device (what Settings > Bluetooth shows)."""
    import subprocess
    key = "'{104EA319-6EE2-4701-BD47-8DDBF425BBE5} 2'"
    if addr:
        match = f"$_.InstanceId -match '{addr:012X}'"
    else:
        match = "$_.FriendlyName -like '*" + name.replace("'", "''") + "*'"
    ps = ("Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object { " + match + " } | ForEach-Object { "
          "$p = Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName " + key +
          " -ErrorAction SilentlyContinue; if ($p -and $p.Data -ne $null) { $p.Data } } | Select-Object -First 1")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=10, creationflags=0x08000000).stdout.strip()
        return int(out.splitlines()[0]) if out else None
    except Exception:
        return None


class XboxBatteryMonitor(threading.Thread):
    """Xbox mode: real Xbox Bluetooth pads send a separate battery report (ID 0x04).
    Listens for it in case the Mirage does too. level is (percent_estimate, charging)."""
    LEVELS = {0: 5, 1: 30, 2: 65, 3: 100}

    def __init__(self):
        super().__init__(daemon=True)
        self.battery = None
        self.last_seen = 0.0

    def run(self):
        while True:
            try:
                import hid
            except ImportError:
                return
            devs = [d for d in hid.enumerate() if d.get("vendor_id") == 0x045E]
            if not devs:
                time.sleep(3)
                continue
            opened = []
            for d in devs:
                h = hid.device()
                try:
                    h.open_path(d["path"])
                    h.set_nonblocking(1)
                    opened.append(h)
                except Exception:
                    pass
            if not opened:
                time.sleep(5)
                continue
            last_any = time.time()
            try:
                while time.time() - last_any < 8:
                    got = False
                    for h in opened:
                        try:
                            buf = h.read(64)
                        except Exception:
                            buf = None
                        if not buf:
                            continue
                        got = True
                        last_any = time.time()
                        if buf[0] == 0x04 and len(buf) >= 2:
                            v = buf[1]
                            self.battery = (self.LEVELS[v & 0x03], bool(v & 0x10))
                            self.last_seen = time.time()
                    if not got:
                        time.sleep(0.05)
            finally:
                for h in opened:
                    try:
                        h.close()
                    except Exception:
                        pass
            time.sleep(1)


# --------------------------------------------------------------------------------------
# PS4 / DInput mode battery (DualShock 4 protocol, same way Steam reads it)
#   needs: pip install hidapi
# --------------------------------------------------------------------------------------

def _ds4_candidates():
    try:
        import hid
    except ImportError:
        return None, []
    out = []
    for d in hid.enumerate():
        if d.get("usage_page") != 1 or d.get("usage") != 5:   # Generic Desktop / Gamepad
            continue
        prod = (d.get("product_string") or "").lower()
        if d.get("vendor_id") == 0x054C or any(k in prod for k in ("mirage", "kreo", "wireless controller")):
            out.append(d)
    return hid, out


def _is_reduced(buf):
    """Bluetooth 'basic' report: only buttons/sticks, everything after byte 10 is empty.
    (Windows pads every report to the same length, so the length alone can't tell.)"""
    return bool(buf) and buf[0] == 0x01 and not any(buf[10:40])


def _parse_ds4_battery(buf):
    """Return (percent, charging) from a DS4 input report, or None."""
    if not buf or _is_reduced(buf):
        return None
    rid = buf[0]
    if rid == 0x11 and len(buf) >= 33:      # Bluetooth full report
        b = buf[32]
        maxv = 10
    elif rid == 0x01 and len(buf) >= 31:    # USB report (BT "reduced" 0x01 reports are only ~10 bytes)
        b = buf[30]
        maxv = 11 if b & 0x10 else 10
    else:
        return None
    level = b & 0x0F
    charging = bool(b & 0x10)
    return min(100, level * 100 // maxv), charging


def _is_ds4_report(buf):
    if not buf:
        return False
    if buf[0] == 0x11 and len(buf) >= 33:
        return (buf[3 + 4] & 0x0F) <= 8      # d-pad hat value is 0-8 on a DS4
    if buf[0] == 0x01 and len(buf) >= 10:
        return (buf[5] & 0x0F) <= 8
    return False


def _ds4_ps_pressed(buf):
    if buf[0] == 0x11:
        return bool(buf[9] & 0x01)
    return bool(buf[7] & 0x01)


class DS4Monitor(threading.Thread):
    """Keeps reading the controller in PS4 mode: tracks the Home (PS) button and the battery."""

    def __init__(self, allow_enable=True):
        super().__init__(daemon=True)
        self.allow_enable = allow_enable
        self.home_down = False
        self.battery = None          # (percent, charging)
        self.last_seen = 0.0
        self.last_buf = None

    def run(self):
        while True:
            hid, cands = _ds4_candidates()
            if not hid:
                return               # hidapi not installed
            cands = [d for d in cands if d.get("vendor_id") != 0x045E]   # skip Xbox-mode devices
            if not cands:
                self.home_down = False
                time.sleep(2)
                continue
            self._watch(hid, cands[0])
            self.home_down = False
            time.sleep(1)

    def _watch(self, hid, d):
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except Exception:
            time.sleep(2)
            return
        asked_full = 0
        started = time.time()
        # Bluetooth HID devices have the HID service GUID in their Windows path
        is_bt = "00001124" in (d["path"].decode("utf-8", "ignore") if isinstance(d["path"], bytes)
                               else str(d["path"])).lower()
        self.battery = None          # forget any old reading from before a mode switch / reconnect
        try:
            while True:
                buf = dev.read(128, 250)
                if not buf:
                    if time.time() - self.last_seen > 5:
                        return       # stale; re-enumerate
                    continue
                if not _is_ds4_report(buf):
                    continue
                self.last_seen = time.time()
                self.last_buf = buf
                self.home_down = _ds4_ps_pressed(buf)
                # over Bluetooth the battery only exists in the full 0x11 report; a 0x01 report there is
                # always "basic" even if it has leftover bytes (that's what produced the fake 0%)
                basic = (is_bt and buf[0] == 0x01) or _is_reduced(buf)
                bat = None if basic else _parse_ds4_battery(buf)
                if bat:
                    self.battery = bat
                elif (self.allow_enable and basic
                      and time.time() - started > 1.5 + asked_full * 3):
                    # keep asking every 3 s for as long as the controller is still in basic mode
                    # (right after a mode switch it can ignore the first few requests)
                    asked_full += 1
                    threading.Thread(target=ds4_enable_full_reports, args=(asked_full - 1,), daemon=True).start()
        except Exception:
            return
        finally:
            try:
                dev.close()
            except Exception:
                pass


# --------------------------------------------------------------------------------------
# "Learn Home button": find the exact bit the controller uses for Home, in any mode
# --------------------------------------------------------------------------------------

def _hid_gamepad_paths():
    """Gamepad HID collections plus any other collections (e.g. consumer 'Home') of the same device."""
    try:
        import hid
    except ImportError:
        return None, []
    allv = hid.enumerate()
    pads = {(d["vendor_id"], d["product_id"]) for d in allv
            if d.get("usage_page") == 1 and d.get("usage") in (4, 5)}
    out = [d for d in allv if (d["vendor_id"], d["product_id"]) in pads]
    return hid, out


def _open_all(hid, devs):
    opened = []
    for d in devs:
        h = hid.device()
        try:
            h.open_path(d["path"])
            h.set_nonblocking(1)
            opened.append((d, h))
        except Exception:
            pass
    return opened


def _collect(opened, seconds, on_report):
    end = time.time() + seconds
    while time.time() < end:
        got = False
        for d, h in opened:
            try:
                buf = h.read(128)
            except Exception:
                continue
            if buf:
                got = True
                on_report(d, buf)
        if not got:
            time.sleep(0.005)


def learn_home(say):
    """Interactive: returns mapping dict or raises RuntimeError with a message."""
    hid, devs = _hid_gamepad_paths()
    if hid is None:
        raise RuntimeError("hidapi isn't installed. Run: pip install hidapi")
    if not devs:
        raise RuntimeError("No controller found. Make sure it's connected.")
    opened = _open_all(hid, devs)
    if not opened:
        raise RuntimeError("Couldn't open the controller's input. Close DS4Windows/other tools and retry.")
    try:
        key = lambda d, buf: (d["vendor_id"], d["product_id"], d.get("usage_page"), d.get("usage"), buf[0], len(buf))
        noisy, last_idle = {}, {}

        def idle(d, buf):
            k = key(d, buf)
            if k in last_idle:
                prev = last_idle[k]
                n = noisy.setdefault(k, [0] * len(buf))
                for i in range(len(buf)):
                    n[i] |= prev[i] ^ buf[i]
            else:
                noisy.setdefault(k, [0] * len(buf))
            last_idle[k] = list(buf)

        say("Don't touch the controller…")
        _collect(opened, 2.0, idle)
        say("HOLD the Home button now (about 2 seconds, don't go to 3)…")
        time.sleep(0.6)
        held = {}
        _collect(opened, 1.4, lambda d, buf: held.__setitem__(key(d, buf), (d, list(buf))))
        say("Let go of Home…")
        time.sleep(0.6)
        released = {}
        _collect(opened, 1.2, lambda d, buf: released.__setitem__(key(d, buf), list(buf)))

        candidates = []
        for k, (d, hb) in held.items():
            base = last_idle.get(k)
            if base is None:
                # report type only sent while Home is pressed (typical for a separate 'Home' report)
                base = released.get(k, [0] * len(hb))
            n = noisy.get(k, [0] * len(hb))
            rel = released.get(k, base)
            for i in range(1, len(hb)):
                diff = (hb[i] ^ base[i]) & ~n[i] & 0xFF
                for bit in range(8):
                    m = 1 << bit
                    if diff & m and (hb[i] & m) and not (rel[i] & m):
                        candidates.append({"vid": k[0], "pid": k[1], "usage_page": k[2], "usage": k[3],
                                           "report_id": k[4], "length": k[5], "byte": i, "mask": m})
        if not candidates:
            raise RuntimeError("Didn't see any change while Home was held. The controller may keep the Home "
                               "button to itself in this mode — try the other mode.")
        # prefer gamepad collection, then lowest byte
        candidates.sort(key=lambda c: (c["usage_page"] != 1, c["byte"]))
        return candidates[0]
    finally:
        for _, h in opened:
            try:
                h.close()
            except Exception:
                pass


class LearnedHomeWatcher(threading.Thread):
    def __init__(self, get_maps):
        super().__init__(daemon=True)
        self.get_maps = get_maps
        self.home_down = False
        self.active = False

    def run(self):
        while True:
            maps = self.get_maps()
            hid, devs = _hid_gamepad_paths()
            if not maps or hid is None:
                self.home_down = self.active = False
                time.sleep(2)
                continue
            want = [d for d in devs if any(m["vid"] == d["vendor_id"] and m["pid"] == d["product_id"] and
                                           m["usage_page"] == d.get("usage_page") and m["usage"] == d.get("usage")
                                           for m in maps)]
            opened = _open_all(hid, want)
            if not opened:
                self.home_down = self.active = False
                time.sleep(2)
                continue
            self.active = True
            last = time.time()
            try:
                while time.time() - last < 6 or self.home_down:
                    got = False
                    for d, h in opened:
                        try:
                            buf = h.read(128)
                        except Exception:
                            buf = None
                            last = 0
                        if not buf:
                            continue
                        got = True
                        last = time.time()
                        for m in maps:
                            if (m["vid"], m["pid"], m["usage_page"], m["usage"]) == \
                                    (d["vendor_id"], d["product_id"], d.get("usage_page"), d.get("usage")) \
                                    and buf[0] == m["report_id"] and len(buf) > m["byte"]:
                                self.home_down = bool(buf[m["byte"]] & m["mask"])
                    if last == 0:
                        break
                    if not got:
                        time.sleep(0.01)
            finally:
                for _, h in opened:
                    try:
                        h.close()
                    except Exception:
                        pass
                self.home_down = self.active = False
            time.sleep(0.5)


# --------------------------------------------------------------------------------------
# Native Windows HID helpers: use the controller's real report sizes (not DS4 guesses)
# --------------------------------------------------------------------------------------

class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("UsagePage", wt.USHORT), ("InputReportByteLength", wt.USHORT),
                ("OutputReportByteLength", wt.USHORT), ("FeatureReportByteLength", wt.USHORT),
                ("Reserved", wt.USHORT * 17), ("NumberLinkCollectionNodes", wt.USHORT),
                ("NumberInputButtonCaps", wt.USHORT), ("NumberInputValueCaps", wt.USHORT),
                ("NumberInputDataIndices", wt.USHORT), ("NumberOutputButtonCaps", wt.USHORT),
                ("NumberOutputValueCaps", wt.USHORT), ("NumberOutputDataIndices", wt.USHORT),
                ("NumberFeatureButtonCaps", wt.USHORT), ("NumberFeatureValueCaps", wt.USHORT),
                ("NumberFeatureDataIndices", wt.USHORT)]


_hiddll = None
if IS_WINDOWS:
    try:
        _hiddll = ctypes.WinDLL("hid.dll")
        _k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD,
                                     wt.HANDLE]
        _k32.CreateFileW.restype = wt.HANDLE
        _k32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
        _k32.WriteFile.restype = wt.BOOL
        _hiddll.HidD_GetPreparsedData.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        _hiddll.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
        _hiddll.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
        _hiddll.HidD_GetFeature.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
        _hiddll.HidD_GetFeature.restype = wt.BOOLEAN
        _hiddll.HidD_SetOutputReport.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG]
        _hiddll.HidD_SetOutputReport.restype = wt.BOOLEAN
    except Exception:
        _hiddll = None

INVALID_HANDLE = ctypes.c_void_p(-1).value


class _HidHandle:
    def __init__(self, path):
        if isinstance(path, bytes):
            path = path.decode("utf-8", "ignore")
        self.h = None
        for access in (0xC0000000, 0x40000000, 0):           # read+write, write, query-only
            h = _k32.CreateFileW(path, access, 0x3, None, 3, 0, None)
            if h and h != INVALID_HANDLE:
                self.h = h
                break
        if not self.h:
            raise OSError(f"can't open controller (Windows error {ctypes.get_last_error()})")
        self.caps = HIDP_CAPS()
        pp = ctypes.c_void_p()
        if _hiddll.HidD_GetPreparsedData(self.h, ctypes.byref(pp)):
            _hiddll.HidP_GetCaps(pp, ctypes.byref(self.caps))
            _hiddll.HidD_FreePreparsedData(pp)

    def get_feature(self, rid):
        n = self.caps.FeatureReportByteLength or 64
        buf = (ctypes.c_ubyte * n)()
        buf[0] = rid
        return bool(_hiddll.HidD_GetFeature(self.h, buf, n))

    def write_output(self, data):
        n = self.caps.OutputReportByteLength
        if not n:
            return False
        buf = (ctypes.c_ubyte * n)(*(list(data)[:n] + [0] * max(0, n - len(data))))
        done = wt.DWORD(0)
        if _k32.WriteFile(self.h, buf, n, ctypes.byref(done), None):
            return True
        return bool(_hiddll.HidD_SetOutputReport(self.h, buf, n))

    def close(self):
        if self.h:
            _k32.CloseHandle(self.h)
            self.h = None


def _ps4_paths():
    hid, cands = _ds4_candidates()
    if hid is None:
        return []
    return [d["path"] for d in cands if d.get("vendor_id") != 0x045E]


ENABLE_DIAG = ""   # last result of ds4_enable_full_reports, shown in the battery panel


def ds4_enable_full_reports(attempt=0):
    """Switch the controller to full reports (incl. battery). On the Mirage, a rumble command does it
    (found by pressing Rumble), so send one with the motors at zero — nothing is felt.
    If that's ignored, later attempts use a 1/255 pulse for 50 ms (imperceptible)."""
    global ENABLE_DIAG
    paths = _ps4_paths()
    if not paths or not _hiddll:
        ENABLE_DIAG = "no PS4-mode device"
        return False
    for p in paths:
        try:
            h = _HidHandle(p)
        except OSError as e:
            ENABLE_DIAG = f"couldn't switch ({e})"
            continue
        try:
            out = h.caps.OutputReportByteLength
            if not out:
                ENABLE_DIAG = "couldn't switch (controller takes no commands)"
                continue
            if attempt == 0:
                ok = h.write_output(_build_ds4_output(out, None, 0, 0, flags=0x01))
            else:
                ok = h.write_output(_build_ds4_output(out, None, 1, 1, flags=0x01))
                time.sleep(0.05)
                h.write_output(_build_ds4_output(out, None, 0, 0, flags=0x01))
            if ok:
                ENABLE_DIAG = "switched"
                return True
            ENABLE_DIAG = f"couldn't switch (write refused, out={out})"
        finally:
            h.close()
    return False


def _build_ds4_output(size, rgb, weak, strong, flags=None):
    import zlib
    if flags is None:
        flags = 0x01 if (weak or strong) else 0x00
        if rgb is not None:
            flags |= 0x02 | 0x04
    if size >= 78:                         # Bluetooth 0x11 report (with CRC)
        buf = [0] * size
        buf[0], buf[1], buf[3] = 0x11, 0xC0, flags
        o = 6
    else:                                  # USB-style 0x05 report
        buf = [0] * max(size, 32)
        buf[0], buf[1] = 0x05, flags
        o = 4
    buf[o], buf[o + 1] = weak & 0xFF, strong & 0xFF
    if rgb is not None:
        buf[o + 2], buf[o + 3], buf[o + 4] = (int(c) & 0xFF for c in rgb)
    if size >= 78:
        crc = zlib.crc32(bytes([0xA2] + buf[:74])) & 0xFFFFFFFF
        buf[74:78] = list(crc.to_bytes(4, "little"))
    return buf


def ds4_send_output(rgb=None, weak=0, strong=0, bluetooth=True):
    """Light-bar colour and/or rumble in PS4 mode, sized to what the controller says it accepts."""
    if not _hiddll:
        return False, "Windows HID not available"
    paths = _ps4_paths()
    if not paths:
        return False, "controller not found in PS4 mode (is hidapi installed?)"
    info = []
    for p in paths:
        try:
            h = _HidHandle(p)
        except OSError as e:
            info.append(str(e))
            continue
        try:
            out = h.caps.OutputReportByteLength
            info.append(f"output size {out}")
            if not out:
                continue
            if h.write_output(_build_ds4_output(out, rgb, weak, strong)):
                return True, "ok"
            # some clones accept the other layout
            alt = 32 if out >= 78 else 78
            if h.write_output(_build_ds4_output(alt, rgb, weak, strong)):
                return True, "ok"
            info.append(f"write refused (error {ctypes.get_last_error()})")
        finally:
            h.close()
    if all(i == "output size 0" for i in info if i.startswith("output size")) and info:
        return False, "this controller doesn't accept light/rumble commands over Bluetooth"
    return False, "; ".join(info) or "no response"


def ds4_battery(allow_enable=True):
    """Returns (percent, charging) or None. If the controller is only sending 'reduced' Bluetooth
    reports, optionally asks it for full reports (exactly what Steam / DS4Windows do)."""
    hid, cands = _ds4_candidates()
    if not hid or not cands:
        return None
    for d in cands:
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except Exception:
            continue
        try:
            for attempt in range(2):
                deadline = time.time() + 1.0
                while time.time() < deadline:
                    buf = dev.read(128, 100)
                    res = _parse_ds4_battery(buf)
                    if res:
                        return res
                if attempt == 0 and allow_enable:
                    try:
                        dev.get_feature_report(0x05, 41)   # switches DS4 BT link to full 0x11 reports
                    except Exception:
                        try:
                            dev.get_feature_report(0x02, 37)
                        except Exception:
                            break
                else:
                    break
        finally:
            dev.close()
    return None


def xinput_rumble(i, strength=0.6, seconds=0.5):
    if not _xi:
        return
    v = int(65535 * strength)
    _xi.XInputSetState(i, ctypes.byref(XINPUT_VIBRATION(v, v)))
    time.sleep(seconds)
    _xi.XInputSetState(i, ctypes.byref(XINPUT_VIBRATION(0, 0)))


# --------------------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------------------

def load_config():
    return json.loads(json.dumps(BUILTIN_CONFIG))   # fresh copy


def save_config(cfg):
    pass   # settings live in BUILTIN_CONFIG at the top of this file; changes last until the app closes


def is_controller(d, cfg):
    return d["address"] == cfg.get("address") or any(k in d["name"].lower() for k in AUTO_MATCH)


def resolve_target(cfg, devices):
    """Pick the controller to act on: a matching device that is connected right now wins
    (so it follows you between Xbox and PS4 mode); otherwise your saved one; otherwise any match."""
    matches = [d for d in devices if is_controller(d, cfg)]
    for d in matches:
        if d["connected"]:
            return d
    for d in matches:
        if d["address"] == cfg.get("address"):
            return d
    return matches[0] if matches else None


# --------------------------------------------------------------------------------------
# Global hotkey (Ctrl+Alt+F12)
# --------------------------------------------------------------------------------------

def start_hotkey_thread(callback):
    def run():
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_F12, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x7B, 0x0312
        if not user32.RegisterHotKey(None, 1, MOD_ALT | MOD_CONTROL | MOD_NOREPEAT, VK_F12):
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                callback()
    threading.Thread(target=run, daemon=True).start()


# --------------------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------------------

import math
import random
import tkinter.font as tkfont

# ---- theme ----------------------------------------------------------------------------
BG = "#0f1115"
PANEL, PANEL_EDGE = "#16191f", "#242832"
ACCENT = "#8ab4f8"            # current accent (lines/highlights) — follows the controller mode
ACCENT_FILL = "#8ab4f8"       # current fill for the main button
NEUTRAL = ("#8ab4f8", "#8ab4f8")
MODE_ACCENTS = {
    "XBOX": ("#5dc25d", "#107C10"),   # Xbox green (bright for lines, brand green for the button)
    "PS4": ("#4d9fff", "#0070D1"),    # PlayStation blue
}
GREEN, RED, AMBER = "#6fcf97", "#eb5757", "#e2b65c"
TEXT, DIM, FAINT = "#e6e8ee", "#8b91a1", "#3a3f4b"
# legacy names used elsewhere
CYAN = MAGENTA = ACCENT
W, H = 520, 830


def resource_path(name):
    """File next to the script, or bundled inside the .exe (PyInstaller unpacks to _MEIPASS)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False)
                                                                    else __file__)))
    return os.path.join(base, name)


def _rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    t = max(0.0, min(1.0, t))
    ra, ga, ba = _rgb(a)
    rb, gb, bb = _rgb(b)
    return "#%02x%02x%02x" % (int(ra + (rb - ra) * t), int(ga + (gb - ga) * t), int(ba + (bb - ba) * t))


def rrect(cv, x1, y1, x2, y2, r, **kw):
    r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def set_dark_titlebar(root):
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        dwm = ctypes.windll.dwmapi
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), 4)          # dark mode
        r, g, b = _rgb(BG)
        col = ctypes.c_int(r | (g << 8) | (b << 16))
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(col), 4)         # caption colour (Win11)
    except Exception:
        pass


class App:
    def __init__(self):
        self.cfg = load_config()
        self.q = queue.Queue()
        self.devices, self.target = [], None
        self.combo_since, self.home_armed, self.learning = None, False, False
        self.home_held, self.home_progress = False, 0.0
        self.tray = None
        self.batt_pct, self.batt_chg = None, False
        self.batt_src, self.batt_checked = "exact", False
        self.connected_since, self.was_connected = None, False
        self.busy = False
        self.flash = 0.0
        self.toast_msg = None
        self.hover = None
        self.buttons = {}
        self.t0 = time.time()

        self.ds4 = DS4Monitor(self.cfg.get("ds4_enable", True))
        self.ds4.start()
        self.learned = LearnedHomeWatcher(lambda: self.cfg.get("home_maps", []))
        self.learned.start()
        self.xbat = XboxBatteryMonitor()
        self.xbat.start()

        self.root = tk.Tk()
        self.root.title("Kreo Mirage")
        self.root.geometry(f"{W}x{H}")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        try:
            self.root.iconbitmap(default=resource_path("app_icon.ico"))
        except Exception:
            pass
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        fams = set(tkfont.families())
        ui = "Segoe UI Variable Display" if "Segoe UI Variable Display" in fams else "Segoe UI"
        self.F = {
            "title": (ui, 18, "bold"), "sub": (ui, 10), "state": (ui, 13, "bold"),
            "btn": (ui, 12, "bold"), "h": (ui, 10, "bold"), "chip": (ui, 10, "bold"),
            "small": (ui, 9), "tiny": (ui, 8), "glyph": ("Segoe UI Symbol", 9), "pct": (ui, 15, "bold"),
        }

        self.cv = tk.Canvas(self.root, width=W, height=H, bg=BG, highlightthickness=0, bd=0)
        self.cv.pack()
        self.draw_static()
        self.cv.bind("<Motion>", self.on_motion)
        self.cv.bind("<Button-1>", self.on_click)
        self.cv.bind("<Leave>", lambda e: setattr(self, "hover", None))
        set_dark_titlebar(self.root)

        if self.cfg.get("hotkey", True):
            start_hotkey_thread(lambda: self.q.put("disconnect"))
        self.setup_tray()
        self.refresh()
        self.root.after(200, self.pump)
        self.root.after(100, self.poll_combo)
        self.root.after(3000, self.periodic)
        self.root.after(800, self.battery_loop)
        self.root.after(50, self.tick)

    # ======================================================================================
    # drawing
    # ======================================================================================
    def draw_static(self):
        cv = self.cv
        cv.create_text(28, 36, text="Kreo Mirage", font=self.F["title"], fill=TEXT, anchor="w")
        cv.create_text(29, 62, text="Controller", font=self.F["sub"], fill=DIM, anchor="w")
        for (y1, y2, title) in ((470, 610, "Live input"), (624, 680, "Battery"), (694, 754, "Lighting")):
            rrect(cv, 20, y1, W - 20, y2, 14, fill=PANEL, outline=PANEL_EDGE)
            cv.create_text(36, y1 + 18, text=title, font=self.F["h"], fill=DIM, anchor="w")

    def button(self, key, x1, y1, x2, y2, label, cb, enabled=True):
        self.buttons[key] = (x1, y1, x2, y2, cb if enabled else None)
        hov = self.hover == key and enabled
        rrect(self.cv, x1, y1, x2, y2, 12, fill=mix(PANEL, TEXT, 0.06) if hov else PANEL,
              outline=mix(PANEL_EDGE, TEXT, 0.15) if hov else PANEL_EDGE, tags="dyn")
        self.cv.create_text((x1 + x2) / 2, (y1 + y2) / 2, text=label, font=self.F["chip"],
                            fill=TEXT if enabled else FAINT, tags="dyn")

    def chip(self, x1, x2, y, label, value, live):
        rrect(self.cv, x1, y, x2, y + 44, 12, fill=PANEL, outline=PANEL_EDGE, tags="dyn")
        self.cv.create_text(x1 + 14, y + 14, text=label, font=self.F["tiny"], fill=DIM, anchor="w", tags="dyn")
        self.cv.create_text(x1 + 14, y + 30, text=value, font=self.F["chip"], fill=TEXT if live else DIM,
                            anchor="w", tags="dyn")

    def draw_controller(self, cx, cy, s, col, inp):
        cv = self.cv
        shape = [(-55, -20), (-30, -28), (30, -28), (55, -20), (63, 6), (59, 31), (46, 39), (32, 31), (19, 16),
                 (-19, 16), (-32, 31), (-46, 39), (-59, 31), (-63, 6)]
        pts = []
        for x, y in shape:
            pts += [cx + x * s, cy + y * s]
        cv.create_polygon(pts, smooth=True, fill=PANEL, outline=col, width=1.5, tags="dyn")
        lx = ly = rx = ry = 0
        if inp:
            lx, ly, rx, ry = inp["lx"], inp["ly"], inp["rx"], inp["ry"]
        soft = mix(PANEL, col, 0.45)
        for (bx, by, ox, oy) in ((-22, 9, lx, ly), (22, 9, rx, ry)):
            cv.create_oval(cx + (bx - 9) * s, cy + (by - 9) * s, cx + (bx + 9) * s, cy + (by + 9) * s,
                           outline=soft, width=1, tags="dyn")
            dx, dy = cx + (bx + ox * 5) * s, cy + (by + oy * 5) * s
            cv.create_oval(dx - 3.5 * s, dy - 3.5 * s, dx + 3.5 * s, dy + 3.5 * s, fill=col, outline="", tags="dyn")
        for (ox, oy) in ((0, -5), (0, 5), (-5, 0), (5, 0)):
            x, y = cx + (-40 + ox) * s, cy + (-7 + oy) * s
            cv.create_rectangle(x - 2 * s, y - 2 * s, x + 2 * s, y + 2 * s, fill=soft, outline="", tags="dyn")
        for (ox, oy) in ((0, -6), (0, 6), (-6, 0), (6, 0)):
            x, y = cx + (40 + ox) * s, cy + (-7 + oy) * s
            cv.create_oval(x - 2.5 * s, y - 2.5 * s, x + 2.5 * s, y + 2.5 * s, fill=soft, outline="", tags="dyn")
        hc = ACCENT if self.home_held else soft
        cv.create_oval(cx - 4 * s, cy - 17 * s, cx + 4 * s, cy - 9 * s, fill=hc, outline="", tags="dyn")

    def tick(self):
        cv = self.cv
        cv.delete("dyn")
        self.buttons = {}
        t = time.time() - self.t0
        connected = bool(self.target and self.target["connected"])
        inp = self.read_input() if connected else None
        # accent follows the mode, fading smoothly when it changes
        global ACCENT, ACCENT_FILL
        want = MODE_ACCENTS.get(self.mode_name(), NEUTRAL) if connected else NEUTRAL
        cur = getattr(self, "_acc", want)
        cur = (mix(cur[0], want[0], 0.15), mix(cur[1], want[1], 0.15))
        self._acc = cur
        ACCENT, ACCENT_FILL = cur
        main = ACCENT if connected else FAINT

        # device picker (top right)
        name = self.target["name"] if self.target else "Select device"
        name = name if len(name) <= 24 else name[:23] + "…"
        x1, y1, x2, y2 = W - 236, 28, W - 24, 58
        self.buttons["device"] = (x1, y1, x2, y2, self.device_menu)
        hov = self.hover == "device"
        rrect(cv, x1, y1, x2, y2, 12, fill=mix(PANEL, TEXT, 0.05) if hov else PANEL, outline=PANEL_EDGE, tags="dyn")
        cv.create_oval(x1 + 12, y1 + 12, x1 + 18, y1 + 18, fill=GREEN if connected else FAINT, outline="", tags="dyn")
        cv.create_text(x1 + 26, (y1 + y2) / 2, text=name, font=self.F["small"], fill=TEXT, anchor="w", tags="dyn")
        cv.create_text(x2 - 12, (y1 + y2) / 2, text="⌄", font=self.F["small"], fill=DIM, anchor="e", tags="dyn")

        # status ring
        cx, cy, r = W / 2, 192, 88
        cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline=PANEL_EDGE, width=2, fill=PANEL, tags="dyn")
        if connected and not self.busy:
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline=mix(PANEL_EDGE, ACCENT, 0.55), width=2, tags="dyn")
        if self.busy:
            a = (t * 220) % 360
            cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=a, extent=90, style="arc", outline=ACCENT,
                          width=2, tags="dyn")
        if self.flash > 0:
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, outline=mix(PANEL_EDGE, ACCENT, self.flash), width=2,
                           tags="dyn")
            self.flash = max(0.0, self.flash - 0.04)
        if self.home_progress > 0:
            cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=90, extent=-359.9 * self.home_progress,
                          style="arc", outline=ACCENT, width=3, tags="dyn")
        self.draw_controller(cx, cy - 16, 1.05, main if connected else DIM, inp)

        if self.busy:
            state, scol = "Turning off…", ACCENT
        elif self.home_progress > 0:
            state, scol = f"Hold · {HOME_HOLD_SECONDS * (1 - self.home_progress):.1f}s", ACCENT
        elif connected:
            state, scol = "Connected", TEXT
        elif self.target:
            state, scol = "Off", DIM
        else:
            state, scol = "No controller", DIM
        cv.create_text(cx, cy + 44, text=state, font=self.F["state"], fill=scol, tags="dyn")
        combo = "Menu + R3" if self.mode_name() == "XBOX" else "Home"
        hint = (f"Hold {combo} {HOME_HOLD_SECONDS:g}s or press Ctrl+Alt+F12 to turn off" if connected
                else "Press Home on the controller to wake it")
        if not self.toast_msg:
            cv.create_text(cx, 300, text=hint, font=self.F["small"], fill=DIM, tags="dyn")

        # info row
        mode = self.mode_name()
        up = "—"
        if connected and self.connected_since:
            s = int(time.time() - self.connected_since)
            up = f"{s // 3600}h {s // 60 % 60:02d}m" if s >= 3600 else f"{s // 60}:{s % 60:02d}"
        link = fmt_addr(self.target["address"]) if self.target else "—"
        gap, cw = 10, (W - 40 - 20) / 3
        self.chip(20, 20 + cw, 326, "Mode", {"XBOX": "Xbox", "PS4": "PS4", "BT": "Bluetooth"}.get(mode, "—"),
                  connected)
        self.chip(20 + cw + gap, 20 + 2 * cw + gap, 326, "Connected for", up if connected else "—", connected)
        self.chip(20 + 2 * (cw + gap), W - 20, 326, "Address", link[-8:] if self.target else "—", bool(self.target))

        # power button
        x1, y1, x2, y2 = 20, 388, W - 20, 444
        enabled = bool(self.target) and not self.busy
        self.buttons["power"] = (x1, y1, x2, y2, self.do_disconnect if enabled else None)
        hov = self.hover == "power" and enabled
        if enabled and connected:
            fill = mix(ACCENT_FILL, "#ffffff", 0.12) if hov else ACCENT_FILL
            rrect(cv, x1, y1, x2, y2, 14, fill=fill, outline="", tags="dyn")
            lcol = "#ffffff"
        else:
            rrect(cv, x1, y1, x2, y2, 14, fill=PANEL, outline=PANEL_EDGE, tags="dyn")
            lcol = DIM
        label = "Turning off…" if self.busy else "Turn off controller"
        cv.create_text((x1 + x2) / 2, (y1 + y2) / 2, text="⏻   " + label, font=self.F["btn"], fill=lcol,
                       tags="dyn")

        self.draw_live(inp, connected)
        self.draw_battery(connected, t)
        self.draw_lighting(connected)

        # bottom row
        on = self.cfg.get("combo", True)
        bw = (W - 40 - 20) / 3
        bx1, by1, by2 = 20, 768, 808
        self.buttons["toggle"] = (bx1, by1, bx1 + bw, by2, self.toggle_combo)
        hov = self.hover == "toggle"
        rrect(cv, bx1, by1, bx1 + bw, by2, 12, fill=mix(PANEL, TEXT, 0.06) if hov else PANEL, outline=PANEL_EDGE,
              tags="dyn")
        sx, sy = bx1 + 14, (by1 + by2) / 2
        rrect(cv, sx, sy - 8, sx + 30, sy + 8, 8, fill=ACCENT if on else FAINT, outline="", tags="dyn")
        kx = sx + (22 if on else 8)
        cv.create_oval(kx - 6, sy - 6, kx + 6, sy + 6, fill=BG if on else DIM, outline="", tags="dyn")
        cv.create_text(sx + 40, sy, text="Hold combo", font=self.F["chip"], fill=TEXT if on else DIM, anchor="w",
                       tags="dyn")
        self.button("rumble", bx1 + bw + 10, by1, bx1 + 2 * bw + 10, by2, "Rumble", self.rumble,
                    enabled=connected and (bool(xinput_pads()) or self.mode_name() == "PS4"))
        self.button("learn", bx1 + 2 * bw + 20, by1, W - 20, by2, "Learn Home", self.learn_home_ui,
                    enabled=not self.learning)

        # toast
        if self.toast_msg:
            msg, col, ts = self.toast_msg
            age = time.time() - ts
            if age > 2.8:
                self.toast_msg = None
            else:
                k = min(1, age / 0.2) * min(1, (2.8 - age) / 0.3)
                ty = 300 + 4 * (1 - k)
                tw = min(W - 60, 40 + 7 * len(msg))
                rrect(cv, W / 2 - tw / 2, ty - 18, W / 2 + tw / 2, ty + 18, 12, fill=mix(BG, "#232733", k),
                      outline="", tags="dyn")
                cv.create_text(W / 2, ty, text=msg, font=self.F["small"], fill=mix(BG, TEXT, k), tags="dyn")

        self.root.after(50, self.tick)

    def draw_live(self, inp, connected):
        cv = self.cv
        y0 = 470
        ps = inp and inp["kind"] == "ps"
        mode = self.mode_name() if connected else None
        if mode == "PS4":
            src = "Hold Share + Options to switch to Xbox mode"
        elif mode == "XBOX":
            src = "Hold View + Menu to switch to PS4 mode"
        else:
            src = "No data" if connected else "Off"
        cv.create_text(W - 36, y0 + 18, text=src, font=self.F["tiny"], fill=ACCENT if mode in ("PS4", "XBOX") else DIM,
                       anchor="e", tags="dyn")
        g = lambda k: bool(inp and inp.get(k))
        idle = mix(PANEL, DIM, 0.18)
        line = mix(PANEL, DIM, 0.35)

        def stick(cx, cy, x, y, pressed):
            cv.create_oval(cx - 30, cy - 30, cx + 30, cy + 30, outline=line, width=1, tags="dyn")
            dx, dy = cx + x * 20, cy + y * 20
            col = ACCENT if (inp and (pressed or abs(x) > 0.08 or abs(y) > 0.08)) else line
            cv.create_oval(dx - 10, dy - 10, dx + 10, dy + 10, fill=mix(PANEL, col, 0.25), outline=col, width=1.5,
                           tags="dyn")

        stick(82, y0 + 82, inp["lx"] if inp else 0, inp["ly"] if inp else 0, g("ls"))
        stick(W - 82, y0 + 82, inp["rx"] if inp else 0, inp["ry"] if inp else 0, g("rs"))

        for (x1, key, lbl) in ((140, "lb", "L1" if ps else "LB"), (W - 186, "rb", "R1" if ps else "RB")):
            on = g(key)
            rrect(cv, x1, y0 + 38, x1 + 46, y0 + 54, 8, fill=ACCENT if on else idle, outline="", tags="dyn")
            cv.create_text(x1 + 23, y0 + 46, text=lbl, font=self.F["tiny"], fill=BG if on else DIM, tags="dyn")
        for (x1, key, lbl) in ((140, "lt", "L2" if ps else "LT"), (W - 186, "rt", "R2" if ps else "RT")):
            v = inp[key] if inp else 0
            cv.create_rectangle(x1, y0 + 62, x1 + 46, y0 + 65, fill=idle, outline="", tags="dyn")
            if v > 0.01:
                cv.create_rectangle(x1, y0 + 62, x1 + 46 * v, y0 + 65, fill=ACCENT, outline="", tags="dyn")
            cv.create_text(x1 + 23, y0 + 76, text=lbl, font=self.F["tiny"], fill=DIM, tags="dyn")

        dx, dy = 163, y0 + 112
        for (key, ox, oy) in (("up", 0, -12), ("down", 0, 12), ("left", -12, 0), ("right", 12, 0)):
            cv.create_rectangle(dx + ox - 6, dy + oy - 6, dx + ox + 6, dy + oy + 6,
                                fill=ACCENT if g(key) else idle, outline="", tags="dyn")
        cv.create_rectangle(dx - 6, dy - 6, dx + 6, dy + 6, fill=idle, outline="", tags="dyn")

        fx, fy = W - 163, y0 + 112
        face = (("n", "△"), ("e", "○"), ("s", "✕"), ("w", "□")) if ps else \
            (("n", "Y"), ("e", "B"), ("s", "A"), ("w", "X"))
        pos = {"n": (0, -15), "e": (15, 0), "s": (0, 15), "w": (-15, 0)}
        for key, lbl in face:
            ox, oy = pos[key]
            on = g(key)
            cv.create_oval(fx + ox - 9, fy + oy - 9, fx + ox + 9, fy + oy + 9, fill=ACCENT if on else idle,
                           outline="", tags="dyn")
            cv.create_text(fx + ox, fy + oy, text=lbl, font=self.F["glyph"], fill=BG if on else DIM, tags="dyn")

        cx = W / 2
        for (key, ox, lbl) in (("view", -34, "Share" if ps else "View"), ("menu", 34, "Options" if ps else "Menu")):
            on = g(key)
            rrect(cv, cx + ox - 20, y0 + 40, cx + ox + 20, y0 + 52, 6, fill=ACCENT if on else idle, outline="",
                  tags="dyn")
            cv.create_text(cx + ox, y0 + 64, text=lbl, font=self.F["tiny"], fill=DIM, tags="dyn")
        hc = ACCENT if self.home_held else idle
        cv.create_oval(cx - 13, y0 + 96, cx + 13, y0 + 122, fill=hc, outline="", tags="dyn")
        cv.create_text(cx, y0 + 109, text="⌂", font=self.F["small"], fill=BG if self.home_held else DIM, tags="dyn")
        cv.create_text(cx, y0 + 132, text="Home", font=self.F["tiny"], fill=DIM, tags="dyn")

    def draw_lighting(self, connected):
        """The Mirage's RGB is controlled only by its own RGB button (no PC command exists),
        so this panel shows the button controls from Kreo's manual."""
        cv = self.cv
        y0 = 694
        cv.create_text(W - 36, y0 + 18, text="Use the RGB button on the controller", font=self.F["tiny"],
                       fill=DIM, anchor="e", tags="dyn")
        items = (("2× press", "Next mode (3)"), ("1× press", "Static / breathing"),
                 ("+ D-pad ↑", "Brighter"), ("+ D-pad ↓", "Dimmer"))
        cw = (W - 72) / 4
        for i, (k, v) in enumerate(items):
            x = 36 + i * cw
            cv.create_text(x, y0 + 36, text=k, font=self.F["chip"], fill=ACCENT if connected else DIM,
                           anchor="w", tags="dyn")
            cv.create_text(x, y0 + 51, text=v, font=self.F["tiny"], fill=DIM, anchor="w", tags="dyn")

    def draw_battery(self, connected, t):
        cv = self.cv
        y0 = 624
        pct = self.batt_pct if connected else None
        x1, x2, by = 120, W - 110, y0 + 29
        cv.create_rectangle(x1, by - 3, x2, by + 3, fill=mix(PANEL, DIM, 0.18), outline="", tags="dyn")
        if pct is not None:
            col = RED if pct <= 20 else (AMBER if pct <= 40 else ACCENT)
            cv.create_rectangle(x1, by - 3, x1 + (x2 - x1) * pct / 100, by + 3, fill=col, outline="", tags="dyn")
            if getattr(self, "batt_src", "exact") == "approx":
                txt = {5: "Critical", 30: "Low", 65: "Medium", 100: "Full"}.get(pct, f"~{pct}%")
                sub = "Charging" if self.batt_chg else "Approximate (Xbox mode)"
            else:
                txt = f"{pct}%"
                sub = "Charging" if self.batt_chg else ("Low" if pct <= 20 else "")
        else:
            txt, col = "—", DIM
            if not connected:
                sub = ""
            elif self.mode_name() == "PS4" and ENABLE_DIAG.startswith("couldn't"):
                sub = ENABLE_DIAG[:60]
            elif getattr(self, "batt_checked", False):
                sub = "Not sent by the controller in this mode"
            else:
                sub = "Checking…"
        cv.create_text(W - 36, by - 2, text=txt, font=self.F["pct"], fill=TEXT if pct is not None else DIM,
                       anchor="e", tags="dyn")
        if sub:
            cv.create_text(W - 36, by + 17, text=sub, font=self.F["tiny"], fill=DIM, anchor="e", tags="dyn")

    # ======================================================================================
    # input
    # ======================================================================================
    def read_input(self):
        pads = xinput_pads()
        if pads:
            st = xinput_state(pads[0])
            if st:
                gp, b = st.Gamepad, st.Gamepad.wButtons
                f = lambda v: max(-1.0, min(1.0, v / 32767))
                return {"kind": "xbox", "lx": f(gp.sThumbLX), "ly": -f(gp.sThumbLY), "rx": f(gp.sThumbRX),
                        "ry": -f(gp.sThumbRY), "lt": gp.bLeftTrigger / 255, "rt": gp.bRightTrigger / 255,
                        "up": b & 0x1, "down": b & 0x2, "left": b & 0x4, "right": b & 0x8, "menu": b & 0x10,
                        "view": b & 0x20, "ls": b & 0x40, "rs": b & 0x80, "lb": b & 0x100, "rb": b & 0x200,
                        "s": b & 0x1000, "e": b & 0x2000, "w": b & 0x4000, "n": b & 0x8000}
        buf = getattr(self.ds4, "last_buf", None)
        if buf and time.time() - self.ds4.last_seen < 1.5:
            o = 2 if buf[0] == 0x11 else 0
            if len(buf) < 10 + o:
                return None
            f = lambda v: (v - 128) / 127
            b5, b6 = buf[5 + o], buf[6 + o]
            hat = b5 & 0x0F
            return {"kind": "ps", "lx": f(buf[1 + o]), "ly": f(buf[2 + o]), "rx": f(buf[3 + o]), "ry": f(buf[4 + o]),
                    "lt": buf[8 + o] / 255, "rt": buf[9 + o] / 255,
                    "up": hat in (0, 1, 7), "right": hat in (1, 2, 3), "down": hat in (3, 4, 5),
                    "left": hat in (5, 6, 7),
                    "w": b5 & 0x10, "s": b5 & 0x20, "e": b5 & 0x40, "n": b5 & 0x80,
                    "lb": b6 & 0x01, "rb": b6 & 0x02, "view": b6 & 0x10, "menu": b6 & 0x20,
                    "ls": b6 & 0x40, "rs": b6 & 0x80}
        return None

    def mode_name(self):
        if not self.target:
            return "--"
        n = self.target["name"].lower()
        return "XBOX" if "xbox" in n else ("PS4" if n == "wireless controller" else "BT")

    # ======================================================================================
    # mouse
    # ======================================================================================
    def hit(self, x, y):
        for k, (x1, y1, x2, y2, cb) in self.buttons.items():
            if x1 <= x <= x2 and y1 <= y <= y2:
                return k, cb
        return None, None

    def on_motion(self, e):
        k, cb = self.hit(e.x, e.y)
        self.hover = k
        self.cv.configure(cursor="hand2" if cb else "")

    def on_click(self, e):
        k, cb = self.hit(e.x, e.y)
        if cb:
            cb()

    def toast(self, msg, col=CYAN):
        self.toast_msg = (msg, col, time.time())

    def device_menu(self):
        m = tk.Menu(self.root, tearoff=0, bg=PANEL, fg=TEXT, activebackground=mix(PANEL, ACCENT, 0.25),
                    activeforeground="#ffffff", bd=0, font=self.F["small"])
        for d in self.devices:
            mark = "●  " if d["connected"] else "○  "
            m.add_command(label=f"{mark}{d['name']}   {fmt_addr(d['address'])}",
                          command=lambda d=d: self.select_device(d))
        if not self.devices:
            m.add_command(label="No paired Bluetooth devices", state="disabled")
        m.add_separator()
        m.add_command(label="↻  Refresh", command=self.refresh)
        x = self.root.winfo_rootx() + W - 236
        y = self.root.winfo_rooty() + 62
        m.tk_popup(x, y)

    def select_device(self, d):
        self.cfg["address"] = d["address"]
        self.refresh()
        self.toast(f"Selected {d['name']}")

    def toggle_combo(self):
        self.cfg["combo"] = not self.cfg.get("combo", True)
        self.toast("Hold combo " + ("on" if self.cfg["combo"] else "off"),
                   GREEN if self.cfg["combo"] else DIM)

    # ======================================================================================
    # logic
    # ======================================================================================
    def battery_loop(self):
        def work():
            if self.target and self.target["connected"]:
                ds = self.ds4.battery if time.time() - self.ds4.last_seen < 10 else None
                xb = self.xbat.battery if time.time() - self.xbat.last_seen < 120 else None
                if ds:
                    self.batt_pct, self.batt_chg = ds
                    self.batt_src = "exact"
                elif xb:
                    self.batt_pct, self.batt_chg = xb
                    self.batt_src = "approx"
                else:
                    w = windows_battery_percent(self.target["name"], self.target["address"])
                    if w is None:
                        pads = xinput_pads()
                        if pads and _xi and hasattr(_xi, "XInputGetBatteryInformation"):
                            bi = XINPUT_BATTERY_INFORMATION()
                            if (_xi.XInputGetBatteryInformation(pads[0], 0, ctypes.byref(bi)) == 0
                                    and bi.BatteryType in (2, 3) and bi.BatteryLevel > 0):
                                w = {1: 30, 2: 65, 3: 100}.get(bi.BatteryLevel)
                                self.batt_src = "approx"
                    else:
                        self.batt_src = "exact"
                    self.batt_pct, self.batt_chg = (w, False) if w is not None else (None, False)
                self.batt_checked = True
            else:
                self.batt_pct, self.batt_chg = None, False
        threading.Thread(target=work, daemon=True).start()
        self.root.after(15000, self.battery_loop)

    def learn_home_ui(self):
        if not messagebox.askokcancel(APP_NAME, "Click OK, keep your hands off the controller for 2 seconds, "
                                                "then hold Home when the app says so (about 2 seconds, not 3) "
                                                "and let go when asked.\n\nDo this once in each mode you use."):
            return
        self.learning = True

        def work():
            try:
                m = learn_home(lambda t: self.q.put(("homemsg", t)))
                maps = [x for x in self.cfg.get("home_maps", []) if (x["vid"], x["pid"]) != (m["vid"], m["pid"])]
                maps.append(m)
                self.cfg["home_maps"] = maps
                self.q.put(("learn_done", True, json.dumps(m)))
            except Exception as e:
                self.q.put(("learn_done", False, str(e)))
        threading.Thread(target=work, daemon=True).start()

    def setup_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError:
            self.root.protocol("WM_DELETE_WINDOW", self.quit)
            return
        try:
            img = Image.open(resource_path("app_icon.ico"))
            img.load()
        except Exception:
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rounded_rectangle((6, 18, 58, 46), radius=13, fill=(138, 180, 248))
        menu = pystray.Menu(
            pystray.MenuItem("Turn off controller", lambda: self.q.put("disconnect"), default=True),
            pystray.MenuItem("Open Kreo Mirage", lambda: self.q.put("show")),
            pystray.MenuItem("Quit", lambda: self.q.put("quit")),
        )
        self.tray = pystray.Icon("kreo_mirage_tool", img, APP_NAME, menu)
        self.tray.run_detached()

    def hide_to_tray(self):
        self.root.withdraw()
        self.notify("Still running in the tray. Ctrl+Alt+F12 powers off the controller.")

    def notify(self, text):
        if self.tray:
            try:
                self.tray.notify(text, APP_NAME)
            except Exception:
                pass

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def quit(self):
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def pump(self):
        try:
            while True:
                cmd = self.q.get_nowait()
                if cmd == "disconnect":
                    self.do_disconnect()
                elif cmd == "show":
                    self.show()
                elif isinstance(cmd, tuple) and cmd[0] == "homemsg":
                    self.toast(cmd[1], ACCENT)
                elif isinstance(cmd, tuple) and cmd[0] == "learn_done":
                    self.learning = False
                    self.home_armed = False
                    if cmd[1]:
                        self.root.clipboard_clear()
                        self.root.clipboard_append(cmd[2])
                        self.toast("Home learned · copied to clipboard", GREEN)
                        messagebox.showinfo(APP_NAME, "Learned for this session.\n\nTo keep it, paste it into "
                                                      "home_maps in BUILTIN_CONFIG at the top of the script "
                                                      "(it's already on your clipboard):\n\n" + cmd[2])
                    else:
                        self.toast("Couldn't learn Home", RED)
                        messagebox.showwarning(APP_NAME, cmd[2])
                elif isinstance(cmd, tuple) and cmd[0] == "toast":
                    self.toast(cmd[1], GREEN if cmd[2] else RED)
                elif cmd == "quit":
                    self.quit()
                    return
                elif isinstance(cmd, tuple) and cmd[0] == "result":
                    self.after_disconnect(*cmd[1:])
        except queue.Empty:
            pass
        self.root.after(150, self.pump)

    def refresh(self):
        try:
            self.devices = list_devices()
        except Exception as e:
            self.devices = []
            self.toast(f"Bluetooth error: {e}", RED)
        self.target = resolve_target(self.cfg, self.devices)
        self.track_connection()

    def track_connection(self):
        c = bool(self.target and self.target["connected"])
        if c and not self.was_connected:
            self.connected_since = time.time()
            self.batt_pct, self.batt_chg = None, False     # don't carry a reading across mode switches
            if self.target:
                self.toast(f"Connected · {self.mode_name().title() if self.mode_name() != 'PS4' else 'PS4'} mode", GREEN)
            self.root.after(1500, self.battery_loop_once)

        if not c:
            self.connected_since = None
            self.batt_checked = False
        self.was_connected = c

    def battery_loop_once(self):
        def work():
            time.sleep(1)
            ds = self.ds4.battery if time.time() - self.ds4.last_seen < 10 else None
            if ds:
                self.batt_pct, self.batt_chg = ds
        threading.Thread(target=work, daemon=True).start()

    def periodic(self):
        try:
            self.devices = list_devices()
            self.target = resolve_target(self.cfg, self.devices)
            self.track_connection()
            # live battery from the PS4-mode stream
            if self.target and self.target["connected"] and time.time() - self.ds4.last_seen < 10 and self.ds4.battery:
                self.batt_pct, self.batt_chg = self.ds4.battery
        except Exception:
            pass
        self.root.after(2000, self.periodic)

    def do_disconnect(self):
        if self.busy:
            return
        if not self.target:
            self.refresh()
        if not self.target:
            self.toast("No controller selected", RED)
            self.show()
            return
        addr, name = self.target["address"], self.target["name"]
        self.busy = True

        def work():
            try:
                ok, msg = disconnect(addr)
            except Exception as e:
                ok, msg = False, str(e)
            self.q.put(("result", name, ok, msg))
        threading.Thread(target=work, daemon=True).start()

    def after_disconnect(self, name, ok, msg):
        self.busy = False
        if ok:
            self.flash = 1.0
            self.toast("Controller turned off", ACCENT)
            self.notify(f"{name} powered off")
        else:
            self.toast("Couldn't turn it off", RED)
            self.notify(f"Couldn't disconnect: {msg}")
            messagebox.showwarning(APP_NAME, msg)
        self.refresh()

    def rumble(self):
        pads = xinput_pads()
        if not pads and self.mode_name() == "PS4":
            bt = not (self.ds4.last_buf and self.ds4.last_buf[0] == 0x01 and len(self.ds4.last_buf) >= 64)

            def work():
                ok, msg = ds4_send_output(weak=200, strong=200, bluetooth=bt)
                time.sleep(0.5)
                ds4_send_output(weak=0, strong=0, bluetooth=bt)
                if not ok:
                    self.q.put(("toast", f"Rumble failed: {msg}", False))
            self.toast("Rumble", ACCENT)
            threading.Thread(target=work, daemon=True).start()
            return
        if not pads:
            self.toast("Controller not connected", AMBER)
            return
        self.toast("Rumble", ACCENT)
        threading.Thread(target=xinput_rumble, args=(pads[0],), daemon=True).start()

    def poll_combo(self):
        if self.learning:
            self.home_progress, self.home_held = 0.0, False
            self.root.after(100, self.poll_combo)
            return
        held = (self.learned.home_down or self.ds4.home_down or xinput_home_pressed()
                or xinput_menu_r3_held())
        self.home_held = held
        if not held:
            self.home_armed = True       # must release Home once (e.g. after using it to power on)
        if self.cfg.get("combo", True) and self.target and held and self.home_armed:
            self.combo_since = self.combo_since or time.time()
            el = time.time() - self.combo_since
            self.home_progress = min(1.0, el / HOME_HOLD_SECONDS)
            if el >= HOME_HOLD_SECONDS:
                self.combo_since = None
                self.home_armed = False
                self.home_progress = 0.0
                self.do_disconnect()
                self.root.after(4000, self.poll_combo)
                return
        else:
            self.combo_since = None
            self.home_progress = 0.0
        self.root.after(30, self.poll_combo)

    def run(self):
        self.root.mainloop()


# --------------------------------------------------------------------------------------

def cli_disconnect():
    cfg = load_config()
    target = resolve_target(cfg, list_devices())
    if not target:
        print("Controller not found. Open the app once and pick it from the list.")
        return 1
    if not target["connected"]:
        print(f"{target['name']} is already disconnected.")
        return 0
    ok, msg = disconnect(target["address"])
    print(f"{target['name']}: {msg}")
    return 0 if ok else 2


def main():
    if not IS_WINDOWS:
        print("This tool only runs on Windows.")
        return 1
    if "--disconnect" in sys.argv:
        return cli_disconnect()
    if "--list" in sys.argv:
        for d in list_devices():
            print(f"{'*' if d['connected'] else ' '} {fmt_addr(d['address'])}  {d['name']}")
        return 0
    App().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
