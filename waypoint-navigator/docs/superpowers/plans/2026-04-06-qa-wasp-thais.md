# QA Plan — wasp_thais EK NoPvP (Live Field Test)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Validar el bot end-to-end en el spawn wasp_thais con personaje EK en servidor NoPvP usando las rutas `wasp_thais_ek_nopvp.json` (mouse) y `wasp_thais_ek_nopvp_live.json` (interception).

**Architecture:** Ejecución por fases bloqueantes. Ninguna fase comienza si la anterior tiene FAIL. Cada test tiene ID trazable, resultado explícito y acción correctiva. Las fases 0-1 son offline; las fases 2-5 requieren Tibia + personaje en juego.

**Tech Stack:** Python 3.12+, pytest 9.x, Interception kernel driver, OBS/MSS frame capture, Tibia client 1920×1080

---

## Estado de las rutas (verificado 2026-04-06)

| Archivo | Walkability | A* completo | Listo |
|---------|-------------|-------------|-------|
| `wasp_thais_ek_nopvp.json` | ✅ OK | ✅ OK (2 WARN aceptados) | ✅ |
| `wasp_thais_ek_nopvp_live.json` | ✅ OK | ✅ OK (2 WARN aceptados) | ✅ |

**WARN aceptados** (no bloquean): segmentos largos [16] y [67] de 27 y 22 tiles — A* los resuelve, son rutas de retorno del spawn hacia el depot.

**Templates de monstruos disponibles:** `wasp.png`, `wolf.png` presentes en `cache/templates/monsters/`.

---

## Fase 0 — Pre-flight (sin Tibia)

> Ejecutar antes de abrir el juego. Tarda ~2 min.

### Task 0.1: Validar suite offline

**Files:**
- Run: `tests/` (no modificar)

- [ ] **Ejecutar suite completa**
```bash
python -m pytest tests/ -q --tb=short
```
Esperado: `6096 passed, 13 skipped` (0 failed). Si hay fallos → detener y corregir antes de continuar.

- [ ] **Verificar rutas con A***
```bash
python -c "
import sys; sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from tools.route_validator import validate_path
for r in ['routes/wasp_thais/wasp_thais_ek_nopvp.json',
          'routes/wasp_thais/wasp_thais_ek_nopvp_live.json']:
    validate_path(Path(r), run_astar=True)
"
```
Esperado: `OK` en ambas. Si FAIL → no continuar.

- [ ] **Verificar templates de monstruos del spawn**
```python
from pathlib import Path
needed = ['wasp', 'wolf', 'starving_wolf']
missing = [m for m in needed if not Path(f'cache/templates/monsters/{m}.png').exists()]
print('Faltantes:', missing or 'ninguno')
```
Esperado: `Faltantes: ninguno`. Si falta alguno → `python tools/download_templates.py` o capturar manualmente.

- [ ] **Validar configs JSON**
```bash
python -c "
from src.combat_manager import CombatConfig
from src.healer import HealConfig, HealCfg
import json, pathlib
json.loads(pathlib.Path('heal_config.json').read_text())
json.loads(pathlib.Path('combat_config.json').read_text())
json.loads(pathlib.Path('hpmp_config.json').read_text())
print('configs OK')
"
```
Esperado: `configs OK`.

- [ ] **Commit estado actual**
```bash
git add routes/wasp_thais/
git commit -m "fix(routes): add cave interior walkable_override z=8 for A* validation"
git push origin main
```

---

## Fase 1 — Entorno live (Tibia abierto, personaje parado)

> Personaje en Thais depot, z=8. No hay monstruos cerca.

### Task 1.1: Frame capture

**Files:** `src/frame_capture.py`, `tools/debug_capture.py`

- [ ] **Verificar captura de frames**
```bash
python tools/debug_capture.py
```
Esperado: archivo guardado en `output/frame_NNNN.png`, resolución 1920×1080, sin frame negro.
Si falla: cambiar `frame_source` en config (obs → mss → dxcam).

- [ ] **QA-F0-003 · Verificar que el frame no es negro**
```python
import cv2, numpy as np
frame = cv2.imread('output/frame_0001.png')
mean = frame.mean()
print(f'Media píxeles: {mean:.1f}')  # Esperado: > 30
assert mean > 30, 'Frame negro — revisar fuente de captura'
print('OK')
```

### Task 1.2: Minimap y posición

**Files:** `src/minimap_radar.py`, `src/minimap_calibrator.py`

- [ ] **QA-F2-002 · Calibrar minimap**
```bash
python main.py calibrate
```
Acción: seguir el wizard hasta que `minimap_config.json` tenga `tiles_wide` entre 100-120. Verificar:
```bash
python -c "import json; c=json.load(open('minimap_config.json')); print('tiles_wide:', c.get('tiles_wide','no-set'))"
```
Esperado: `tiles_wide: 109` (o valor entre 100-120).

- [ ] **QA-F2-003 · Leer posición desde minimap**
```python
import cv2, numpy as np
from src.minimap_radar import MinimapRadar
from src.map_loader import TibiaMapLoader

loader = TibiaMapLoader()
radar = MinimapRadar(loader)

# Tomar frame real
import mss
with mss.mss() as sct:
    img = np.array(sct.grab(sct.monitors[1]))
    frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

pos = radar.read(frame)
print(f'Posición: {pos}')
assert pos is not None, 'Radar no detectó posición'
assert 32000 < pos.x < 33000, f'X fuera de rango Thais: {pos.x}'
assert 32000 < pos.y < 33500, f'Y fuera de rango Thais: {pos.y}'
print('OK')
```
Esperado: coordenada válida cerca de (32349, 32225, z=8).

### Task 1.3: HP/MP y Healer

**Files:** `src/hpmp_detector.py`, `src/healer.py`

- [ ] **QA-F0-006 · Verificar ROIs de HP/MP**
```bash
python tools/calibrate_viewport.py
```
Ajustar hasta que las barras resaltadas coincidan con las barras en pantalla. Luego:
```python
from src.hpmp_detector import HpMpDetector, HpMpConfig
import mss, cv2, numpy as np

cfg = HpMpConfig.load()
det = HpMpDetector(cfg)
with mss.mss() as sct:
    img = np.array(sct.grab(sct.monitors[1]))
    frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

hp, mp = det.read(frame)
print(f'HP={hp}% MP={mp}%')
assert hp is not None and 0 < hp <= 100, f'HP inválido: {hp}'
assert mp is not None and 0 < mp <= 100, f'MP inválido: {mp}'
print('OK')
```
Esperado: HP entre 95-100% con personaje sano.

- [ ] **QA-F0-007 · Verificar ROI de Battle List**
```bash
python tools/debug_all_rois.py
```
Verificar visualmente en `output/` que el recuadro battle_list_roi enmarca el panel izquierdo de Tibia.

### Task 1.4: Input controller

**Files:** `src/input_controller.py`

- [ ] **QA-F2-005 · Test de teclas (solo en ventana Tibia enfocada)**
```python
import time
from src.input_controller import InputController

ctrl = InputController(window_title="Tibia")
# Test: presionar Escape (no debería hacer nada dañino)
ctrl.press_key(0x1B)  # VK_ESCAPE
time.sleep(0.5)
print('Keypress OK')

# Test concurrencia
import threading
errors = []
def click_test(tid):
    for _ in range(10):
        try:
            ctrl.move_mouse(500, 400)
        except RuntimeError as e:
            errors.append(str(e))

threads = [threading.Thread(target=click_test, args=(i,)) for i in range(5)]
for t in threads: t.start()
for t in threads: t.join()
print(f'Errores concurrencia: {len(errors)}')  # Esperado: 0
assert not errors, f'Errores: {errors}'
print('OK')
```

---

## Fase 2 — Subsistemas en vivo (componente a componente)

> Personaje en zona segura (depot o temple). Un test a la vez.

### Task 2.1: Navegación point-to-point

**Files:** `src/navigator.py`, `src/pathfinder.py`

- [ ] **QA-F3-001 · Navegar de depot a start coord de ruta**

Personaje en (32349, 32225, z=8):
```bash
python main.py navigate --sx 32349 --sy 32225 --ex 32345 --ey 32221 --floor 7
```
Observar: el personaje camina de z=8 a z=7 (baja por escalera). Criterios PASS:
- El personaje llega al destino (±3 tiles)
- No queda atascado
- Tiempo < 30 segundos

- [ ] **QA-F3-002 · Test stuck detector**

Bloquear el camino con un objeto y observar:
- El bot debe intentar un "nudge" (pequeño rodeo)
- Si no puede pasar en 3 intentos → repath
- Si repath falla → abort con log

Verificar en `logs/` que aparece `[STUCK]` con recovery action.

### Task 2.2: Script executor (segmento de ruta)

**Files:** `src/script_executor.py`, `routes/wasp_thais/wasp_thais_ek_nopvp.json`

- [ ] **QA-F3-003 · Ejecutar solo el segmento `go_hunt`**

Editar temporalmente la ruta para saltar al label `go_hunt`:
```bash
python main.py track --dest-name "wasp" --route routes/wasp_thais/wasp_thais_ek_nopvp.json --start-label go_hunt
```
Observar:
- El personaje camina desde z=7 hasta el hoyo del shovel
- El personaje hace el `shovel` (usa pala en el tile)
- El personaje baja a z=8 (la cueva de wasps)
- El personaje patrulla los nodos dentro de z=8

Criterios PASS: llega a z=8 en < 2 min sin errores de navegación.

- [ ] **QA-F3-004 · Verificar combat en z=8**

Con monstruos presentes en la cueva:
- Battle list debe mostrar `Wasp` / `Wolf`
- El bot debe atacar (tecla de ataque según `combat_config.json`)
- HP/MP debe fluctuar y el healer debe reaccionar

Verificar en log: `[COMBAT] target=Wasp` y `[HEAL] HP=XX% → usando hotkey`.

### Task 2.3: Loop completo de depositar

**Files:** `src/depot_manager.py`, `src/depot_orchestrator.py`

- [ ] **QA-F3-005 · Ciclo de deposit**

Poner ≥1 honeycomb en inventario. Ejecutar desde label `refill`:
```bash
python main.py track --route routes/wasp_thais/wasp_thais_ek_nopvp.json --start-label refill
```
Observar:
- El bot navega al depot (z=8 → z=7 → depot)
- Abre el depot y deposita el loot
- Navega al NPC de potions (abre puerta, compra mana potions)
- Regresa al spawn

Criterios PASS: ciclo completo sin intervención manual en < 5 min.

---

## Fase 3 — Integración completa (ciclo de caza)

> Personaje con inventario de inicio (rope, shovel, mana potions ≥10, mochila orange).

### Task 3.1: Ciclo completo nopvp (mouse input)

**Files:** `routes/wasp_thais/wasp_thais_ek_nopvp.json`

- [ ] **Ejecutar ruta base (mouse)**
```bash
python main.py track --route routes/wasp_thais/wasp_thais_ek_nopvp.json
```

Checklist durante ejecución (observar 1 ciclo completo ~10-15 min):

| Check | Esperado | Resultado |
|-------|----------|-----------|
| Navega de depot a shovel hole | Sin atascarse | |
| Shovel abre el hoyo | Hoyo aparece en tile | |
| Baja a z=8 | Personaje en cueva | |
| Patrulla 8 nodos en z=8 | Sin atascarse | |
| `count` check (honeycomb ≥50) | Salta correctamente | |
| Sube con rope a z=7 | Personaje en superficie | |
| Regresa al depot | Sin errores de pathfinding | |
| Deposita loot | Depot UI abre y cierra | |
| Compra mana potions | NPC UI abre, compra, cierra | |
| Inicia siguiente ciclo | Loop sin intervención | |

Criterios PASS: 2 ciclos completos consecutivos sin errores manuales.

- [ ] **Verificar telemetría**
```python
from src.telemetry import SessionTelemetry
import json
from pathlib import Path

# Buscar último snapshot
snaps = sorted(Path('output').glob('telemetry_*.json'), key=lambda p: p.stat().st_mtime)
if snaps:
    data = json.loads(snaps[-1].read_text())
    print(f"Steps: {data.get('steps_walked', 0)}")
    print(f"Kills: {data.get('kills', 0)}")
    print(f"Depot cycles: {data.get('depot_cycles', 0)}")
```
Esperado tras 2 ciclos: kills > 0, depot_cycles ≥ 1.

### Task 3.2: Ciclo completo live (interception input)

**Files:** `routes/wasp_thais/wasp_thais_ek_nopvp_live.json`

> Requiere Interception kernel driver instalado y activo.

- [ ] **Verificar Interception activo**
```bash
python -c "import interception; d = interception.Interception(); print('Interception OK')"
```
Si falla: reinstalar driver + reboot. No continuar sin este check.

- [ ] **Ejecutar ruta live (interception)**
```bash
python main.py track --route routes/wasp_thais/wasp_thais_ek_nopvp_live.json
```
Mismos checks que Task 3.1. Diferencias esperadas:
- Input más fluido (hardware-level)
- `sell` vende viales automáticamente (items explícitos configurados)
- `buy_potions` qty=10 (vs 5 en base)

Criterios PASS: 2 ciclos sin errores. Input no detectado como bot por Tibia.

---

## Fase 4 — Stress y resistencia

> Solo ejecutar si Fase 3 pasa completamente.

### Task 4.1: Soak de 1 hora

- [ ] **Ejecutar soak**
```bash
python tools/soak_monitor.py --route routes/wasp_thais/wasp_thais_ek_nopvp_live.json --duration 3600
```
Métricas objetivo a 60 min:

| Métrica | Límite FAIL |
|---------|-------------|
| CPU promedio | > 80% |
| RAM peak | > 800 MB |
| Crashes / reintentos | > 3 |
| Ciclos completados | < 4 |
| Deaths del personaje | > 0 |

- [ ] **Verificar logs de soak**
```bash
python -c "
from pathlib import Path
import re
log = next(Path('logs').glob('soak_*.log'), None)
if log:
    text = log.read_text(encoding='utf-8', errors='replace')
    errors = [l for l in text.splitlines() if 'ERROR' in l or 'CRITICAL' in l]
    print(f'Errores en log: {len(errors)}')
    for e in errors[:10]: print(' ', e)
"
```
Esperado: 0 errores CRITICAL.

### Task 4.2: Simulación de muerte

- [ ] **Forzar death y verificar recovery**

Acercar un GM o reducir HP manualmente a 0. Observar:
- `death_handler.py` detecta la pantalla de muerte (`cache/templates/death_screen.png`)
- El bot espera respawn
- Reanuda desde `start` label

Criterios PASS: bot retoma automáticamente en < 3 min post-respawn.

### Task 4.3: Simulación de desconexión

- [ ] **Desconectar red por 10 segundos y reconectar**

Observar:
- `reconnect_handler.py` detecta la pantalla de login
- El bot re-logea automáticamente
- Reanuda la ruta desde checkpoint

Criterios PASS: reconnect en < 60 segundos sin intervención.

---

## Fase 5 — Criterios de paso a producción

Todos los siguientes deben estar en PASS antes de marcar la ruta como "production":

- [ ] `python -m pytest tests/ -q` → 0 failed
- [ ] Route validator A* → OK en ambos archivos
- [ ] Fase 0 pre-flight → todos PASS
- [ ] Fase 1 componentes live → todos PASS
- [ ] Fase 2 subsistemas → todos PASS
- [ ] Fase 3 integración → ≥2 ciclos completos en ambas rutas
- [ ] Fase 4 soak 1h → dentro de límites
- [ ] 0 deaths involuntarias en soak
- [ ] 0 errores CRITICAL en logs

**Al completar todos:** actualizar `LIVE_TEST_PLAN_v3.md` marcando los módulos como `✅ PASS` y pusheando a `gmastro06-blip/frbit`.

---

## Referencia rápida de comandos

```bash
# Validar rutas (offline)
python -c "
import sys; sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path; from tools.route_validator import validate_path
for r in Path('routes').rglob('*.json'): validate_path(r, run_astar=True)
"

# Ejecutar ruta base (mouse)
python main.py track --route routes/wasp_thais/wasp_thais_ek_nopvp.json

# Ejecutar ruta live (interception)
python main.py track --route routes/wasp_thais/wasp_thais_ek_nopvp_live.json

# Tests offline completos
python -m pytest tests/ -q --tb=short

# Calibrar minimap
python main.py calibrate

# Ver floor map del spawn
python main.py show-floor 8

# Preflight check completo
python tools/preflight_check.py
```
