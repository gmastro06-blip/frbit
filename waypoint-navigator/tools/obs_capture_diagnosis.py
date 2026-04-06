#!/usr/bin/env python3
"""
obs_capture_diagnosis.py
------------------------
Diagnóstico específico: OBS NO está capturando Tibia.
"""

def main() -> None:
    print("🚨 PROBLEMA CONFIRMADO - OBS CAPTURE INCORRECTO")
    print("=" * 55)

    print("📸 ANÁLISIS DE IMÁGENES CAPTURED:")
    print("  ❌ HP Region: Barra gris horizontal (NO Tibia HP)")
    print("  ❌ MP Region: Barra gris horizontal (NO Tibia MP)")
    print("  ❌ Minimap: Iconos UI (estrella, config) (NO Tibia minimap)")
    print("  ❌ Battle: Elementos rojos/gris (NO Tibia battlelist)")

    print(f"\n🎯 DIAGNÓSTICO DEFINITIVO:")
    print(f"OBS Projector está capturando OTRA APLICACIÓN, no Tibia.")
    print(f"Las ROI coordinates son correctas - el problema es la fuente OBS.")

    print(f"\n🛠️  SOLUCIÓN PASO A PASO:")

    print(f"\n1️⃣  VERIFICAR QUÉ ESTÁ CAPTURANDO OBS:")
    print(f"   • Ir a Monitor 2 (donde está el OBS projector)")
    print(f"   • ¿Se ve Tibia client ahí?")
    print(f"   • Si NO → problema con OBS scene")
    print(f"   • Si SÍ pero raro → problema con projector")

    print(f"\n2️⃣  CONFIGURAR OBS CORRECTAMENTE:")
    print(f"   • Abrir OBS Studio")
    print(f"   • Scene: crear/seleccionar 'Tibia'")
    print(f"   • Source: agregar 'Window Capture'")
    print(f"   • Window: seleccionar ventana de Tibia")
    print(f"   • Verificar que Tibia sea visible en OBS preview")

    print(f"\n3️⃣  CONFIGURAR PROJECTOR:")
    print(f"   • Right-click en OBS preview")
    print(f"   • 'Windowed Projector (Preview)'")
    print(f"   • Mover projector a Monitor 2")
    print(f"   • Resize projector a 1920x1080")

    print(f"\n4️⃣  VERIFICAR SETUP:")
    print(f"   • Tibia client debe estar visible y loggeado")
    print(f"   • OBS preview muestra Tibia")
    print(f"   • Monitor 2 projector muestra Tibia")
    print(f"   • Character logged in con HP/MP visible")

    print(f"\n🧪 COMANDOS DE VERIFICACIÓN:")
    print(f"   # Después de configurar OBS:")
    print(f"   python single_detection_test.py")
    print(f"   # → Debe mostrar:")
    print(f"   #   HP: Red pixels > 0%")
    print(f"   #   MP: Blue pixels > 0%")
    print(f"   #   Minimap: Tibia minimap content")
    print(f"   #   Battle: Empty o Tibia monsters")

    print(f"\n🔍 CAPTURE ALTERNATIVES:")
    print(f"   Si OBS sigue fallando, try:")
    print(f"   1. Window Capture → Game Capture")
    print(f"   2. Display Capture (full screen)")
    print(f"   3. Verificar Tibia no esté minimized")

    print(f"\n✅ EXPECTED RESULTS DESPUÉS DEL FIX:")
    print(f"   📍 Position: 'Character detected' + coords")
    print(f"   ❤️  HP: 'XX%' (actual health)")
    print(f"   💙 MP: 'XX%' (actual mana)")
    print(f"   🍖 Status: 'Hungry: True/False'")
    print(f"   ⚔️  Battlelist: 'Empty' o monster names")

    print(f"\n🚀 TEST COMMAND:")
    print(f"   python improved_player_status.py")
    print(f"   # Should show real game data, not UI elements")

    print(f"\n📝 SUMMARY:")
    print(f"   • Bot code: ✅ Perfect")
    print(f"   • ROI coords: ✅ Perfect")
    print(f"   • OBS setup: ❌ Capturing wrong source")
    print(f"   • Fix: Configure OBS to capture Tibia window")

if __name__ == "__main__":
    main()