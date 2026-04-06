"""Monitor de foco de ventanas — detecta quién roba el foreground.

Ejecutar en una terminal SEPARADA mientras corre el bot:
    python tools/monitor_focus.py

Registra cada cambio de ventana foreground con timestamp, PID, título y exe.
"""
import ctypes
import ctypes.wintypes as wt
import time
import os

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
psapi = ctypes.windll.psapi

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

def get_fg_info():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None, 0, "(ninguna)", "(ninguna)"
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    title = buf.value or "(sin titulo)"
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = "(desconocido)"
    hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if hproc:
        buf2 = ctypes.create_unicode_buffer(512)
        size = wt.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(hproc, 0, buf2, ctypes.byref(size)):
            exe = os.path.basename(buf2.value)
        kernel32.CloseHandle(hproc)
    return hwnd, pid.value, title, exe

def find_tracked_windows():
    """Busca Tibia (client.exe) y Proyector (OBS) por exe + título exacto."""
    tracked = {}  # label -> (hwnd, title)
    results = []
    def cb(h, _):
        if user32.IsWindowVisible(h):
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(h, buf, 512)
            title = buf.value
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            exe = ""
            hproc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
            if hproc:
                buf2 = ctypes.create_unicode_buffer(512)
                size = wt.DWORD(512)
                if kernel32.QueryFullProcessImageNameW(hproc, 0, buf2, ctypes.byref(size)):
                    exe = os.path.basename(buf2.value).lower()
                kernel32.CloseHandle(hproc)
            results.append((h, title, exe, pid.value))
        return True
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.POINTER(ctypes.c_long))
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    for h, title, exe, pid in results:
        tl = title.lower()
        if exe == "client.exe" and "tibia" in tl:
            tracked["Tibia-client"] = (h, title[:50])
        elif "proyector" in tl:
            tracked["Proyector"] = (h, title[:50])
        elif exe == "obs64.exe" and "obs" in tl and "proyector" not in tl:
            tracked["OBS-main"] = (h, title[:50])
    return tracked

def main():
    print("=== Monitor de foco de ventanas v2 ===")
    print("Rastreo por exe: client.exe=Tibia, Proyector, OBS-main")
    print("Ctrl+C para detener\n")

    # Initial discovery
    tracked = find_tracked_windows()
    for label, (h, t) in tracked.items():
        print(f"  [{label}] hwnd={h:#010x}  {t}")
    print()

    prev_hwnd = None
    n = 0
    refresh_counter = 0
    while True:
        hwnd, pid, title, exe = get_fg_info()
        if hwnd != prev_hwnd:
            n += 1
            ts = time.strftime("%H:%M:%S")
            # Check iconic state of tracked windows
            alerts = ""
            for label, (th, tt) in tracked.items():
                if user32.IsIconic(th):
                    alerts += f"  >> {label}=MINIMIZED"
                # Also check if window rect is at -32000 (minimized to tray)
                wr = ctypes.wintypes.RECT()
                user32.GetWindowRect(th, ctypes.byref(wr))
                if wr.left <= -30000:
                    if "MINIMIZED" not in alerts or label not in alerts:
                        alerts += f"  >> {label}=TRAY({wr.left},{wr.top})"
            if not alerts:
                alerts = "  OK all visible"
            print(f"[{ts}] #{n:4d}  hwnd={hwnd:#010x}  pid={pid:6d}  exe={exe:30s}  title={title[:50]}{alerts}")
            prev_hwnd = hwnd
        # Refresh tracked windows every ~5 seconds
        refresh_counter += 1
        if refresh_counter >= 100:
            tracked = find_tracked_windows()
            refresh_counter = 0
        time.sleep(0.05)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nDetenido.")
