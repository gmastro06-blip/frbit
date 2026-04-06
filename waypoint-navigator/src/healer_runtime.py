from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any, Callable, Optional, Set, Tuple

import numpy as np

if TYPE_CHECKING:
    from .healer import AutoHealer


def run_loop(
    healer: "AutoHealer",
    *,
    jittered_sleep_fn: Callable[[float], Any],
) -> None:
    while healer._running:
        if not healer._paused:
            try:
                tick(
                    healer,
                    time_module=healer._runtime_time,
                    random_module=healer._runtime_random,
                    jittered_sleep_fn=jittered_sleep_fn,
                )
            except Exception as exc:
                healer._log(f"  [H] ⚠ Error en loop: {exc}")
        jittered_sleep_fn(healer._cfg.check_interval)


def tick(
    healer: "AutoHealer",
    *,
    time_module: Any,
    random_module: Any,
    jittered_sleep_fn: Callable[[float], Any],
) -> None:
    hp, mp = read_from_frame(healer, time_module=time_module)

    if hp <= 0 and mp <= 0:
        with healer._lock:
            healer._hp_pct = hp
            healer._mp_pct = mp
        return

    if hp <= 0 and mp > 0:
        healer._zero_hp_streak += 1
        if healer._zero_hp_streak >= 3:
            healer._log("  [H] ⚠ HP=0 for 3+ ticks with MP>0 — detector likely broken, skipping")
            return
    else:
        healer._zero_hp_streak = 0

    with healer._lock:
        healer._hp_pct = hp
        healer._mp_pct = mp

    now = time_module.monotonic()
    jitter = healer._cfg.cooldown_jitter

    with healer._lock:
        do_emergency = (
            healer._cfg.emergency_hotkey_vk
            and hp <= healer._cfg.hp_emergency_pct
            and mp >= healer._cfg.heal_min_mp_pct
            and (now - healer._last_emergency)
            >= healer._cfg.emergency_cooldown
            * random_module.uniform(1.0 - jitter, 1.0 + jitter)
        )
        if do_emergency:
            healer._last_emergency = now

        do_heal = (
            not do_emergency
            and healer._cfg.heal_hotkey_vk
            and hp <= healer._cfg.hp_threshold_pct
            and mp >= healer._cfg.heal_min_mp_pct
            and (now - healer._last_heal)
            >= healer._cfg.heal_cooldown
            * random_module.uniform(1.0 - jitter, 1.0 + jitter)
        )
        if do_heal:
            healer._last_heal = now

        do_mana = (
            healer._cfg.mana_hotkey_vk
            and mp < healer._cfg.mp_threshold_pct
            and (now - healer._last_mana)
            >= healer._cfg.mana_cooldown
            * random_module.uniform(1.0 - jitter, 1.0 + jitter)
        )
        if do_mana:
            healer._last_mana = now

        do_utamo = (
            healer._cfg.utamo_hotkey_vk
            and mp >= healer._cfg.utamo_min_mp_pct
            and (now - healer._last_utamo) >= healer._cfg.utamo_cooldown
        )
        if do_utamo:
            healer._last_utamo = now

        do_haste = (
            healer._cfg.haste_hotkey_vk
            and (now - healer._last_haste) >= healer._cfg.haste_cooldown
        )
        if do_haste:
            healer._last_haste = now

    if do_emergency:
        jittered_sleep_fn(random_module.uniform(0.20, 0.50))
        healer._log(
            f"  [H] ⚠ EMERGENCIA HP={hp:.0f}% — VK=0x{healer._cfg.emergency_hotkey_vk:x}"
        )
        healer._ctrl.press_key(healer._cfg.emergency_hotkey_vk)
        with healer._lock:
            healer._emergency_uses += 1
        if healer.on_emergency:
            healer.on_emergency()
        try_verify_heal(healer, hp, verify_hp_changed_fn=healer._runtime_verify_hp_changed)
    elif do_heal:
        jittered_sleep_fn(random_module.uniform(0.15, 0.40))
        healer._log(f"  [H] HP={hp:.0f}% < {healer._cfg.hp_threshold_pct}% → curar")
        healer._ctrl.press_key(healer._cfg.heal_hotkey_vk)
        with healer._lock:
            healer._heals_done += 1
        if healer.on_heal:
            healer.on_heal()
        try_verify_heal(healer, hp, verify_hp_changed_fn=healer._runtime_verify_hp_changed)

    if do_mana:
        jittered_sleep_fn(random_module.uniform(0.10, 0.30))
        healer._log(f"  [H] MP={mp:.0f}% < {healer._cfg.mp_threshold_pct}% → maná")
        healer._ctrl.press_key(healer._cfg.mana_hotkey_vk)
        with healer._lock:
            healer._mana_uses += 1
        if healer.on_mana:
            healer.on_mana()
        try_verify_mana(healer, mp, verify_mp_changed_fn=healer._runtime_verify_mp_changed)

    if do_utamo:
        healer._log(
            f"  [H] MP={mp:.0f}% ≥ {healer._cfg.utamo_min_mp_pct}% → utamo vita"
        )
        healer._ctrl.press_key(healer._cfg.utamo_hotkey_vk)
        with healer._lock:
            healer._utamo_casts += 1

    if do_haste:
        _handle_haste(healer, now=now)


def try_verify_heal(
    healer: "AutoHealer",
    old_hp: float,
    *,
    verify_hp_changed_fn: Any,
) -> None:
    if not healer._verify_heals or verify_hp_changed_fn is None or healer._frame_getter is None:
        return
    try:
        ok = verify_hp_changed_fn(
            healer._detector,
            int(old_hp),
            healer._frame_getter,
            direction="up",
            timeout=1.5,
        )
        if not ok:
            with healer._lock:
                healer._heal_verify_fails += 1
                fails = healer._heal_verify_fails
            healer._log(f"  [H] ⚠ verify: HP did not increase after heal (fails={fails})")
    except Exception as exc:
        healer._log(f"  [H] ⚠ verify HP failed: {exc}")


def try_verify_mana(
    healer: "AutoHealer",
    old_mp: float,
    *,
    verify_mp_changed_fn: Any,
) -> None:
    if not healer._verify_heals or verify_mp_changed_fn is None or healer._frame_getter is None:
        return
    try:
        ok = verify_mp_changed_fn(
            healer._detector,
            int(old_mp),
            healer._frame_getter,
            direction="up",
            timeout=1.5,
        )
        if not ok:
            with healer._lock:
                healer._mana_verify_fails += 1
                fails = healer._mana_verify_fails
            healer._log(f"  [H] ⚠ verify: MP did not increase after mana pot (fails={fails})")
    except Exception as exc:
        healer._log(f"  [H] ⚠ verify MP failed: {exc}")


def get_conditions(healer: "AutoHealer") -> Set[str]:
    if healer._conditions_getter is None:
        return set()
    try:
        result = healer._conditions_getter()
        return result if isinstance(result, set) else set(result)
    except Exception as exc:
        healer._log(f"  [H] ⚠ conditions read failed: {exc}")
        return set()


def read_from_frame(
    healer: "AutoHealer",
    *,
    time_module: Any,
) -> Tuple[float, float]:
    if healer._frame_getter is None or healer._detector is None:
        if not healer._no_frame_getter_warned:
            healer._no_frame_getter_warned = True
            healer._log(
                "[H] ⚠ HEALER SORDO — frame_getter no configurado. El healer nunca curará hasta que se registre un frame source."
            )
        with healer._lock:
            return healer._hp_pct, healer._mp_pct

    try:
        frame = healer._frame_getter()
        if frame is None:
            with healer._lock:
                return healer._hp_pct, healer._mp_pct
        hp_val, mp_val = healer._detector.read_bars(frame)
        if hp_val is None and mp_val is None:
            with healer._lock:
                healer._read_fail_count += 1
                return healer._hp_pct, healer._mp_pct

        hp = float(hp_val) if hp_val is not None else healer._hp_pct
        mp = float(mp_val) if mp_val is not None else healer._mp_pct
        if not math.isfinite(hp):
            hp = healer._hp_pct
        if not math.isfinite(mp):
            mp = healer._mp_pct
        hp = max(0.0, min(100.0, hp))
        mp = max(0.0, min(100.0, mp))
        with healer._lock:
            healer._read_fail_count = 0
            _was_blind = healer._blind_alarm_fired
            if _was_blind:
                healer._blind_since = 0.0
                healer._blind_alarm_fired = False
        if _was_blind:
            healer._log("[H] \u2713 HP/MP detector recovered")
            if healer._event_bus is not None:
                try:
                    healer._event_bus.emit("healer_recovered", {})
                except Exception:
                    pass
        return hp, mp
    except Exception as exc:
        with healer._lock:
            healer._read_fail_count += 1
            _fail_count = healer._read_fail_count
            _blind_since = healer._blind_since
            _blind_alarm_fired = healer._blind_alarm_fired
        if _fail_count <= 3 or _fail_count % 50 == 0:
            logging.getLogger("wn.hl").warning(
                "[H] _read_from_frame failed (%d consecutive): %s",
                _fail_count,
                exc,
            )
        if _fail_count >= 25:
            now = time_module.monotonic()
            if _blind_since == 0.0:
                with healer._lock:
                    healer._blind_since = now
            elif not _blind_alarm_fired and (now - _blind_since) >= healer._blind_alarm_s:
                with healer._lock:
                    healer._blind_alarm_fired = True
                healer._log(
                    f"[H] \u26a0 HEALER BLIND \u2014 HP/MP unreadable for {now - _blind_since:.0f}s. Check ROI calibration."
                )
                if healer._event_bus is not None:
                    try:
                        healer._event_bus.emit(
                            "healer_blind",
                            {
                                "seconds": round(now - _blind_since, 1),
                                "fail_count": _fail_count,
                            },
                        )
                    except Exception:
                        pass
            return 0.0, 0.0
        with healer._lock:
            return healer._hp_pct, healer._mp_pct


def _handle_haste(healer: "AutoHealer", *, now: float) -> None:
    if healer._conditions_getter is not None:
        conditions = get_conditions(healer)
        if "haste" not in conditions and "battle" not in conditions:
            healer._log(f"  [H] Haste buff ausente → VK=0x{healer._cfg.haste_hotkey_vk:x}")
            healer._ctrl.press_key(healer._cfg.haste_hotkey_vk)
            with healer._lock:
                healer._last_haste = now
                healer._haste_casts += 1
            return
        with healer._lock:
            healer._last_haste = now - healer._cfg.haste_cooldown + 2.0
        return

    with healer._lock:
        healer._last_haste = now
    if not healer._no_conditions_warned:
        healer._no_conditions_warned = True
        healer._log(
            "  [H] ⚠ Haste configurado pero conditions_getter no registrado — Auto Hur deshabilitado. Registra set_conditions_getter() para activarlo."
        )