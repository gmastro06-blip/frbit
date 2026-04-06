from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import numpy as np

if TYPE_CHECKING:
    from .combat_manager import CombatManager


Detection = Tuple[int, int, float, str]


def read_hp_pct(manager: "CombatManager", frame: np.ndarray) -> Optional[int]:
    if manager._hp is None:
        return None
    try:
        hp, mp = manager._hp.read_bars(frame)
        hp_int = int(hp) if hp is not None else None
        with manager._lock:
            manager._cached_mp_pct = int(mp) if mp is not None else None
            manager._last_hp_pct = hp_int
        return hp_int
    except Exception as exc:
        manager._log(f"  [C] ⚠ HP read failed: {exc}")
        return None


def cast_spells(
    manager: "CombatManager",
    frame: np.ndarray,
    mob_count: int,
    *,
    time_module: Any,
    random_module: Any,
) -> None:
    """Cast configured spells when MP/cooldown conditions are met."""
    if not manager._cfg.spells:
        return

    with manager._lock:
        mp_pct: Optional[int] = manager._cached_mp_pct
    if mp_pct is None and manager._hp is not None:
        try:
            _, mp_pct = manager._hp.read_bars(frame)
            mp_pct = int(mp_pct) if mp_pct is not None else None
        except Exception as exc:
            manager._log(f"  [C] ⚠ MP fallback read failed: {exc}")

    use_aoe = mob_count >= manager._cfg.aoe_mob_threshold
    now = time_module.monotonic()

    if (now - manager._last_any_spell) < manager._GLOBAL_SPELL_CD:
        return

    for spell in manager._cfg.spells:
        vk = int(spell.get("vk", 0))
        min_mp = int(spell.get("min_mp", 0))
        cooldown = float(spell.get("cooldown", 1.5))
        label = spell.get("label", hex(vk))
        spell_type = spell.get("type", "")
        if vk == 0:
            continue
        if mp_pct is not None and mp_pct < min_mp:
            continue
        last_cast = manager._spell_cds.get(vk)
        if last_cast is not None and (now - last_cast) < cooldown:
            continue
        if spell_type and use_aoe and spell_type in ("single_target",):
            continue
        if spell_type and not use_aoe and spell_type in ("aoe", "taunt"):
            continue

        if manager._ctrl.press_key(vk):
            manager._spell_cds[vk] = now
            manager._last_any_spell = now + random_module.uniform(-0.125, 0.125)
            manager._log(f"  [C] ✨ {label} lanzado (MP={mp_pct}%)")
            manager._emit("e5", {"vk": vk, "label": label, "mp_pct": mp_pct})
            break

        manager._log(f"  [C] ⚠ press_key({vk}) failed for {label}")
        break


def sort_by_priority(
    manager: "CombatManager",
    detections: List[Detection],
) -> List[Detection]:
    priority = manager._cfg.monster_priority
    if not priority:
        return detections

    priority_map: Dict[str, int] = {
        name.lower(): index for index, name in enumerate(priority)
    }
    fallback = len(priority)

    def sort_key(detection: Detection) -> Tuple[int, int]:
        name_lower = detection[3].lower()
        normalized_name = name_lower.replace("_", " ")
        index = priority_map.get(name_lower, priority_map.get(normalized_name, fallback))
        return (index, detection[1])

    return sorted(detections, key=sort_key)


def check_anti_lure(manager: "CombatManager", detection_count: int) -> bool:
    max_mobs = manager._cfg.max_expected_mobs
    if max_mobs <= 0 or detection_count <= max_mobs:
        return False

    manager._lure_warnings += 1
    manager._log(
        f"  [C] ⚠ ANTI-LURE: {detection_count} mobs > "
        f"max_expected={max_mobs} (warning #{manager._lure_warnings})"
    )
    manager._emit(
        "e25",
        {
            "mob_count": detection_count,
            "max_expected": max_mobs,
            "action": manager._cfg.lure_action,
        },
    )
    return True