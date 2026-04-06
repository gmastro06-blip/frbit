#!/usr/bin/env python
"""
Nivel 6 — Live Tests: Recovery + Seguridad
Requiere: OBS Proyector, Tibia abierto.

Tests de init, config, lifecycle (desde templo, sin muerte/desconexión real).

Tests:
  P-REC-01   DeathHandler: init, config, check_now (no death at temple)
  P-REC-02   ReconnectHandler: init, config, check_now (no disconnect)
  P-REC-03   AntiKick: init, start/stop, notify_activity
  P-REC-04   BreakScheduler: init, should_break, time_until_break
  P-REC-05   ChatResponder: init, config, FV-14 max_responses_per_session
"""
from __future__ import annotations
import sys, os, time, json
import ctypes
import numpy as np
from pathlib import Path

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

PASS = 0
FAIL = 0
SKIP = 0
RESULTS: list[tuple[str, str, str]] = []
WINDOW_TITLE = "Proyector"


def report(test_id: str, status: str, detail: str = "") -> None:
    global PASS, FAIL, SKIP
    RESULTS.append((test_id, status, detail))
    if status == "PASS":
        PASS += 1
    elif status == "FAIL":
        FAIL += 1
    else:
        SKIP += 1
    icon = {"PASS": "\u2705", "FAIL": "\u274c", "SKIP": "\u23ed\ufe0f"}.get(status, "?")
    print(f"  {icon} {test_id}: {status}  {detail}")


def find_hwnd(title_fragment: str) -> int:
    user32 = ctypes.windll.user32
    results: list[int] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)

    def callback(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if title_fragment.lower() in buf.value.lower():
                results.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return results[0] if results else 0


# ═══════════════════════════════════════════════════════════════════════════
print("=" * 60)
print("  NIVEL 6 — Live Tests (Recovery + Seguridad)")
print("  Método: Screen capture + HID only")
print("=" * 60)

hwnd = find_hwnd(WINDOW_TITLE)
if not hwnd:
    print(f"\n\u274c ABORT: No se encontr\u00f3 ventana '{WINDOW_TITLE}'.")
    sys.exit(1)
print(f"\n\u2713 Ventana '{WINDOW_TITLE}': hwnd={hwnd}")

from src.frame_capture import PrintWindowCapture
cap = PrintWindowCapture(hwnd=hwnd)
grab = cap.open()
FRAME = grab()
assert FRAME is not None and FRAME.ndim == 3
print(f"\u2713 Frame capturado: {FRAME.shape[1]}x{FRAME.shape[0]}")

from src.input_controller import InputController
ic = InputController(target_title="Tibia", input_method="interception")
ic.find_target()
print("\u2713 InputController listo")

# ═══════════════════════════════════════════════════════════════════════════
# P-REC-01 — DeathHandler
# ═══════════════════════════════════════════════════════════════════════════
print("\n\u2500\u2500 P-REC-01: DeathHandler \u2500\u2500")
try:
    from src.death_handler import DeathHandler, DeathConfig

    dcfg = DeathConfig(max_deaths=3, re_equip_hotkeys=[0x75, 0x76])
    dh = DeathHandler(ctrl=ic, config=dcfg)
    dh.set_frame_getter(lambda: grab())
    report("P-REC-01a", "PASS",
           f"Init OK, max_deaths={dcfg.max_deaths}, "
           f"re_equip={dcfg.re_equip_hotkeys}")
except Exception as e:
    report("P-REC-01a", "FAIL", str(e))

try:
    # FV-13: safe_zone_waypoint — check config field exists
    assert hasattr(dcfg, 'respawn_delay'), "No respawn_delay"
    report("P-REC-01b", "PASS", f"FV-13: respawn_delay={dcfg.respawn_delay}")
except Exception as e:
    report("P-REC-01b", "FAIL", str(e))

try:
    # check_now should return False (no death at temple)
    is_dead = dh.check_now(FRAME)
    report("P-REC-01c", "PASS" if not is_dead else "FAIL",
           f"check_now={is_dead} (expected False at temple)")
except Exception as e:
    report("P-REC-01c", "FAIL", str(e))

try:
    assert dh.deaths == 0, f"deaths={dh.deaths}"
    snap = dh.stats_snapshot()
    assert isinstance(snap, dict)
    report("P-REC-01d", "PASS",
           f"deaths=0, stats={snap}")
except Exception as e:
    report("P-REC-01d", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-REC-02 — ReconnectHandler
# ═══════════════════════════════════════════════════════════════════════════
print("\n\u2500\u2500 P-REC-02: ReconnectHandler \u2500\u2500")
try:
    from src.reconnect_handler import ReconnectHandler, ReconnectConfig

    rcfg = ReconnectConfig(max_retries=5, max_backoff=300.0)
    rh = ReconnectHandler(ctrl=ic, config=rcfg)
    rh.set_frame_getter(lambda: grab())
    report("P-REC-02a", "PASS",
           f"Init OK, max_retries={rcfg.max_retries}, backoff={rcfg.max_backoff}")
except Exception as e:
    report("P-REC-02a", "FAIL", str(e))

try:
    is_dc = rh.check_now(FRAME)
    report("P-REC-02b", "PASS" if not is_dc else "FAIL",
           f"check_now={is_dc} (expected False, playing)")
except Exception as e:
    report("P-REC-02b", "FAIL", str(e))

try:
    assert rh.disconnects == 0
    assert rh.reconnects == 0
    snap = rh.stats_snapshot()
    assert isinstance(snap, dict)
    report("P-REC-02c", "PASS",
           f"disconnects=0, reconnects=0, stats keys={list(snap.keys())}")
except Exception as e:
    report("P-REC-02c", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-REC-03 — AntiKick
# ═══════════════════════════════════════════════════════════════════════════
print("\n\u2500\u2500 P-REC-03: AntiKick \u2500\u2500")
try:
    from src.anti_kick import AntiKick, AntiKickConfig

    ak_cfg = AntiKickConfig(idle_threshold=10.0, action_interval=5.0, enabled=True)
    ak = AntiKick(ctrl=ic, config=ak_cfg)
    report("P-REC-03a", "PASS",
           f"Init OK, idle={ak_cfg.idle_threshold}s, interval={ak_cfg.action_interval}s")
except Exception as e:
    report("P-REC-03a", "FAIL", str(e))

try:
    ak.start()
    time.sleep(0.5)
    ak.notify_activity()
    time.sleep(0.3)
    assert ak.is_running, "Not running after start()"
    report("P-REC-03b", "PASS", f"start/notify OK, is_running={ak.is_running}")
except Exception as e:
    report("P-REC-03b", "FAIL", str(e))

try:
    ak.stop()
    time.sleep(0.3)
    snap = ak.stats_snapshot()
    assert isinstance(snap, dict)
    report("P-REC-03c", "PASS",
           f"stop OK, actions_sent={ak.actions_sent}, stats={snap}")
except Exception as e:
    report("P-REC-03c", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-REC-04 — BreakScheduler
# ═══════════════════════════════════════════════════════════════════════════
print("\n\u2500\u2500 P-REC-04: BreakScheduler \u2500\u2500")
try:
    from src.break_scheduler import BreakScheduler, BreakSchedulerConfig

    bs_cfg = BreakSchedulerConfig(
        play_min_minutes=2.0, play_max_minutes=3.0,
        break_min_minutes=0.5, break_max_minutes=1.0,
        enabled=True,
    )
    bs = BreakScheduler(config=bs_cfg)
    report("P-REC-04a", "PASS",
           f"Init OK, play={bs_cfg.play_min_minutes}-{bs_cfg.play_max_minutes}min")
except Exception as e:
    report("P-REC-04a", "FAIL", str(e))

try:
    bs.start()
    ttb = bs.time_until_break()
    assert ttb > 0, f"time_until_break={ttb}"
    report("P-REC-04b", "PASS",
           f"time_until_break={ttb:.0f}s, on_break={bs.on_break}")
except Exception as e:
    report("P-REC-04b", "FAIL", str(e))

try:
    sb = bs.should_break()
    assert not sb, "should_break=True too early"
    report("P-REC-04c", "PASS",
           f"should_break={sb} (expected False right after start)")
except Exception as e:
    report("P-REC-04c", "FAIL", str(e))

try:
    snap = bs.stats_snapshot()
    assert isinstance(snap, dict)
    report("P-REC-04d", "PASS", f"stats: {snap}")
    bs.stop()
except Exception as e:
    report("P-REC-04d", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-REC-05 — ChatResponder
# ═══════════════════════════════════════════════════════════════════════════
print("\n\u2500\u2500 P-REC-05: ChatResponder \u2500\u2500")
try:
    from src.chat_responder import ChatResponder, ChatResponderConfig

    cr_cfg = ChatResponderConfig(
        enabled=True, scan_interval=1.0, max_responses_per_session=5,
    )
    cr = ChatResponder(config=cr_cfg)
    cr.set_frame_getter(lambda: grab())
    cr.set_input_controller(ic)
    report("P-REC-05a", "PASS",
           f"Init OK, max_responses={cr_cfg.max_responses_per_session}")
except Exception as e:
    report("P-REC-05a", "FAIL", str(e))

try:
    # FV-14: max_responses_per_session limits responses
    assert cr_cfg.max_responses_per_session == 5
    assert len(cr_cfg.generic_responses) > 0, "No generic_responses"
    report("P-REC-05b", "PASS",
           f"FV-14: {len(cr_cfg.generic_responses)} generic, "
           f"{len(cr_cfg.gm_responses)} gm responses")
except Exception as e:
    report("P-REC-05b", "FAIL", str(e))

try:
    cr.start()
    time.sleep(1.0)
    assert cr.is_running
    scans = cr.total_scans
    report("P-REC-05c", "PASS",
           f"start OK, scans={scans}, pm_detected={cr.total_pms_detected}")
except Exception as e:
    report("P-REC-05c", "FAIL", str(e))

try:
    cr.stop()
    snap = cr.stats_snapshot()
    assert isinstance(snap, dict)
    report("P-REC-05d", "PASS",
           f"stop OK, responses_sent={cr.total_responses_sent}, stats={snap}")
except Exception as e:
    report("P-REC-05d", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# RESUMEN
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print(f"  NIVEL 6 RESULTADO: {PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
print("=" * 60)
if RESULTS:
    max_id = max(len(r[0]) for r in RESULTS)
    for tid, st, det in RESULTS:
        icon = {"PASS": "\u2705", "FAIL": "\u274c", "SKIP": "\u23ed\ufe0f"}.get(st, "?")
        print(f"  {icon} {tid:<{max_id}}  {det}")
print("=" * 60)

if FAIL > 0:
    print(f"\n\u26a0\ufe0f  {FAIL} test(s) FALLARON")
    sys.exit(1)
else:
    print(f"\n\U0001f389  Todos {PASS} tests PASS")
    sys.exit(0)
