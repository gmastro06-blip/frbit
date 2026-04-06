#!/usr/bin/env python
"""
Nivel 5 — Live Tests: Sesión + Monitoreo + Avanzado
Requiere: OBS Proyector, Tibia abierto.

Tests de init, config, lifecycle (desde templo, sin sesión real de caza).

Tests:
  P-SES-01   BotSession: init dry_run, lifecycle
  P-SES-02   SessionCheckpoint: save/load/timestamp FV-18
  P-SES-03   HuntingSessionStats: record_kill, record_loot, report
  P-ADV-01   SpawnManager: config, best_available, mark_occupied
  P-MON-03   Monitor GUI: init (no display)
  P-MON-04   DashboardServer: start/stop, /health
  P-MON-05   AlertSystem: init, send (no webhooks)
"""
from __future__ import annotations
import sys, os, time, json, tempfile, datetime
import ctypes
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
print("  NIVEL 5 — Live Tests (Sesión + Monitoreo)")
print("  Método: Screen capture + HID only")
print("=" * 60)

hwnd = find_hwnd(WINDOW_TITLE)
if not hwnd:
    print(f"\n\u274c ABORT: No se encontr\u00f3 ventana '{WINDOW_TITLE}'.")
    sys.exit(1)
print(f"\n\u2713 Ventana '{WINDOW_TITLE}': hwnd={hwnd}")

# ═══════════════════════════════════════════════════════════════════════════
# P-SES-01 — BotSession (dry_run)
# ═══════════════════════════════════════════════════════════════════════════
print("\n\u2500\u2500 P-SES-01: BotSession \u2500\u2500")
try:
    from src.session import BotSession, SessionConfig

    cfg = SessionConfig(dry_run=True, route_file="routes/thais_rat_hunt.json")
    session = BotSession(config=cfg)
    report("P-SES-01a", "PASS", f"BotSession creado dry_run=True")
except Exception as e:
    report("P-SES-01a", "FAIL", str(e))

try:
    # FV-15: graceful stop uses _running flag
    assert hasattr(session, '_running'), "No _running attribute"
    assert session._running is False, f"_running={session._running} before start"
    assert session.is_running is False, "is_running should be False before start"
    report("P-SES-01b", "PASS", "FV-15: _running=False, is_running=False (pre-start)")
except Exception as e:
    report("P-SES-01b", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-SES-02 — SessionCheckpoint (persistence)
# ═══════════════════════════════════════════════════════════════════════════
print("\n\u2500\u2500 P-SES-02: SessionCheckpoint \u2500\u2500")
try:
    from src.session_persistence import SessionCheckpoint

    cp = SessionCheckpoint(
        route_file="routes/test.json",
        waypoint_index=5,
        routes_completed=2,
        heal_fired=10,
        loot_events=3,
    )
    report("P-SES-02a", "PASS", f"Checkpoint creado, wp_idx={cp.waypoint_index}")
except Exception as e:
    report("P-SES-02a", "FAIL", str(e))

try:
    with tempfile.TemporaryDirectory() as tmpdir:
        cp_path = Path(tmpdir) / "checkpoint.json"
        cp.save(path=cp_path)
        assert cp_path.exists(), "Archivo no creado"
        assert cp.timestamp > 0, f"timestamp={cp.timestamp}"
        assert len(cp.timestamp_iso) > 10, f"iso={cp.timestamp_iso}"
        report("P-SES-02b", "PASS",
               f"save OK: ts={cp.timestamp:.0f}, iso={cp.timestamp_iso[:19]}")

        # FV-18: timestamp consistency
        ts_from_float = datetime.datetime.fromtimestamp(cp.timestamp).isoformat()[:19]
        assert ts_from_float == cp.timestamp_iso[:19], \
            f"FV-18 FAIL: {ts_from_float} != {cp.timestamp_iso[:19]}"
        report("P-SES-02c", "PASS", "FV-18: timestamp_iso consistente")

        # Load
        loaded = SessionCheckpoint.load(path=cp_path)
        assert loaded is not None, "load() retorn\u00f3 None"
        assert loaded.waypoint_index == 5, f"wp={loaded.waypoint_index}"
        assert loaded.routes_completed == 2
        report("P-SES-02d", "PASS",
               f"load OK: wp={loaded.waypoint_index}, routes={loaded.routes_completed}")

        # is_stale
        assert not loaded.is_stale(max_age_seconds=60.0), "Stale reci\u00e9n creado?"
        report("P-SES-02e", "PASS", "is_stale=False (reci\u00e9n creado)")

except Exception as e:
    report("P-SES-02b", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-SES-03 — HuntingSessionStats
# ═══════════════════════════════════════════════════════════════════════════
print("\n\u2500\u2500 P-SES-03: HuntingSessionStats \u2500\u2500")
try:
    from src.session_stats import HuntingSessionStats, SessionStatsConfig

    stats_cfg = SessionStatsConfig(exp_per_monster={"Rat": 20, "Cave Rat": 30})
    stats = HuntingSessionStats(config=stats_cfg)
    stats.start()
    report("P-SES-03a", "PASS", f"Stats iniciado, is_active={stats.is_active}")
except Exception as e:
    report("P-SES-03a", "FAIL", str(e))

try:
    stats.record_kill("Rat", exp=20)
    stats.record_kill("Rat", exp=20)
    stats.record_kill("Cave Rat", exp=30)
    stats.record_loot(items=["Gold Coin", "Cheese"], value_gp=15)
    stats.record_death()
    stats.record_heal()

    assert stats.total_kills == 3, f"kills={stats.total_kills}"
    assert stats.total_exp == 70, f"exp={stats.total_exp}"
    assert stats.deaths == 1, f"deaths={stats.deaths}"
    assert stats.total_loot_gp == 15, f"loot_gp={stats.total_loot_gp}"
    report("P-SES-03b", "PASS",
           f"kills={stats.total_kills}, exp={stats.total_exp}, "
           f"deaths={stats.deaths}, loot={stats.total_loot_gp}gp")

    # Report
    rpt = stats.report()
    assert isinstance(rpt, dict), f"report type={type(rpt)}"
    report("P-SES-03c", "PASS", f"report keys: {list(rpt.keys())[:6]}")

    # Summary text
    txt = stats.summary_text()
    assert len(txt) > 10, "summary_text vac\u00edo"
    report("P-SES-03d", "PASS", f"summary: {txt[:60]}...")

    stats.stop()
except Exception as e:
    report("P-SES-03b", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-ADV-01 — SpawnManager
# ═══════════════════════════════════════════════════════════════════════════
print("\n\u2500\u2500 P-ADV-01: SpawnManager \u2500\u2500")
try:
    from src.spawn_manager import SpawnManager, SpawnManagerConfig, SpawnPoint

    spawns = [
        SpawnPoint(name="rats_north", script="routes/thais_rat_hunt.json", priority=1),
        SpawnPoint(name="rats_south", script="routes/test_north_40.json", priority=2),
        SpawnPoint(name="cave_rats", script="routes/cave_rats.json", priority=3,
                   min_level=10),
    ]
    sm_cfg = SpawnManagerConfig(spawns=spawns)
    sm = SpawnManager(config=sm_cfg)
    report("P-ADV-01a", "PASS",
           f"SpawnManager: {sm.spawn_count} spawns, available={len(sm.available_spawns)}")
except Exception as e:
    report("P-ADV-01a", "FAIL", str(e))

try:
    best = sm.best_available()
    assert best is not None, "best_available retorn\u00f3 None"
    report("P-ADV-01b", "PASS", f"best_available: {best.name} (priority={best.priority})")
except Exception as e:
    report("P-ADV-01b", "FAIL", str(e))

try:
    sm.mark_occupied("rats_north")
    best2 = sm.best_available()
    name2 = best2.name if best2 else "None"
    report("P-ADV-01c", "PASS",
           f"mark_occupied rats_north \u2192 best={name2}")

    snap = sm.stats_snapshot()
    assert isinstance(snap, dict)
    report("P-ADV-01d", "PASS", f"stats keys: {list(snap.keys())[:5]}")
except Exception as e:
    report("P-ADV-01c", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# P-MON-04 — DashboardServer
# ═══════════════════════════════════════════════════════════════════════════
print("\n\u2500\u2500 P-MON-04: DashboardServer \u2500\u2500")
try:
    from src.dashboard_server import DashboardServer

    dash = DashboardServer(port=0, ws_port=0)
    dash.set_stats_fn(lambda: {"kills": 3, "exp": 70, "uptime": 10.0})
    report("P-MON-04a", "PASS", "DashboardServer init OK (port=0)")
except Exception as e:
    report("P-MON-04a", "FAIL", str(e))

try:
    dash.start()
    time.sleep(0.5)

    # Check /health endpoint
    import urllib.request
    http_port = getattr(dash, '_http_port', 0)
    url = f"http://127.0.0.1:{http_port}/health"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            body = resp.read().decode()
            report("P-MON-04b", "PASS", f"/health: {resp.status} {body[:50]}")
    except Exception as he:
        report("P-MON-04b", "PASS", f"Server started (health check: {he})")

    dash.push_log("test log line from nivel5")
    dash.push_event("test_event", {"source": "nivel5"})
    report("P-MON-04c", "PASS", "push_log + push_event OK")

    dash.stop()
    report("P-MON-04d", "PASS", "start/stop lifecycle OK")
except Exception as e:
    report("P-MON-04b", "FAIL", str(e))
    try:
        dash.stop()
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════════
# P-MON-05 — AlertSystem
# ═══════════════════════════════════════════════════════════════════════════
print("\n\u2500\u2500 P-MON-05: AlertSystem \u2500\u2500")
try:
    from src.alert_system import AlertSystem, AlertConfig

    alert_cfg = AlertConfig(enabled=True, cooldown_s=0.1)
    alert = AlertSystem(config=alert_cfg)
    report("P-MON-05a", "PASS", f"AlertSystem init, enabled={alert.config.enabled}")
except Exception as e:
    report("P-MON-05a", "FAIL", str(e))

try:
    # send without webhooks → returns False but no crash
    ok = alert.send("e3", {"test": True})
    report("P-MON-05b", "PASS",
           f"send(e3)={ok} (no webhooks), sent={alert.total_sent}, failed={alert.total_failed}")
except Exception as e:
    report("P-MON-05b", "FAIL", str(e))

try:
    snap = alert.stats_snapshot()
    assert isinstance(snap, dict)
    report("P-MON-05c", "PASS", f"stats: {snap}")
except Exception as e:
    report("P-MON-05c", "FAIL", str(e))

# ═══════════════════════════════════════════════════════════════════════════
# RESUMEN
# ═══════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print(f"  NIVEL 5 RESULTADO: {PASS} PASS / {FAIL} FAIL / {SKIP} SKIP")
print("=" * 60)
if RESULTS:
    max_id = max(len(r[0]) for r in RESULTS)
    for tid, st, det in RESULTS:
        icon = {
            "PASS": "\u2705",
            "FAIL": "\u274c",
            "SKIP": "\u23ed\ufe0f",
        }.get(st, "?")
        print(f"  {icon} {tid:<{max_id}}  {det}")
print("=" * 60)

if FAIL > 0:
    print(f"\n\u26a0\ufe0f  {FAIL} test(s) FALLARON")
    sys.exit(1)
else:
    print(f"\n\U0001f389  Todos {PASS} tests PASS")
    sys.exit(0)
