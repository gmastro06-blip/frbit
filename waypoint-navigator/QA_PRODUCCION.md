# Plan de QA Exhaustivo — Waypoint Navigator
**Objetivo:** Validar el bot de extremo a extremo antes de pasar a producción.
**Fecha:** 2026-03-27

---

## Índice

1. [Estrategia general](#1-estrategia-general)
2. [Fase 0 — Pre-flight y entorno](#2-fase-0--pre-flight-y-entorno)
3. [Fase 1 — Tests automatizados offline](#3-fase-1--tests-automatizados-offline)
4. [Fase 2 — Componentes en vivo (unitario)](#4-fase-2--componentes-en-vivo-unitario)
5. [Fase 3 — Subsistemas en vivo](#5-fase-3--subsistemas-en-vivo)
6. [Fase 4 — Integración completa](#6-fase-4--integración-completa)
7. [Fase 5 — Stress y resistencia](#7-fase-5--stress-y-resistencia)
8. [Fase 6 — Casos de borde y fallos](#8-fase-6--casos-de-borde-y-fallos)
9. [Métricas de salud en producción](#9-métricas-de-salud-en-producción)
10. [Criterios de paso a producción](#10-criterios-de-paso-a-producción)

---

## 1. Estrategia general

### Principios
- **Nada se salta.** Cada fase debe pasar completamente antes de pasar a la siguiente.
- **Falla explícita.** Si algo no puede verificarse, se bloquea el avance.
- **Trazabilidad.** Cada test tiene ID (QA-Fx-NNN), resultado (PASS/FAIL/SKIP), y timestamp.
- **Entorno limpio.** Tibia cerrado y reabierto antes de cada fase para estado fresco.

### Árbol de prioridad
```
BLOQUEANTE ─── Input delivery (sin input → nada funciona)
            ├── HP/MP reading (lectura errónea → muerte)
            ├── Frame capture (sin frames → todo ciego)
            └── Combat templates (sin templates → no ataca)

CRÍTICO ───── Navigator / pathfinding
           ├── Healer thresholds
           ├── Death detection
           └── Reconnect handling

IMPORTANTE ── Stuck detector
           ├── Depot / Loot
           ├── Alert system
           └── Break scheduler

NICE TO HAVE ─ Dashboard GUI
            ├── PvP detector
            └── GM detector
```

### Entorno requerido

| Ítem | Requisito |
|------|-----------|
| OS | Windows 11, sin actualizaciones pendientes |
| Tibia | Cliente oficial, resolución 1920×1080, UI default |
| Python | 3.11+ con `.venv` activo |
| Kernel driver | Interception instalado y activo (verificar `run_check.py`) |
| OBS | Corriendo con Virtual Camera activa (si source=obs) |
| Personaje | Cuenta de test, lvl suficiente para spawn elegido |
| Spawn | Spawn con monstruos que coincidan con templates en `cache/templates/` |
| Red | Conexión estable (<50 ms ping a servidor Tibia) |

---

## 2. Fase 0 — Pre-flight y entorno

### QA-F0-001 · Run check completo
```
Comando: python run_check.py
Espera:  Todas las secciones en verde
Falla:   Cualquier [ERROR] en output
Acción si falla: Instalar dependencia faltante, no continuar
```

### QA-F0-002 · Verificar Interception driver
```
Comando: python -c "import interception; d = interception.Interception(); print('OK')"
Espera:  OK
Falla:   ImportError o PermissionError
Acción si falla: Reinstalar kernel driver + reboot
```

### QA-F0-003 · Verificar frame capture con Tibia abierto
```
Comando: python tools/check_frame.py  (o debug_capture.py)
Espera:  Frame 1920×1080 RGB guardado en output/
Falla:   Frame negro, tamaño erróneo, timeout
Acción si falla: Cambiar frame_source en config (dxcam → mss → obs)
```

### QA-F0-004 · Verificar templates de monstruos
```
Verificar: ls cache/templates/monsters/ → debe tener ≥1 .png
Test: python -c "
from src.combat_manager import BattleDetector, CombatConfig
cfg = CombatConfig.load()
bd = BattleDetector(cfg)
print(f'Templates: {len(bd._templates)}')
"
Espera:  Templates: N (N > 0)
Falla:   Templates: 0
Acción si falla: Añadir PNGs de iconos de battle list para los monstruos del spawn
```

### QA-F0-005 · Verificar ruta de waypoints
```
Verificar: combat_config.json → route_file apunta a archivo existente
Test: python -c "
from src.models import Route
import json, pathlib
r = json.loads(pathlib.Path('routes/<tu_ruta>.json').read_text())
print(f'Waypoints: {len(r[\"waypoints\"])}')
"
Espera:  Waypoints: N (N > 0)
Falla:   FileNotFoundError o JSON inválido
```

### QA-F0-006 · Calibrar y verificar ROIs de HP/MP
```
Comando: python calibrate_viewport.py
Acción:  Mover slider hasta que la barra resaltada coincida con HP/MP en pantalla
Verificar:
  - hpmp_config.json → hp_roi y mp_roi actualizados
  - python -c "
    from src.hpmp_detector import HpMpDetector, HpMpConfig
    import numpy as np
    # pone aquí un frame capturado de Tibia
    "
Espera:  HP% entre 95-100 cuando personaje tiene HP llena
Falla:   HP% < 50 o > 105 con personaje sano → ROI mal calibrado
Acción si falla: Re-ejecutar calibrate_viewport.py
```

### QA-F0-007 · Verificar ROI de Battle List
```
Comando: python debug_all_rois.py
Acción:  Abrir imagen generada en output/ y verificar visualmente que el recuadro
         de battle_list_roi enmarca correctamente el panel izquierdo de Tibia
Falla:   Recuadro vacío, fuera de pantalla, o sobre el mapa
```

### QA-F0-008 · Verificar ROI de minimap
```
Comando: python debug_capture.py
Acción:  Verificar que minimap_roi enmarca el minimapa (esquina superior derecha)
Falla:   Recuadro vacío o fuera de minimap
```

### QA-F0-009 · Validar archivos de configuración
```
Comando: python -c "
from src.combat_manager import CombatConfig
from src.healer import HealConfig
cfg_c = CombatConfig.load(); cfg_c.validate(); print('combat OK')
cfg_h = HealConfig.load(); print('heal OK')
"
Espera:  combat OK / heal OK (sin excepciones)
Falla:   ValidationError → revisar el campo indicado en el JSON
```

---

## 3. Fase 1 — Tests automatizados offline

> No se requiere Tibia abierto. Todos los módulos externos están mockeados.

### QA-F1-001 · Suite completa pytest
```
Comando: python -m pytest tests/ -x --timeout=60 -q
Espera:  0 failed; la suite actual colecta ~5430 tests
Falla:   Cualquier FAILED → investigar antes de continuar
Notas:   -x detiene en primer fallo; quitar para ver todos
```

### QA-F1-002 · Tests de config y validación
```
Comando: python -m pytest tests/test_config_validation.py tests/test_preflight.py -v
Espera:  PASSED en todos los casos de validación de campos de config
```

### QA-F1-003 · Tests del combat manager (offline)
```
Comando: python -m pytest tests/test_combat.py tests/test_combat_manager.py -v
Espera:  PASSED — template matching, kill tracking, flee logic, lure detection
Cobertura objetivo: >80% en combat_manager.py
```

### QA-F1-004 · Tests del healer (offline)
```
Comando: python -m pytest tests/test_healer.py -v
Espera:  PASSED — thresholds, emergency heal, mana logic, cooldowns
```

### QA-F1-005 · Tests del navigator (offline)
```
Comando: python -m pytest tests/test_navigator.py -v
Espera:  PASSED — pathfinding, waypoint following, floor transitions
```

### QA-F1-006 · Tests del stuck detector
```
Comando: python -m pytest tests/test_stuck_detector.py -v
Espera:  PASSED — nudge, repath, escape, abort, cooldown de re-enable (T5)
Verificar especialmente: test del abort cooldown (T5) y position verification (M4-fix)
```

### QA-F1-007 · Tests de frame cache y quality
```
Comando: python -m pytest tests/test_frame_cache.py tests/test_frame_quality.py -v
Espera:  PASSED
```

### QA-F1-008 · Tests de session (offline)
```
Comando: python -m pytest tests/test_session.py -v --timeout=30
Espera:  PASSED — ciclo de vida start/stop, preflight, threading
```

### QA-F1-009 · Tests de script executor
```
Comando: python -m pytest tests/test_script_executor.py -v
Espera:  PASSED
```

### QA-F1-010 · Tests de casos de borde
```
Comando: python -m pytest tests/test_edge_cases.py -v
Espera:  PASSED — inputs nulos, frames corruptos, configs vacías
```

### QA-F1-011 · Tests e2e offline
```
Comando: python -m pytest tests/test_e2e_offline.py -v --timeout=120
Espera:  PASSED — ciclo completo con todo mockeado
```

### QA-F1-012 · Reporte de cobertura
```
Comando: python -m pytest tests/ --cov=src --cov-report=html:output/coverage_html -q
Verificar: output/coverage_html/index.html
Objetivo: ≥75% total coverage (excluidos módulos en .coveragerc)
Falla si: <60% en session.py, navigator.py, healer.py, combat_manager.py, stuck_detector.py
```

### QA-F1-013 · Niveles de test (run_nivel*)
```
Comandos (en orden):
  python run_nivel0_tests.py
  python run_nivel1_tests.py
  python run_nivel2_tests.py
  python run_nivel3_tests.py
  python run_nivel4_tests.py
  python run_nivel5_tests.py
  python run_nivel6_tests.py
  python run_nivel7_tests.py
Espera:  PASSED en cada nivel antes de ejecutar el siguiente
Falla:   Anotar qué nivel falla y el test específico
```

---

## 4. Fase 2 — Componentes en vivo (unitario)

> Tibia abierto, personaje parado. Un componente a la vez.

### QA-F2-001 · Frame capture en vivo
```
Comando: python -c "
import time
from src.frame_capture import build_frame_getter
from src.detector_config import DetectorConfig
cfg = DetectorConfig()
getter = build_frame_getter(cfg)
for i in range(5):
    f = getter()
    print(f'Frame {i}: shape={f.shape if f is not None else None}')
    time.sleep(0.2)
"
Espera:  5 frames shape=(1080, 1920, 3)
Falla:   None, shape incorrecto, o exception
Verificar también: FPS efectivos ≥ 15 fps (60ms/frame max)
```

### QA-F2-002 · Lectura HP/MP en vivo
```
Acción:  Personaje con HP y MP completos
Comando: python -c "
import time
from src.hpmp_detector import HpMpDetector, HpMpConfig
from src.detector_config import DetectorConfig
from src.frame_capture import build_frame_getter
cfg_d = DetectorConfig()
cfg_h = HpMpConfig.load()
getter = build_frame_getter(cfg_d)
det = HpMpDetector(cfg_h)
for i in range(10):
    f = getter()
    hp, mp = det.read(f)
    print(f'HP={hp:.1f}%  MP={mp:.1f}%')
    time.sleep(0.1)
"
Espera:  HP ~100%, MP ~100% (±5%)
Verificar: Beber una poción de mana → MP debe bajar en la lectura
Falla:   HP/MP fuera de rango 0-105, o siempre 0 → ROI mal calibrada
```

### QA-F2-003 · Input delivery — teclado
```
Acción:  Personaje parado, mano quieta en canal general de Tibia
Prueba 1: Hotkey de heal (F1 o el configurado)
  → Verificar que el personaje intenta curar (efecto visual o mensaje)
Prueba 2: Hotkey de mana (F2)
  → Verificar bebida de mana visible
Prueba 3: Movimiento (arrow keys via input_controller)
  Comando: python -c "
  from src.input_controller import InputController, InputConfig
  ic = InputController(InputConfig())
  ic.press_key(0x25)  # Left arrow
  "
  → Personaje debe moverse 1 tile a la izquierda
Falla: No se produce ningún efecto en juego → Input method equivocado
```

### QA-F2-004 · Detección de monstruos en vivo
```
Acción:  Personaje en zona con monstruos visibles en battle list
Comando: python -c "
from src.combat_manager import BattleDetector, CombatConfig
from src.frame_capture import build_frame_getter
from src.detector_config import DetectorConfig
import cv2, numpy as np
cfg = CombatConfig.load()
bd = BattleDetector(cfg)
getter = build_frame_getter(DetectorConfig())
f = getter()
dets = bd.detect(f)
print(f'Detecciones: {len(dets)}')
for x, y, conf, name in dets:
    print(f'  {name}: conf={conf:.2f} pos=({x},{y})')
"
Espera:  ≥1 detección con nombre de monstruo y conf ≥ 0.5
Falla:   0 detecciones → templates mal recortados o ROI incorrecta
```

### QA-F2-005 · Minimap radar en vivo
```
Acción:  Personaje parado en posición conocida (anotar coordenadas de Tibia)
Comando: python -c "
from src.minimap_radar import MinimapRadar, MinimapConfig
from src.frame_capture import build_frame_getter
from src.detector_config import DetectorConfig
import time
cfg = MinimapConfig.load()
radar = MinimapRadar(cfg)
getter = build_frame_getter(DetectorConfig())
for i in range(5):
    f = getter()
    pos = radar.get_position(f)
    print(f'Pos: {pos}')
    time.sleep(1)
"
Espera:  Coordenadas x,y,z cercanas a la posición real (±3 tiles)
Acción:  Mover personaje 5 tiles → verificar que pos cambia en consecuencia
Falla:   None siempre → minimap ROI incorrecta o floor incorrecto en config
```

### QA-F2-006 · Alert system en vivo
```
Acción:  Configurar Discord webhook en alert_config (opcional)
Comando: python -c "
from src.alert_system import AlertManager, AlertConfig
cfg = AlertConfig(enabled=True, discord_webhook='TU_WEBHOOK')
am = AlertManager(cfg)
am.send('e18', {'msg': 'QA test alert'})
"
Espera:  Mensaje recibido en Discord en <30 segundos
Falla:   Timeout o URL rechazada → verificar webhook URL en config
```

---

## 5. Fase 3 — Subsistemas en vivo

> Tibia abierto. Un subsistema activo, el resto en modo pasivo/desactivado.

### QA-F3-001 · Combat only (run_phase1)
```
Config:  Deshabilitar navigator (enable_navigation=false en session config)
         Deshabilitar healer temporalmente para aislar
Comando: python run_phase1.py
Duración: 5 minutos
Verificar:
  [ ] Bot detecta monstruos en battle list
  [ ] Ataca con hotkeys (ver F7/F8/F9 en pantalla)
  [ ] Kill counter incrementa al morir monstruos
  [ ] No ataca cuando battle list vacía
  [ ] Flee funciona cuando HP < hp_flee_pct (simular bajando HP manualmente)
  [ ] Lure detection: si llegan > flee_mob_count monstruos → acción correcta
Métricas:
  Kills/min ≥ 5 (depende del spawn)
  False positives: ≤ 5% (ataques a UI elements, no monstruos)
```

### QA-F3-002 · Healer only
```
Config:  Deshabilitar navigator y combat
Comando: python -c "
from src.healer import AutoHealer, HealConfig
# setup con frame getter y input controller
"
Duración: 10 minutos
Verificar:
  [ ] Heal se dispara cuando HP baja de hp_threshold_pct (simular con /setHp command)
  [ ] Emergency heal se dispara en hp_emergency_pct
  [ ] Mana restore funciona cuando MP < mp_threshold_pct
  [ ] No spamea heal cuando HP está alta (cooldown funciona)
  [ ] check_interval ≤ 100ms (medir latencia promedio de respuesta)
Prueba crítica:
  Bajar HP manualmente a 35% → emergency heal debe dispararse en < 200ms
```

### QA-F3-003 · Navigator only (run_phase2)
```
Config:  Deshabilitar combat y healer
Comando: python run_phase2.py
Duración: 10 minutos (ruta completa ×2)
Verificar:
  [ ] Personaje sigue ruta de waypoints en orden correcto
  [ ] Floor transitions (stairs/ropes/holes) se ejecutan correctamente
  [ ] Personaje no se queda pegado (stuck detector interviene)
  [ ] Repath funciona si personaje se desvía del waypoint
  [ ] step_interval sincroniza con velocidad de walk de Tibia
Métricas:
  Waypoints completados: 100%
  Stucks: ≤ 1 por vuelta de ruta
  Tiempo por waypoint: < step_interval × 1.5
```

### QA-F3-004 · Stuck detector — test manual
```
Acción:  Con navigator activo, colocar un obstáculo (tile de agua, box, jugador bloqueando)
Verificar:
  [ ] Después de stuck_timeout (8s default): detector activa recovery
  [ ] Recovery 1 — Repath: intenta nuevo camino
  [ ] Recovery 2 — Nudge: mueve 1 tile aleatorio
  [ ] Recovery 3 — Escape: presiona escape / usa rope
  [ ] Recovery 4 — Abort: emite e31 y para walker
  [ ] Después de abort_cooldown (60s): walker se re-habilita automáticamente (T5)
Falla:   Bot nunca escapa → nudge_fn o escape_fn no están conectados
```

### QA-F3-005 · Death handler — test manual
```
Acción:  Morir intencionalmente en zona segura
Verificar:
  [ ] Death screen detectada (log: "death detected")
  [ ] Healer para
  [ ] Combat para
  [ ] Navigator para
  [ ] Se activa death_handler (espera, acepta pantalla, o notifica)
  [ ] Se emite evento de muerte (e31 o similar)
Falla:   Bot sigue intentando moverse/atacar después de morir
```

### QA-F3-006 · Reconnect handler — test manual
```
Acción:  Desconectar internet brevemente (3-5 segundos)
Verificar:
  [ ] Frame watchdog detecta timeout de frames
  [ ] Bot detecta pantalla de login
  [ ] Intenta reconectar (max reconnect_retries)
  [ ] Después de login exitoso, retoma operación normal
Falla:   Bot loop crash o queda en estado zombi
```

---

## 6. Fase 4 — Integración completa

### QA-F4-001 · Full session (run_phase3)
```
Comando: python run_phase3.py
Duración: 30 minutos
Config:  Todo habilitado: navigator + combat + healer + stuck + death
Verificar:
  [ ] Start sequence completa sin errors en log
  [ ] Personaje navega a spawn
  [ ] Combat se activa al encontrar monstruos
  [ ] HP se mantiene por encima de emergency threshold (40%)
  [ ] Personaje vuelve a la ruta después de combate
  [ ] Kills/min ≥ target del spawn
  [ ] No hay errores CRITICAL en output/app.log
  [ ] CPU < 50%, RAM < 500 MB
  [ ] Threads no se acumulan (verificar thread count estable)
```

### QA-F4-002 · Full session con Loot habilitado
```
Duración: 15 minutos
Verificar:
  [ ] Loot se recoge después de cada kill
  [ ] Inventario no se llena sin acción (alerta o depot trip)
  [ ] No hay doble-loot (loot del mismo cadáver ×2)
```

### QA-F4-003 · Full session con Depot habilitado
```
Acción:  Llenar inventario manualmente al inicio
Verificar:
  [ ] Depot trip se activa cuando loot_threshold se supera
  [ ] Navigator lleva al personaje al depot correctamente
  [ ] Items se depositan en la bodega
  [ ] Personaje retoma ruta después del depot
```

### QA-F4-004 · Break scheduler
```
Config:  break_scheduler.enabled=true, min_break=1min, max_break=2min (para test)
Duración: 15 minutos
Verificar:
  [ ] Bot para en break window
  [ ] Log registra inicio y fin de break
  [ ] Bot retoma operación después del break correctamente
```

### QA-F4-005 · Anti-kick
```
Config:  anti_kick.enabled=true
Duración: 20 minutos parado (sin movimiento intencional)
Verificar:
  [ ] No recibe kick por AFK en 15 minutos
  [ ] Input anti-kick se envía periódicamente (log)
  [ ] Input es sutil (no visible/obvio para observadores)
```

---

## 7. Fase 5 — Stress y resistencia

### QA-F5-001 · Endurance test — 2 horas
```
Comando: python run_phase3.py (con todo habilitado)
Duración: 2 horas continuas
Monitorear (cada 15 min):
  [ ] CPU% estable (no creciente → leak de frames o threads)
  [ ] RAM MB estable (no creciente → memory leak)
  [ ] Thread count estable
  [ ] Kills/min estable (no degradación de performance)
  [ ] Log sin errores CRITICAL o EXCEPTION no manejadas
Falla si:
  - CPU > 70% sostenido por >5 minutos
  - RAM crece > 100 MB/hora
  - Thread count crece indefinidamente
  - Kills/min cae > 50% del baseline
```

### QA-F5-002 · Frame drop bajo carga
```
Acción:  Ejecutar proceso CPU-intensivo en background (ej. video renderizado)
Verificar:
  [ ] Frame watchdog no hace timeout
  [ ] Bot degrada gracefully (más lento pero funcional)
  [ ] No crashea bajo frame starvation
```

### QA-F5-003 · Recovery de múltiples stucks consecutivos
```
Acción:  Colocar obstáculos repetidamente durante 30 minutos
Verificar:
  [ ] Stuck detector escala correctamente (repath → nudge → escape → abort)
  [ ] Abort cooldown (60s) se respeta antes de re-habilitación (T5)
  [ ] global_abort_count no supera max_aborts (3 default) → bot para permanentemente
  [ ] Alert se envía al Discord/Telegram en cada abort
```

### QA-F5-004 · Ciclo muerte + reconnect repetido
```
Acción:  Morir 3 veces consecutivas en 30 minutos
Verificar:
  [ ] Death handler se activa correctamente cada vez
  [ ] Reconnect funciona después de cada muerte
  [ ] Stats (deaths, kills) se acumulan correctamente en session_stats
  [ ] No hay race condition entre death_handler y combat_manager
```

---

## 8. Fase 6 — Casos de borde y fallos

### QA-F6-001 · Frame negro / Tibia minimizado
```
Acción:  Minimizar Tibia mientras el bot está corriendo
Espera:  Frame watchdog detecta frames inválidos, suspende operación
Falla:   Bot intenta actuar con frame negro → crash o acción errónea
```

### QA-F6-002 · Config inválida / campos faltantes
```
Acción:  Borrar `battle_list_roi` de combat_config.json, lanzar bot
Espera:  ValidationError clara con mensaje descriptivo, bot no arranca
Falla:   Bot arranca con valores default silenciosos erróneos
```

### QA-F6-003 · Sin templates de monstruos
```
Acción:  Vaciar cache/templates/monsters/, lanzar bot con combat habilitado
Espera:  Warning claro en log "0 templates cargados", combat deshabilitado o en fallback OCR
Falla:   Exception no manejada, crash
```

### QA-F6-004 · Spawn sin monstruos
```
Acción:  Llevar personaje a zona sin monstruos
Espera:  Bot navega, combat inactivo, no spamea ataques a nada
Falla:   False positives de detección, ataques al vacío
```

### QA-F6-005 · PvP — jugador en zona
```
Acción:  Entrar otro jugador a la zona de farming
Espera:  PvP detector activa modo seguro (si pvp_detector habilitado)
         O bien: bot ignora al jugador y no lo ataca
Falla:   Bot ataca al jugador → ban por PvP no autorizado
```

### QA-F6-006 · GM en zona
```
Acción:  Simular presencia de GM (si hay forma de test)
Espera:  gm_detector activa pausa, alerta enviada
Falla:   Bot continúa operando normalmente
```

### QA-F6-007 · Pérdida de red prolongada (>30 segundos)
```
Acción:  Deshabilitar red durante 30-60 segundos
Espera:  Bot detecta pérdida, espera, reintenta (max reconnect_retries=5)
         Si supera retries → para y alerta
Falla:   Loop infinito de reconexión sin cap
```

### QA-F6-008 · OBS / fuente de video cae (si source=obs)
```
Acción:  Cerrar OBS mientras el bot está corriendo
Espera:  Frame source maneja ConnectionError, frame watchdog activa timeout
Falla:   Crash no manejado, exception no capturada
```

### QA-F6-009 · Kill counter — monstruo desaparece sin morir
```
Situación: Un monstruo aparece en battle list y desaparece (rune, huye, despawn)
Espera:  absence_counter incrementa, pero solo confirma kill después de
         absence_frames_required frames consecutivos
Falla:   Kill false positive (kill contado sin que el monstruo haya muerto)
```

### QA-F6-010 · Múltiples monstruos mismo tipo (duplicados en Counter)
```
Situación: 2 "Troll" en battle list, 1 muere
Espera:  Kill count = 1, no 2
Falla:   Counter mal decrementado → kill doble
Nota:    Revisar fix aplicado en combat_manager.py (Counter reemplazó list.remove)
```

---

## 9. Métricas de salud en producción

### Objetivo operativo

Estas métricas no son para "se ve bien". Sirven para decidir si la sesión sigue sola, si debe pausar, o si debe abortarse antes de perder control del personaje.

### Fuente de verdad

| Fuente | Uso |
|--------|-----|
| Dashboard (`http://localhost:8080`) | Vista rápida de sesión si está habilitado |
| `output/app.log` o `output/phase3.log` | Confirmación de eventos, errores y recovery |
| `session_stats` / EventBus | Contadores de kills, deaths, stucks, uptime |
| Administrador de tareas / PerfMon | Confirmación de CPU y RAM reales del proceso |

### Frecuencia de revisión

| Horizonte | Qué mirar |
|----------|-----------|
| Cada 1 minuto | Kills/min, HP mínimo, stucks recientes, errores nuevos |
| Cada 5 minutos | CPU, RAM, frame drops, latencia de captura |
| Cada vuelta de ruta | Tiempo por ciclo, loot anómalo, desvíos o abortos |
| Al final de cada sesión | Kills/hora, deaths, reconnects, aborts, duración total |

### Umbrales operativos

| Métrica | Verde | Amarillo | Rojo | Acción sugerida |
|---------|-------|----------|------|-----------------|
| Kills/min | ≥ 20 | 10–19 | < 10 | Revisar battle list ROI, templates, spawn o path |
| HP% mínimo últimos 5 min | ≥ 50% | 35–49% | < 35% | Ajustar healer o reducir agresividad del spawn |
| MP% mínimo últimos 5 min | ≥ 25% | 10–24% | < 10% | Ajustar restore/cooldowns o revisar supply |
| Frame drop % | < 2% | 2–10% | > 10% | Revisar source de captura, OBS y carga del sistema |
| Latencia de frame | < 60 ms | 60–120 ms | > 120 ms | Cambiar backend o reducir carga concurrente |
| CPU proceso | < 40% | 40–60% | > 60% | Revisar OCR, template matching o capturas duplicadas |
| RAM proceso | < 300 MB | 300–500 MB | > 500 MB | Buscar fuga, cache excesiva o loops de debug |
| Stucks/hora | 0–2 | 3–5 | > 5 | Revisar mapa, blocked tiles, path y recovery |
| Reconnects/hora | 0 | 1 | > 1 | Revisar red, cliente y handler de reconexión |
| Deaths/hora | 0 | 1 | > 1 | Pausar sesión y revisar healer/combat/path |
| Aborts por stuck | 0 | 1 | > 1 | No continuar sin corregir el recovery chain |

### Reglas de decisión rápidas

| Señal | Decisión |
|------|----------|
| 1 métrica roja aislada durante <2 min | Observar, no abortar todavía |
| 2 métricas rojas simultáneas | Pausar sesión y revisar logs |
| Death + reconnect + stuck en la misma ventana de 10 min | Abort controlado; no reanudar automáticamente |
| Kills/min en amarillo sostenido 15 min | Revisar eficiencia; puede seguir si no hay riesgo |
| CPU/RAM en rojo sostenido 5 min | Reiniciar sesión o degradar subsistemas no críticos |

### Logs a monitorear en vivo en Windows

```powershell
# Errores y recovery crítico
Get-Content output/app.log -Wait |
  Select-String -Pattern "ERROR|CRITICAL|EXCEPTION|stuck|death|abort|reconnect"

# Confirmación de kills
Get-Content output/app.log -Wait |
  Select-String -Pattern "Kill confirmado|kill_count|e1"

# Estado de healer / HP / MP
Get-Content output/app.log -Wait |
  Select-String -Pattern "heal|HP=|MP=|emergency"

# Si la sesión se corre con run_phase3.py
Get-Content output/phase3.log -Wait |
  Select-String -Pattern "ERROR|CRITICAL|stuck|death|kill|heal"
```

### Alertas críticas

| Evento | ID/Evento | Qué significa | Severidad |
|--------|-----------|----------------|-----------|
| Muerte del personaje | `e3` | Death handler detectó muerte | Crítica |
| GM detectado | `e15` | Riesgo operativo inmediato | Crítica |
| Error crítico de inventario/flujo | `e19` | Falla seria de subsistema | Alta |
| Recovery de stuck | `e30` | Repath / nudge / escape en curso | Media |
| Abort por stuck | `e31` | El recovery chain agotó intentos | Crítica |
| Stop permanente por stuck | `e32` | Se alcanzó el máximo de aborts | Crítica |

### SLA interno recomendado para considerar la sesión sana

| Ventana | Objetivo |
|--------|----------|
| 30 min | 0 deaths, 0 aborts, frame drops < 5% |
| 2 h | CPU estable, RAM sin crecimiento sostenido > 20% |
| 1 ciclo de ruta | 0 desvíos irreversibles, 0 bloqueos permanentes |
| 1 sesión completa | Sin `CRITICAL` no recuperado en logs |

### Checklist rápida de operador

| Check | Estado esperado |
|------|-----------------|
| Kills/min no cae a rojo | Sí |
| HP y MP mínimos no entran en rojo | Sí |
| No hay `e3`, `e31`, `e32` en la sesión | Sí |
| No hay crecimiento anómalo de RAM | Sí |
| No hay `CRITICAL` no recuperado en logs | Sí |
| Reconnects = 0 en sesión estable | Sí |
| CPU y frame latency se mantienen fuera de rojo | Sí |

Si uno de estos checks cae en rojo, la sesión deja de ser candidata a producción continua.

---

## 10. Criterios de paso a producción

### BLOQUEANTE (deben estar todos en PASS)

| ID | Criterio |
|----|----------|
| QA-F0-001 | run_check.py sin errores |
| QA-F0-006 | HP/MP reading dentro de ±5% de valor real |
| QA-F0-007 | Battle list ROI visualmente correcta |
| QA-F2-003 | Input delivery funcional (personaje se mueve) |
| QA-F2-004 | ≥1 monstruo detectado con conf ≥ 0.5 |
| QA-F3-001 | Combat solo: kills/min ≥ 5 en 5 minutos |
| QA-F3-002 | Healer: emergency heal en < 200ms |
| QA-F3-003 | Navigator: 100% waypoints completados en 10 min |
| QA-F4-001 | Full session 30 min sin CRITICAL en log |
| QA-F5-001 | Endurance 2h: CPU y RAM estables |
| QA-F6-005 | No ataca jugadores (PvP safety) |

### Gate operativo adicional

Para declarar "production ready" no basta con que pasen los tests manuales/automáticos. La sesión también debe cumplir estos gates de salud definidos en la sección 9:

| Gate | Requisito |
|------|-----------|
| Salud 30 min | 0 deaths, 0 aborts, frame drops < 5%, sin `CRITICAL` no recuperado |
| Salud 2 h | CPU estable, RAM sin crecimiento sostenido > 20%, reconnects = 0 idealmente |
| Riesgo operativo | Ninguna métrica en rojo sostenida más de 5 min |
| Seguridad | Ningún evento `e15`, ningún ataque a jugador, ningún abort permanente `e32` |
| Recovery | Si ocurre un stuck, recovery exitoso sin escalar a `e31`/`e32` |

### CRÍTICO (≥ 90% debe estar en PASS)

| ID | Criterio |
|----|----------|
| QA-F1-001 | pytest suite: 0 FAILED |
| QA-F3-004 | Stuck recovery funciona en los 3 niveles |
| QA-F3-005 | Death detection < 5 segundos |
| QA-F3-006 | Reconnect exitoso después de desconexión breve |
| QA-F4-003 | Depot trip funciona end-to-end |
| QA-F5-003 | múltiples stucks → no loop infinito |
| QA-F6-009 | No false positive kills |
| QA-F6-010 | Duplicados en battle list → kill count correcto |

### Evidencia mínima antes de declarar salida

| Tipo de evidencia | Mínimo |
|------------------|--------|
| Suite offline | `pytest` sin fallos en el subconjunto crítico y sin errores estáticos del subproyecto. Evidencia actual de sesión: lote dirigido `367/367` PASS y `tests/test_session.py` `125/125` PASS |
| Validación live | Fases 2 a 6 documentadas con PASS/FAIL/TIMEOUT y timestamp; usar `python tools/live_test_runner.py <t1..t5|all>` para generar evidencia persistida en `output/live_qa_*.json` y `output/live_qa_*.md` |
| Logs | Archivo de sesión sin `CRITICAL` no recuperado |
| Métricas | Captura o registro de kills/min, frame drops, CPU, RAM, deaths y stucks |
| Alertas | Confirmación de que `e3`, `e15`, `e19`, `e31`, `e32` se enrutan correctamente cuando aplica |

### Estado de revalidación actual

- La suite completa actual colecta ~5430 tests.
- En esta sesión ya se corrigieron los lotes que estaban rompiendo `break_scheduler`, `depot`, `frame_capture`, `gm_detector`, `hpmp`, `input`, `monitor_gui`, `session` y `trade`.
- También se endureció el apagado de hilos en `anti_kick`, `death_handler`, `reconnect_handler` y `stuck_detector` para evitar daemons vivos al final de la suite.
- La rerun completa del `2026-04-02` ya cerró limpia en una sola pasada monolítica; cruzó el tramo histórico del `91-94%` y terminó con `exit code 0`.
- Se dejó una corrida formal persistida en `output/pytest_full_2026-04-01.log` para evidencia única de sesión.
- Esa corrida formal **no cerró limpia**: terminó con `Windows fatal exception: access violation` durante el tramo final, con stack traces activos en hilos de `death_handler`, `reconnect_handler`, `anti_kick` y `stuck_detector`, cerca de `tests/test_trade.py`.
- En la investigación posterior del 2026-04-02, una nueva rerun monolítica y varios cortes acumulativos quedaron atascados cerca del 91-94% con procesos Python creciendo aproximadamente a `10-25 GB` de RSS.
- El perfilado secuencial por archivo desde `tests/test_e2e_offline.py` mostró que los mayores saltos normales de RSS son `tests/test_e2e_offline.py` (~`+49.5 MB`) y `tests/test_frame_capture.py` (~`+51.1 MB`).
- Ese perfilado también permitió aislar una retención importante en `tests/test_trade.py`: el módulo por sí solo retenía ~`1.15 GB` por la inicialización innecesaria de EasyOCR en ROIs vacíos. Tras añadir una guardia visual previa al OCR en `src/trade_manager.py`, `tests/test_trade.py` bajó a ~`661 MB` y el salto acumulativo al entrar en trade desde `tests/test_e2e_offline.py` cayó a ~`+572.7 MB`.
- Con esa optimización, el corte que antes se quedaba atascado (`tests/test_e2e_offline.py` hasta el último archivo) ahora **termina completo** con cobertura total ~`76%`.
- El ruido de rotación concurrente de `output/app.log` en Windows sí quedó corregido. La combinación de limpieza de hilos, liberación más agresiva en backends de captura y la guardia OCR en trade permitió recuperar la estabilidad de la corrida completa.

### Evidencia formal de sesión

| Evidencia | Estado | Ruta / notas |
|----------|--------|--------------|
| Lote dirigido de regresiones | PASS | `367/367` PASS |
| `tests/test_session.py` | PASS | `125/125` PASS |
| `tests/test_frame_capture.py` | PASS | validado nuevamente el `2026-04-02` tras limpiar referencias en backends de captura |
| Tramo final desde `test_trade.py` al último archivo | PASS | `349/349` PASS |
| Corte `tests/test_e2e_offline.py` → último archivo | PASS | rerun exitoso el `2026-04-02` tras optimización de OCR en `src/trade_manager.py`; cobertura total ~`76%` |
| Corrida monolítica completa persistida (`2026-04-01`) | FAIL | `output/pytest_full_2026-04-01.log` → termina con `Windows fatal exception: access violation` |
| Corrida monolítica completa persistida (`2026-04-02`) | PASS | `output/pytest_full_2026-04-02.log` → corrida completa finaliza con `exit code 0`; cobertura total ~`88%` |
| Preflight live real (`printwindow` + `Proyector`) | PASS | `2026-04-02`: `python tools/live_test_runner.py preflight` valida captura real y ROI HP/MP sobre la ventana `Proyector` |
| T1 live navegación | PASS con riesgo observado | `output/live_qa_2026-04-02_12-24-47.json` y `.md` → ventana de observación de `300s` completada; la navegación y el radar funcionaron, pero hubo bloqueos/replans repetidos cerca de `(32372, 32218, 7)` |
| Perfilado secuencial desde `tests/test_e2e_offline.py` | PASS parcial / diagnóstico | `tools/profile_pytest_memory.py` identificó como mayores saltos `test_e2e_offline.py` (~`+49.5 MB`), `test_frame_capture.py` (~`+51.1 MB`) y `test_trade.py`; tras la guardia OCR, el tramo `e2e -> trade` completa con pico ~`869 MB` |

### Implicación operativa actual

- El subproyecto está funcionalmente mucho más estable que al inicio de la revisión.
- No hay fallos abiertos en los lotes Python aislados que se estuvieron corrigiendo en esta sesión.
- La validación offline end-to-end queda reabierta y cerrada favorablemente con la nueva corrida monolítica persistida del `2026-04-02`.
- El riesgo residual principal ya no es un bloqueo conocido del suite offline, sino la validación live de Fases 2 a 6 según este mismo plan.
- Para esa fase live ya existe un punto único de ejecución y evidencia: `tools/live_test_runner.py` deja reportes estructurados en `output/` sin depender de copiar la salida de consola a mano.
- La primera corrida live útil ya quedó registrada: `preflight` real PASS y `T1` PASS por observación. El riesgo live visible ahora es de comportamiento, no de arranque: hay un tramo que reintenta/replanea repetidamente alrededor de `(32372, 32218, 7)`.

### IMPORTANTE (documentar si no pasa, no bloquea)

| ID | Criterio |
|----|----------|
| QA-F4-004 | Break scheduler funciona |
| QA-F4-005 | Anti-kick previene expulsión en 15 min |
| QA-F5-002 | Bot funcional bajo carga CPU alta |
| QA-F6-006 | GM detection activa pausa |
| QA-F1-012 | Coverage ≥ 75% (módulos offline) |

### Plantilla de resultado

```
QA-Fx-NNN | [PASS|FAIL|SKIP] | YYYY-MM-DD HH:MM | Notas: ...
```

### Firma de aprobación

Antes de mover a producción permanente:
- [ ] Todas las pruebas BLOQUEANTES en PASS
- [ ] ≥ 90% de pruebas CRÍTICAS en PASS
- [ ] Pruebas IMPORTANTES documentadas con plan de resolución
- [ ] Log de endurance (2h) archivado en `output/qa_endurance_YYYY-MM-DD.log`
- [ ] Config final commiteada / respaldada
