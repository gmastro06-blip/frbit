#!/usr/bin/env python3
"""
Console verification of player status detection
"""

import sys
import time
from pathlib import Path

def main() -> int:
    """Test all player detection systems"""
    print("VERIFICACION CONSOLE - STATUS DEL JUGADOR")
    print("=" * 55)

    # Setup path
    sys.path.insert(0, str(Path(__file__).parent / "src"))

    try:
        from frame_capture import build_frame_getter
        from frame_cache import FrameCache
        from minimap_radar import MinimapRadar
        from position_resolver import PositionResolver
        from hpmp_detector import HpMpDetector
        from condition_monitor import ConditionMonitor
        from combat_manager import CombatManager

        print("1. Initializing detection systems...")
        position = None
        hp_pct = 0
        mp_pct = 0

        # Frame capture and cache
        frame_getter = build_frame_getter("mss")  # Now defaults to monitor_idx=2
        frame_cache = FrameCache(frame_getter, ttl_ms=100)

        # Detection modules
        radar = MinimapRadar()
        pos_resolver = PositionResolver()
        hpmp_detector = HpMpDetector()
        condition_monitor = ConditionMonitor()
        combat_manager = CombatManager()

        print("2. Capturing frame...")
        frame = frame_cache.latest()
        if frame is None:
            print("ERROR: No frame captured!")
            return 1

        print(f"   Frame size: {frame.shape[1]}x{frame.shape[0]}")

        # Position detection
        print("\n3. Position Detection:")
        try:
            position = pos_resolver.resolve(frame)
            if position and position.x and position.y:
                print(f"   Character position: {position}")
                print(f"   Coordinates: ({position.x}, {position.y}, Floor {position.floor})")
            else:
                print("   No character detected")
        except Exception as e:
            print(f"   Position error: {e}")

        # HP/MP Detection
        print("\n4. HP/MP Detection:")
        try:
            hp_pct, mp_pct = hpmp_detector.get_hp_mp_percentages(frame)
            if hp_pct > 0:
                print(f"   HP: {hp_pct}%")
            else:
                print("   HP: No detection")

            if mp_pct > 0:
                print(f"   MP: {mp_pct}%")
            else:
                print("   MP: No detection")
        except Exception as e:
            print(f"   HP/MP error: {e}")

        # Status conditions
        print("\n5. Status Conditions:")
        try:
            conditions = condition_monitor.get_conditions(frame)
            if conditions:
                print(f"   Active conditions: {', '.join(conditions)}")
            else:
                print("   No status conditions detected")
        except Exception as e:
            print(f"   Conditions error: {e}")

        # Combat/Battle List
        print("\n6. Battle List:")
        try:
            combat_manager.update(frame)
            if combat_manager.active_targets:
                print(f"   Active targets: {len(combat_manager.active_targets)}")
                for i, target in enumerate(combat_manager.active_targets[:3]):
                    print(f"     {i+1}. {target}")
            else:
                print("   Battle list: Empty")
        except Exception as e:
            print(f"   Combat error: {e}")

        print("\n" + "=" * 55)
        print("SUMMARY:")

        # Check if basic detection is working
        working = (
            frame is not None and
            (position is not None and position.x and position.y) or
            (hp_pct > 0 or mp_pct > 0)
        )

        if working:
            print("SUCCESS: Bot detection systems are working!")
        else:
            print("ISSUE: Detection systems may need calibration")

        return 0 if working else 1

    except Exception as e:
        print(f"FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    exit(main())