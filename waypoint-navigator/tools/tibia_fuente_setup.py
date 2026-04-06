#!/usr/bin/env python3
"""
tibia_fuente_setup.py
--------------------
🔥 CONFIGURACIÓN ESPECÍFICA: Proyector en ventana (Fuente) - Tibia_Fuente
⚠️  CRÍTICO: Bot DEBE capturar SOLO de este proyector específico.
"""

def main() -> None:
    print("🔥 CONFIGURACIÓN CRÍTICA - TIBIA_FUENTE ESPECÍFICA")
    print("=" * 60)

    print("⚠️  INFORMACIÓN CRÍTICA A GRABAR A FUEGO:")
    print("  📺 Source: 'Tibia_Fuente' en OBS")
    print("  🖥️  Projector: 'Proyector en ventana (Fuente) - Tibia_Fuente'")
    print("  🎯 Bot DEBE capturar SOLO de este projector específico")
    print("  ❌ NO del monitor principal")
    print("  ❌ NO de otros projectors")
    print("  ❌ NO de otras fuentes OBS")

    print(f"\n🎮 SETUP CORRECTO:")

    print(f"\n1️⃣  VERIFICAR OBS SOURCES:")
    print(f"   • Open OBS Studio")
    print(f"   • Scene debe contener: 'Tibia_Fuente' source")
    print(f"   • Tibia_Fuente = Window Capture de Tibia game")
    print(f"   • Verificar que Tibia_Fuente muestra el juego activo")

    print(f"\n2️⃣  CONFIGURAR PROJECTOR ESPECÍFICO:")
    print(f"   • Right-click en 'Tibia_Fuente' source")
    print(f"   • Select: 'Proyector en ventana (Fuente)'")
    print(f"   • Se abre: 'Proyector en ventana (Fuente) - Tibia_Fuente'")
    print(f"   • Mover este projector a Monitor 2 (1920x1080)")
    print(f"   • ⚠️  ESTE es el projector que captura el bot")

    print(f"\n3️⃣  VERIFICAR CONFIGURACIÓN:")
    print(f"   • Window title debe ser: 'Proyector en ventana (Fuente) - Tibia_Fuente'")
    print(f"   • Projector muestra SOLO el contenido de Tibia")
    print(f"   • NO muestra OBS UI, outros sources, etc.")
    print(f"   • Resolution: 1920x1080")
    print(f"   • Position: Monitor 2")

    print(f"\n🔧 FRAME CAPTURE CONFIGURATION:")
    print(f"   Bot captura usando MSS de Monitor 2")
    print(f"   Monitor 2 debe mostrar: 'Proyector en ventana (Fuente) - Tibia_Fuente'")
    print(f"   Coordinate mapping: 1:1 con Tibia game coordinates")

    print(f"\n🚨 PROBLEMAS COMUNES:")
    print(f"   ❌ Proyector de Scene preview (muestra todo)")
    print(f"   ❌ Proyector de otra fuente")
    print(f"   ❌ Projector en monitor incorrecto")
    print(f"   ❌ Tibia_Fuente no está capturing Tibia")
    print(f"   ❌ Multiple projectors confundir el setup")

    print(f"\n✅ CONFIGURACIÓN CORRECTA:")
    print(f"   ✅ OBS Source: 'Tibia_Fuente' (Window Capture de Tibia)")
    print(f"   ✅ Projector: 'Proyector en ventana (Fuente) - Tibia_Fuente'")
    print(f"   ✅ Location: Monitor 2, 1920x1080")
    print(f"   ✅ Content: SOLO Tibia game, character logged in")
    print(f"   ✅ Bot capture: MSS de Monitor 2 → Tibia_Fuente projector")

    print(f"\n🧪 VERIFICATION STEPS:")
    print(f"   1. Verify 'Tibia_Fuente' source exists in OBS")
    print(f"   2. Verify 'Proyector en ventana (Fuente) - Tibia_Fuente' en Monitor 2")
    print(f"   3. Test: python single_detection_test.py")
    print(f"   4. Check: debug_bot_view.png shows TIBIA (not LinkedIn)")
    print(f"   5. Test: python improved_player_status.py")
    print(f"   6. Expected: Character position, HP%, MP% detected")

    print(f"\n📝 DOCUMENTATION:")
    print(f"   • Bot setup REQUIRES specific OBS configuration")
    print(f"   • Source name: 'Tibia_Fuente'")
    print(f"   • Projector: Source-specific projector (NOT scene projector)")
    print(f"   • Monitor: 2 (secondary, 1920x1080)")
    print(f"   • Content: Pure Tibia game output")

    print(f"\n🎯 REMEMBER:")
    print(f"   🔥 ALWAYS capture from 'Tibia_Fuente' source projector")
    print(f"   🔥 NEVER capture from scene preview")
    print(f"   🔥 NEVER capture from other sources")
    print(f"   🔥 VERIFY projector window title contains 'Tibia_Fuente'")

if __name__ == "__main__":
    main()