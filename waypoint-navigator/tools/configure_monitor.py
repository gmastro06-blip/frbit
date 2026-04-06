#!/usr/bin/env python3
"""
configure_monitor.py
--------------------
Configure specific monitor for bot frame capture.
"""

import sys
import json
from pathlib import Path

def main() -> int:
    print("⚙️  CONFIGURE MONITOR FOR BOT CAPTURE")
    print("=" * 50)

    # Test available monitors
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        import mss
    except Exception as e:
        print(f"❌ MSS import failed: {e}")
        return 1

    with mss.mss() as sct:
        monitors = sct.monitors

        print(f"🖥️  Available monitors:")
        for i, monitor in enumerate(monitors):
            if i == 0:
                print(f"  {i}: All screens - {monitor['width']}x{monitor['height']}")
            else:
                print(f"  {i}: Monitor {i} - {monitor['width']}x{monitor['height']} at ({monitor['left']}, {monitor['top']})")

    print(f"\n📋 Current bot capture:")
    print(f"   • Uses Monitor 1 (primary) by default")
    print(f"   • Currently capturing LinkedIn (wrong!)")
    print(f"   • Should capture Tibia")

    print(f"\n🎯 TO FIX THIS:")

    print(f"\n1️⃣  QUICK FIX - Test each monitor:")
    print(f"   python test_monitor_capture.py")
    print(f"   # Check which monitor shows Tibia")

    print(f"\n2️⃣  CONFIGURE SPECIFIC MONITOR:")
    print(f"   # Edit frame_capture.py to use specific monitor")
    print(f"   # Or move OBS projector to primary monitor")

    print(f"\n🔧 IMMEDIATE SOLUTION:")
    print(f"   1. Run: python test_monitor_capture.py")
    print(f"   2. Check captures/monitor_*.png files")
    print(f"   3. Find Tibia in one of them")
    print(f"   4. Either:")
    print(f"      • Move OBS projector to Monitor 1 (simplest)")
    print(f"      • Configure bot for correct monitor")

    print(f"\n💡 SIMPLEST FIX:")
    print(f"   1. Close OBS projector")
    print(f"   2. Right-click OBS preview → 'Windowed Projector'")
    print(f"   3. Drag projector to PRIMARY monitor (where LinkedIn is now)")
    print(f"   4. Resize projector to 1920x1080")
    print(f"   5. Test: python improved_player_status.py")

    print(f"\n✅ AFTER FIX:")
    print(f"   Bot will capture Tibia instead of LinkedIn")
    print(f"   Position detection will work")
    print(f"   HP/MP detection will work")
    print(f"   All systems operational! 🚀")
    return 0

if __name__ == "__main__":
    main()