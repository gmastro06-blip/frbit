# Prompt de contexto — waypoint-navigator (estado real 2026-03-30)

Pega este bloque en una conversación nueva de Claude para que tenga contexto completo del proyecto.

---

## PROMPT

```
Eres un ingeniero de software senior especializado en automatización de juegos, computer vision y Python.

Estoy trabajando en un bot de Tibia llamado **waypoint-navigator**. A continuación está el estado real y actual del proyecto.

---

## CONTEXTO TÉCNICO

- Tibia tiene protección anti-captura de pantalla (BitBlt devuelve imagen negra)
- La captura se hace a través de OBS Studio usando Game Capture + Windowed Projector
- El bot captura el Windowed Projector de OBS (que sí muestra la imagen) usando su HWND
- BattlEye es el anti-cheat activo — NO se puede leer memoria ni inyectar paquetes
- El bot funciona exclusivamente por análisis de imagen + inputs simulados a nivel hardware
- Sistema operativo: Windows 11
- Python 3.12
- Monitor 2 = 1920×1080 | Ventana Tibia = 1456×816

---

## ARQUITECTURA REAL DEL PROYECTO

waypoint-navigator/
├── main.py                          # Entry point con CLI (argparse)
├── __main__.py                      # python -m waypoint_navigator
├── requirements.txt
├── pyproject.toml
├── pytest.ini
├── .coveragerc
├── build.spec                       # PyInstaller spec
├── README.md
├── KNOWLEDGE_BASE.md                # Decisiones de diseño y convenciones
├── QA_PRODUCCION.md
├── LIVE_TEST_PLAN_v3.md             # Plan de tests funcionales en campo (165 casos)
│
├── # Configs JSON (una por módulo, en raíz del proyecto)
├── hpmp_config.json                 # ROIs barras HP/MP + thresholds
├── combat_config.json               # ROI battle list, spells, targeting
├── combat_config_druid.json         # Variante configuración clase Druid
├── combat_config_paladin.json       # Variante configuración clase Paladin
├── combat_config_sorcerer.json      # Variante configuración clase Sorcerer
├── minimap_config.json              # ROI minimapa, paleta de colores
├── loot_config.json                 # ROIs viewport/container, loot rules
├── depot_config.json                # Config ciclo de depósito
├── heal_config.json                 # Thresholds curación, hotkeys, cooldowns
├── trade_config.json                # Config NPC trade (compra suministros)
├── detector_config.json             # ROI coordenadas OCR (detector_config)
│
├── src/                             # ~65 módulos Python, ~33,345 líneas
│   ├── __init__.py
│   │
│   ├── # ── Core session ─────────────────────────────────────────────────
│   ├── session.py                   # Orquestador principal (2,847 líneas)
│   ├── session_stats.py             # Métricas de sesión (kills, loot, uptime)
│   ├── session_persistence.py       # Guarda/restaura estado de sesión entre reinicios
│   ├── multi_session.py             # Gestión de múltiples sesiones paralelas
│   │
│   ├── # ── Script engine ────────────────────────────────────────────────
│   ├── script_executor.py           # Ejecuta scripts .in paso a paso (1,989 líneas)
│   ├── script_parser.py             # Parser formato .in (node/stand/label/call/wait/action)
│   │
│   ├── # ── Captura de pantalla ──────────────────────────────────────────
│   ├── frame_capture.py             # Captura OBS Windowed Projector (HWND + BitBlt)
│   ├── frame_cache.py               # Buffer circular de frames con timestamps
│   ├── frame_quality.py             # Detección de frames negros/corruptos
│   ├── frame_sources.py             # Abstracción: OBS / MSS / archivo / virtual-cam
│   ├── frame_watchdog.py            # Alarma si no llegan frames nuevos (timeout)
│   │
│   ├── # ── Visión / detección ───────────────────────────────────────────
│   ├── hpmp_detector.py             # Lee HP/MP por color de píxeles + EasyOCR (1,108 líneas)
│   ├── minimap_radar.py             # Posición del jugador vía template matching en minimapa (1,149 líneas)
│   ├── combat_manager.py            # Detección y gestión de combate (targeting, spells) (1,160 líneas)
│   ├── character_detector.py        # Detecta sprite del propio personaje en viewport
│   ├── gm_detector.py               # Detecta mensajes de GM / chat sospechoso (e15)
│   ├── pvp_detector.py              # Detecta skulls y jugadores hostiles (e18)
│   ├── condition_monitor.py         # Detecta condiciones (poisoned, burning, etc.)
│   ├── ui_detection.py              # Detecta elementos UI (ventanas abiertas, diálogos)
│   ├── image_processing.py          # Utilidades OpenCV: crop, resize, threshold, morph
│   ├── adaptive_roi.py              # Auto-ajuste de ROIs si la resolución cambia
│   ├── action_verifier.py           # Verifica que una acción tuvo efecto (dialog_open, etc.)
│   ├── deprecated_ocr.py            # Wrapper OCR legacy (PaddleOCR → EasyOCR migration)
│   │
│   ├── # ── Navegación ───────────────────────────────────────────────────
│   ├── models.py                    # Dataclasses: Coordinate, Waypoint, Route, Node, etc.
│   ├── navigator.py                 # A* pathfinding sobre tiles Tibia (8-dir)
│   ├── pathfinder.py                # Implementación A* pura (sin dependencias de sesión)
│   ├── map_loader.py                # Carga mapas .otbm/.tmx para grid de obstáculos
│   ├── position_resolver.py         # Fusiona coord_ocr + minimap_radar → posición canónica
│   ├── stuck_detector.py            # Detecta stuck por pixel_diff + timeout de coordenadas
│   ├── obstacle_analyzer.py         # Marca tiles temporalmente bloqueados tras stuck
│   ├── transitions.py               # Maneja escaleras, teletransportaciones, cambio de piso (z)
│   ├── spawn_manager.py             # Gestiona puntos de spawn: lure, flee, re-engage (e27/e28)
│   ├── path_visualizer.py           # Debug visual: dibuja ruta A* sobre frame
│   ├── walkability_overlay.py       # Overlay de tiles caminables sobre viewport
│   │
│   ├── navigation/                  # Submódulo: grabación y log de rutas
│   │   ├── route_recorder.py        # Graba ruta jugando manualmente
│   │   ├── waypoint_recorder.py     # Variante: graba waypoints con hotkey
│   │   └── waypoint_logger.py       # Log estructurado de waypoints con timestamps
│   │
│   ├── # ── Input / control ──────────────────────────────────────────────
│   ├── input_controller.py          # SendInput hardware-level: teclas, clicks, type (1,397 líneas)
│   ├── mouse_bezier.py              # Movimiento de ratón con curvas de Bézier (aspecto humano)
│   ├── humanizer.py                 # Delays aleatorios, varianza de timing, anti-patrón
│   ├── protocols.py                 # Protocolo abstracto InputController (duck typing)
│   │
│   ├── # ── Healing ──────────────────────────────────────────────────────
│   ├── healer.py                    # Thread daemon: monitorea HP/MP, usa pociones/spells (1,088 líneas)
│   │
│   ├── # ── Loot / inventario ────────────────────────────────────────────
│   ├── looter.py                    # Thread loot: detecta cadáveres, abre, recoge items (1,220 líneas)
│   ├── inventory_manager.py         # Detecta slots libres, peso, estado inventario
│   │
│   ├── # ── Depot / NPC trade ────────────────────────────────────────────
│   ├── depot_manager.py             # Ciclo depósito: abrir locker, depositar loot
│   ├── depot_orchestrator.py        # Orquesta: ir a depot → depositar → banco → comprar → volver
│   ├── trade_manager.py             # NPC trade: compra automática de suministros
│   │
│   ├── # ── Seguridad / anti-detección ───────────────────────────────────
│   ├── break_scheduler.py           # Programa descansos automáticos (anti-bot detection)
│   ├── anti_kick.py                 # Previene kick por inactividad (mueve mouse/cámara)
│   ├── chat_responder.py            # Responde mensajes de chat predefinidos
│   ├── soak_monitor.py              # Monitorea consumo de recursos anómalos
│   │
│   ├── # ── Recovery / errores ───────────────────────────────────────────
│   ├── death_handler.py             # Maneja muerte del personaje (e3): respawn, re-equip
│   ├── reconnect_handler.py         # Detecta desconexión y reconecta automáticamente
│   ├── preflight.py                 # Verificaciones pre-inicio: ventana visible, config válida
│   │
│   ├── # ── Infraestructura ──────────────────────────────────────────────
│   ├── event_bus.py                 # Pub/sub sincrónico thread-safe (e1–e32)
│   ├── alert_system.py              # Sistema de alertas: sound, popup, webhook
│   ├── telemetry.py                 # Métricas de rendimiento (fps, latencia, errores)
│   ├── dashboard_server.py          # HTTP server con stats en tiempo real (FastAPI/Flask)
│   ├── monitor_gui.py               # GUI tkinter/PyQt para monitorear el bot
│   ├── visualizer.py                # Overlay OpenCV con toda la info debug sobre el frame
│   ├── game_data.py                 # Constantes del juego: tiles, colores, coords de ciudad
│   ├── config_paths.py              # Resuelve rutas de archivos config (raíz del proyecto)
│   ├── detector_config.py           # Dataclass + loader para detector_config.json
│   │
│   └── # ── Calibración ──────────────────────────────────────────────────
│       ├── calibrator.py            # Herramienta interactiva OpenCV para capturar ROIs con ratón
│       └── minimap_calibrator.py    # Calibración específica del minimapa (paleta + escala)
│
├── tools/                           # ~25 herramientas de diagnóstico y mantenimiento
│   ├── verify_roi.py                # Valida ROIs de config JSONs con overlay visual
│   ├── record_route.py              # Graba una ruta jugando manualmente
│   ├── run_route.py                 # Ejecuta una ruta .in sin sesión completa
│   ├── route_validator.py           # Valida sintaxis de archivos .in
│   ├── convert_cloudbot.py          # Convierte scripts de CloudBot al formato .in
│   ├── capture_templates.py         # Captura templates de monstruos/items para OpenCV
│   ├── capture_missing_templates.py # Detecta qué templates faltan y captura
│   ├── download_templates.py        # Descarga templates desde tibiawiki
│   ├── download_corpses.py          # Descarga sprites de cadáveres
│   ├── validate_templates.py        # Verifica que todos los templates son válidos
│   ├── fix_blank_templates.py       # Detecta y elimina templates en blanco/corruptos
│   ├── generate_synthetic_templates.py  # Genera templates sintéticos para test
│   ├── preflight_check.py           # Verifica entorno antes de lanzar el bot
│   ├── release_check.py             # Check completo para release (tests + validaciones)
│   ├── live_test_runner.py          # Runner para tests funcionales en campo
│   ├── benchmark_latency.py         # Mide latencia de captura + procesamiento
│   ├── debug_radar.py               # Debug del minimap radar en tiempo real
│   ├── quick_radar_check.py         # Verificación rápida de coordenadas OCR
│   ├── monitor_focus.py             # Monitorea si la ventana Tibia pierde foco
│   ├── analyze_hpmp_bars.py         # Analiza píxeles de barras HP/MP para calibrar
│   ├── analyze_border.py            # Analiza bordes del viewport Tibia
│   ├── find_bar_boundaries.py       # Detecta automáticamente límites de barras HP/MP
│   ├── find_mp_gaps.py              # Detecta gaps en barra MP (vacíos entre segmentos)
│   ├── extract_anchors.py           # Extrae anchor points del minimapa para calibración
│   ├── extract_full_palette.py      # Extrae paleta de colores del minimapa
│   ├── extract_login_template.py    # Extrae template de pantalla de login/reconexión
│   ├── extract_ocr_texts.py         # Extrae muestras de texto para calibrar OCR
│   ├── compare_mask_vs_walked.py    # Compara máscara de walkability vs ruta grabada
│   └── verify_minimap_palette.py    # Verifica que la paleta del minimapa es correcta
│
├── examples/                        # ~25 demos y diagnósticos
│   ├── session_demo.py              # Demo completo de sesión de caza
│   ├── auto_walker.py               # Demo de waypoint walking autónomo
│   ├── basic_navigation.py          # Demo de A* + movimiento básico
│   ├── multi_waypoint_route.py      # Demo de ruta con múltiples waypoints
│   ├── healer_demo.py               # Demo del healer en aislamiento
│   ├── looter_demo.py               # Demo del looter en aislamiento
│   ├── deposit_demo.py              # Demo del ciclo de depósito
│   ├── script_runner.py             # Ejecuta un script .in con log verboso
│   ├── city_routes.py               # Rutas precargadas de ciudades de Tibia
│   ├── live_tracker.py              # Overlay en tiempo real de lo que ve el bot
│   ├── run_monitor.py               # Monitor de stats de sesión en consola
│   ├── obs_demo.py                  # Demo de captura OBS
│   ├── obs_waypoint_tracker.py      # Tracker de waypoints vía OBS
│   ├── capture_templates.py         # Demo de captura de templates
│   ├── download_monster_templates.py # Descarga templates de monstruos específicos
│   ├── diag_inputs.py               # Diagnóstico de inputs (envía teclas de prueba)
│   ├── diag_hpmp.py                 # Diagnóstico de lectura HP/MP en tiempo real
│   ├── input_bridge.py              # Bridge de input para pruebas remotas
│   ├── scan_roi.py                  # Auto-detecta ROI de coordenadas OCR (contours + EasyOCR)
│   ├── minimap_sim.py               # Simula minimapa para tests offline
│   ├── export_route_json.py         # Exporta rutas .in a formato JSON
│   ├── waypoint_search.py           # Busca waypoints en archivos .in
│   └── _clean_output.py             # Limpia archivos de output temporales
│
├── tests/                           # ~120 archivos de test, ~3,912 tests pasando
│   ├── conftest.py                  # Fixtures globales pytest
│   ├── test_session.py / test_session_*.py  # Tests del orquestador (8 archivos)
│   ├── test_combat.py / test_combat_*.py    # Tests de combat_manager (4 archivos)
│   ├── test_healer.py / test_healer_*.py    # Tests del healer
│   ├── test_looter.py / test_looter_*.py    # Tests del looter
│   ├── test_hpmp.py / test_hpmp_*.py        # Tests del hpmp_detector
│   ├── test_navigator.py / test_navigator_*.py  # Tests de A* y navegación
│   ├── test_script_executor.py / *_combat   # Tests del script executor
│   ├── test_input_controller.py / test_input_*  # Tests de input (3 archivos)
│   ├── test_depot_manager.py / test_depot_* # Tests de depot (3 archivos)
│   ├── test_event_bus.py            # Tests del EventBus
│   ├── test_minimap_radar.py        # Tests del minimap radar
│   ├── test_frame_capture.py / *_coverage   # Tests de captura
│   ├── test_break_scheduler.py      # Tests del break scheduler
│   ├── test_death_reconnect.py      # Tests de death handler + reconnect
│   ├── test_e2e_offline.py          # Tests end-to-end sin hardware
│   └── ... (120 archivos en total)
│
├── human_input_system/              # Paquete de abstracción de input hardware
│   └── __init__.py                  # Expone: InputBridge, HardwareInput, HIDDevice
│
├── pico2/                           # Firmware RP2040 (Raspberry Pi Pico 2) para HID físico
│   ├── boot.py                      # USB HID composite device setup
│   └── code.py                      # Implementación de teclado/ratón HID via serial
│
├── routes/                          # Scripts .in de rutas (no listados aquí)
│
├── # Scripts de test en campo (raíz)
├── run_nivel0_tests.py              # Nivel 0: tests sin hardware
├── run_nivel1_tests.py              # Nivel 1: captura y visión
├── run_nivel2_tests.py              # Nivel 2: input y navegación
├── run_nivel3_tests.py              # Nivel 3: healer y combat
├── run_nivel4_tests.py              # Nivel 4: looter e inventario
├── run_nivel5_tests.py              # Nivel 5: depot y trade
├── run_nivel6_tests.py              # Nivel 6: recovery y seguridad
├── run_nivel7_tests.py              # Nivel 7: sesión completa integración
├── run_phase1.py / run_phase2.py / run_phase3.py  # Runners por fase
├── run_depot_test.py                # Test específico de ciclo depot
├── run_check.py                     # Health-check rápido del entorno
│
├── # Scripts de debug (raíz)
├── debug_all_rois.py                # Dibuja todos los ROIs de config JSONs sobre captura
├── debug_capture.py                 # Debug de captura de pantalla en tiempo real
├── debug_walker.py                  # Debug del walker A* paso a paso
└── calibrate_viewport.py            # Calibración rápida del viewport del juego


---

## SISTEMA DE EVENTOS (EventBus)

El EventBus usa códigos e1–e32 + nombres descriptivos:

| Código | Nombre | Descripción |
|--------|--------|-------------|
| e1 | kill | Monstruo confirmado muerto |
| e2 | loot | Cadáver looteado |
| e3 | death | Muerte del personaje |
| e4 | heal | Curación ejecutada |
| e5 | spell | Spell de ataque lanzado |
| e6 | mana | Poción de mana usada |
| e15 | gm_detected | GM / chat sospechoso detectado |
| e18 | pvp_detected | Jugador hostil / skull detectado |
| e19 | inventory_full | Inventario lleno |
| e20 | inventory_check | Check periódico de inventario |
| e25 | combat_flee | Huyendo de combate (flee mode) |
| e26 | combat_lure | Atrayendo monstruo (lure mode) |
| e27 | spawn_start | Inicio de nueva oleada de spawn |
| e28 | spawn_clear | Spawn limpiado |
| e30 | stuck_minor | Stuck leve (1-2 intentos) |
| e31 | stuck_major | Stuck severo (sidestep/reroute) |
| e32 | stuck_critical | Stuck crítico (bot pausado) |

---

## ROIs CALIBRADOS (monitor 2 = 1920×1080, Tibia 1456×816)

Todos los ROIs se almacenan en espacio de referencia 1920×1080 y se escalan automáticamente.

```json
hpmp_config.json:
  hp_roi:       [12, 28, 700, 10]
  mp_roi:       [780, 28, 700, 10]
  hp_text_roi:  [300, 42, 80, 18]
  mp_text_roi:  [1060, 42, 80, 18]

combat_config.json:
  battle_list_roi: [1530, 340, 190, 200]

minimap_config.json:
  roi: [1735, 25, 130, 130]

loot_config.json:
  viewport_roi:   [230, 60, 960, 540]
  container_roi:  [1610, 430, 220, 205]
```

---

## BUGS CORREGIDOS (2026-03-30)

| # | Archivo | Severidad | Descripción |
|---|---------|-----------|-------------|
| BUG-01 | combat_manager.py:428 | MEDIO | debug_save usaba detect() en vez de detect_auto() en modo OCR |
| BUG-02 | combat_manager.py:849 | BAJO | Jitter de spell cooldown asimétrico → ahora ±125ms simétrico |
| BUG-03 | looter.py:1158/1169 | ALTO | frame2/frame3 sin try/except → loop infinito si frame_getter lanza |
| BUG-04 | hpmp_detector.py:197 | BAJO | _resolution_warned no declarado en __init__ |
| BUG-05 | hpmp_detector.py:521 | BAJO | Blank line faltante entre propiedades (PEP 8) |
| BUG-06 | break_scheduler.py:172 | ALTO | Sleep ininterrumpible hasta 10h → threading.Event + abort_break() |
| BUG-07 | looter.py:683 | MEDIO | reset_stats() sin lock → race condition con hilo de loot |
| BUG-08 | healer.py:validate | MEDIO | cooldown_jitter sin cota superior → multiplier negativo → spam |
| BUG-09 | depot_orchestrator.py:423 | MEDIO | _frame_getter no inicializado en __init__ → AttributeError |
| BUG-10 | input_controller.py:1027 | BAJO | _press_two_keys no reseteaba _consecutive_failures en éxito |
| BUG-11 | event_bus.py:151 | BAJO | _handler_errors += 1 fuera del lock → race condition |

---

## MÓDULOS CORE (por tamaño)

| Módulo | Líneas | Responsabilidad |
|--------|--------|-----------------|
| session.py | 2,847 | Orquestador principal de sesión |
| script_executor.py | 1,989 | Ejecución de scripts .in |
| input_controller.py | 1,397 | Inputs hardware-level SendInput |
| minimap_radar.py | 1,149 | Posición por template matching |
| hpmp_detector.py | 1,108 | Lectura HP/MP por color + OCR |
| healer.py | 1,088 | Thread de curación autónoma |
| combat_manager.py | 1,160 | Gestión de combate y spells |
| looter.py | 1,220 | Thread de loot autónomo |

---

## ESTADO DE TESTS (2026-03-30)

- 3,912 tests pasando
- 120+ archivos de test en tests/
- 63 de 65 módulos cubiertos
- LIVE_TEST_PLAN_v3.md — niveles 1-7 todos PASS (165/165 casos de campo)

Tests de campo completados:
- P-INP-01/02: input controller
- P-VIS-01/02/05-08: visión (HP/MP, minimap, coordenadas)
- P-NAV-01/04-08: navegación
- P-HP-01/02: healer (21/21 PASS)
- P-CMB-01/02/03/04: combat, GM detector, PVP detector, condition monitor
- P-LOOT-01-05: looter, inventory, depot, trade, depot orchestrator
- P-REC-01-07: death handler, reconnect, anti-kick, break scheduler, chat responder, waypoint logger, recorder

Pendiente (tests funcionales reales en campo):
- Combat real con mobs
- Healer con HP bajo real
- Depot ciclo real con locker y NPC
- Death recovery con muerte real

---

## INPUT HARDWARE

El proyecto soporta 3 métodos de input (seleccionable en config):

1. **SendInput** (default) — ctypes directo, hardware-level, Windows only
2. **human_input_system** — paquete propio con varianza humana (Bézier para ratón)
3. **Pico2 HID** — Raspberry Pi Pico 2 conectado por USB actuando como teclado/ratón físico real (máxima seguridad anti-detección)

---

## REGLAS DEL PROYECTO

1. NO leer memoria del proceso Tibia (BattlEye bannea)
2. NO inyectar paquetes de red
3. NO usar pyautogui
4. TODO por análisis de imagen + SendInput / HID físico
5. Type hints en todos los métodos
6. Thread-safety donde sea necesario (threading.Lock, threading.Event)
7. Logging con módulo logging (nunca print)
8. Degradación graceful si algún módulo no está disponible
9. ROIs siempre en espacio de referencia 1920×1080, escalados en lectura
10. Cada módulo tiene su propio JSON de configuración con validate() + load()
```
