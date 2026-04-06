#!/usr/bin/env python3
"""
Quick test of bot detection after monitor fix
"""

import sys
import cv2
import numpy as np
from pathlib import Path

def test_bot_detection() -> bool:
    """Test if bot detection works now with Monitor 2"""
    print("Testing bot detection after monitor configuration fix...")
    print("=" * 55)

    # Initialize bot modules
    sys.path.insert(0, str(Path(__file__).parent / "src"))

    try:
        from frame_capture import build_frame_getter
        from frame_cache import FrameCache
        from position_resolver import PositionResolver
        from hpmp_detector import HpMpDetector
        from combat_manager import CombatManager
        from condition_monitor import ConditionMonitor

        # Test frame capture with Monitor 2
        print("1. Testing frame capture (Monitor 2)...")
        frame_getter = build_frame_getter("mss", monitor_idx=2)
        frame_cache = FrameCache(frame_getter, ttl_ms=100)

        frame = frame_cache.latest()
        if frame is None:
            print("   ERROR: No frame captured")
            return False

        cv2.imwrite("captures/test_detection_frame.png", frame)
        print(f"   Frame captured: {frame.shape[1]}x{frame.shape[0]}")
        print(f"   Saved: captures/test_detection_frame.png")

        # Test position detection
        print("\n2. Testing position detection...")
        pos_resolver = PositionResolver()
        position = pos_resolver.resolve(frame)

        if position and position.x and position.y:
            print(f"   Position detected: {position}")
        else:
            print(f"   Position: {position or 'Unknown'}")

        # Test HP/MP detection
        print("\n3. Testing HP/MP detection...")
        hpmp = HpMpDetector()
        hp_pct, mp_pct = hpmp.get_hp_mp_percentages(frame)

        if hp_pct > 0:
            print(f"   HP: {hp_pct}%")
        else:
            print(f"   HP: Not detected")

        if mp_pct > 0:
            print(f"   MP: {mp_pct}%")
        else:
            print(f"   MP: Not detected")

        # Test combat/battle list
        print("\n4. Testing battle list detection...")
        try:
            combat = CombatManager()
            combat.update(frame)

            if combat.active_targets:
                print(f"   Battle list: {len(combat.active_targets)} targets")
                for target in combat.active_targets[:3]:  # Show first 3
                    print(f"     - {target}")
            else:
                print("   Battle list: Empty")
        except Exception as e:
            print(f"   Battle list: Error - {e}")

        # Test conditions/status
        print("\n5. Testing status conditions...")
        try:
            condition_monitor = ConditionMonitor()
            conditions = condition_monitor.get_conditions(frame)

            if conditions:
                print(f"   Status conditions: {', '.join(conditions)}")
            else:
                print("   Status conditions: None detected")
        except Exception as e:
            print(f"   Status conditions: Error - {e}")

        # Summary
        print("\n" + "=" * 55)
        print("DETECTION SUMMARY:")

        detection_working = (
            position is not None and
            position.x and position.y and
            (hp_pct > 0 or mp_pct > 0)
        )

        if detection_working:
            print("SUCCESS: Bot detection is working!")
            print(f"  Position: {position}")
            print(f"  HP: {hp_pct}% | MP: {mp_pct}%")
        else:
            print("ISSUE: Bot detection still has problems")
            print("  Check captures/test_detection_frame.png")

        return detection_working

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_bot_detection()
    print(f"\nResult: {'PASS' if success else 'FAIL'}")