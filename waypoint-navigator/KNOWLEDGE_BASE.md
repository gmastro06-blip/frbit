# Knowledge Base: Bot Tibia (frbit) — Guía Definitiva v2

> Base de conocimiento completa para construir, calibrar y operar un bot Tibia 100% funcional.
> Vision-only · BattlEye-safe · Sin lectura de memoria · Sin inyección de paquetes.
>
> _Última actualización: 2026-03-23 — Personaje activo: Aelzerand Neeymas, Knight, Thais surface_

---

## ÍNDICE

**PARTE 1 — FUNDAMENTOS**
1. [Principios de Diseño](#1-principios-de-diseño)
2. [Entorno y Setup](#2-entorno-y-setup)
3. [Stack Tecnológico](#3-stack-tecnológico)

**PARTE 2 — ARQUITECTURA**
4. [Arquitectura en Capas](#4-arquitectura-en-capas)
5. [Patrón de Módulo Estándar](#5-patrón-de-módulo-estándar)
6. [EventBus: Comunicación entre Módulos](#6-eventbus)
7. [Sistema de Configuración](#7-sistema-de-configuración)

**PARTE 3 — MÓDULOS CORE**
8. [Frame Capture](#8-frame-capture)
9. [Input Controller](#9-input-controller)
10. [HP/MP Detector](#10-hpmp-detector)
11. [AutoHealer](#11-autohealer)
12. [CombatManager](#12-combatmanager)
13. [Looter](#13-looter)
14. [Navigator + Pathfinder](#14-navigator--pathfinder)
15. [PositionResolver + MinimapRadar](#15-positionresolver--minimapradar)

**PARTE 4 — MÓDULOS AVANZADOS**
16. [DepotOrchestrator (Refill)](#16-depotorchestrator)
17. [InventoryManager](#17-inventorymanager)
18. [TradeManager](#18-trademanager)
19. [ConditionMonitor](#19-conditionmonitor)
20. [Human Input System (Arduino/Pico)](#20-human-input-system)

**PARTE 5 — ROBUSTEZ Y SEGURIDAD**
21. [Sistemas de Seguridad](#21-sistemas-de-seguridad)
22. [Humanización Anti-Detección](#22-humanización-anti-detección)
23. [Alert System](#23-alert-system)

**PARTE 6 — TESTING Y DEPLOY**
24. [Estrategia de Tests](#24-estrategia-de-tests)
25. [Pre-flight Check](#25-pre-flight-check)
26. [Diagnóstico y Debug](#26-diagnóstico-y-debug)
27. [Scripts de Producción](#27-scripts-de-producción)

**PARTE 7 — RECETAS**
28. [Setup desde Cero](#28-setup-desde-cero)
29. [Calibrar Nuevo Personaje](#29-calibrar-nuevo-personaje)
30. [Adaptar a Nuevo Spawn](#30-adaptar-a-nuevo-spawn)
31. [Arquitectura Greenfield Recomendada](#31-arquitectura-greenfield)

**APÉNDICE**
- [A. Modelos de Datos](#a-modelos-de-datos)
- [B. VK Codes de Referencia](#b-vk-codes)
- [C. Sistema de Coordenadas](#c-sistema-de-coordenadas)
- [D. Flujos de Ejecución](#d-flujos-de-ejecución)
- [E. Configuraciones de Producción Completas](#e-configuraciones-de-producción)

---

## PARTE 1 — FUNDAMENTOS

---

## 1. Principios de Diseño

| Principio | Implementación | Por qué |
|-----------|----------------|---------|
| **Vision-only** | Solo captura de pantalla (mss). Sin acceso al proceso. | BattlEye detecta cualquier syscall al proceso de Tibia |
| **BattlEye-safe** | Input via WinAPI/hardware HID. Sin inyección de DLLs. | Único método de input seguro verificado en producción |
| **Modularidad** | Cada subsistema: `start()/stop()` independiente | Permite probar módulos individualmente, fácil debugging |
| **Thread-safety** | Cada módulo corre en su propio hilo daemon | Evitar bloqueos entre healer (crítico) y navegación |
| **Graceful degradation** | Fallbacks en cadena para cada detección | El bot no muere por un frame None o una posición perdida |
| **Explicit > implicit** | `frame_source="mss"` explícito evita auto-upgrades | Aprendido en producción: auto-upgrades a dxcam → hang |
| **Configuración externa** | Todo en JSON. Sin hardcoding de coords o VKs. | Permite cambiar spawn/personaje sin tocar código |
| **Humanización** | Jitter en timing, Bezier en mouse, fatigue model | Reduce firma de comportamiento robótico |
| **Observable** | Logging estructurado, EventBus, dashboard web | Permite diagnosticar problemas en producción |

---

## 2. Entorno y Setup

### Hardware y pantallas

```
Monitor 1 (primario, left=0):    VS Code, terminales, herramientas
Monitor 2 (secundario, left=1920): Tibia corriendo aquí
  → mss.monitors[2] = {'left': 1920, 'top': 0, 'width': 1920, 'height': 1080}
```

- OBS Projector en Monitor 2 para supervisión visual en tiempo real
- Tibia: ventana borderless en Monitor 2, posición (1920, 0) en pantalla virtual
- Cliente Tibia en y=0 del monitor 2 → barras de UI en y=29 relativo al monitor

### Python y dependencias

```bash
# Versión requerida
Python 3.12.x (3.12.10 en producción)

# Crear entorno virtual
python -m venv .venv
.venv\Scripts\activate  # PowerShell
source .venv/Scripts/activate  # bash

# Instalar dependencias
pip install -r requirements.txt

# Encoding crítico en Windows (cp1252 por defecto)
python -X utf8 script.py
# O en el entorno:
set PYTHONUTF8=1
```

### Directorios clave

```
waypoint-navigator/
├── src/                # 60+ módulos Python
├── routes/             # JSON de rutas de hunting
├── cache/templates/    # PNGs de templates por categoría
│   ├── monsters/       # Templates de monstruos
│   ├── items/          # Templates de ítems para loot
│   └── ui/             # Templates de elementos UI
├── data/               # monsters.json, spells.json por clase
├── maps/               # PNGs de pisos de tibiamaps.io
├── output/             # Capturas de debug y diagnóstico
├── logs/               # Logs rotativos de sesión
├── human_input_system/ # Sistema HID Arduino/Pico
├── *.json              # Configs por módulo
└── run_phase3.py       # Script de producción activo
```

---

## 3. Stack Tecnológico

| Categoría | Librería | Versión | Uso |
|-----------|----------|---------|-----|
| **Captura** | `mss` | 9.0+ | Captura de pantalla principal (GDI/BitBlt) |
| **Visión** | `opencv-python` | 4.9+ | Template matching, procesamiento de imágenes |
| **Visión** | `numpy` | 1.26+ | Arrays de frames, operaciones vectorizadas |
| **OCR** | `easyocr` | 1.7+ | Lectura de texto en battle list |
| **Input** | `pywin32` | 306+ | WinAPI: SendInput, FindWindow, SetForeground |
| **Input** | ctypes | stdlib | keybd_event, mouse_event sin dependencias |
| **Serial** | `pyserial` | 3.5+ | Comunicación con Arduino/Pico HID |
| **Config** | `PyYAML` | 6.0+ | Configs opcionales en YAML |
| **HTTP** | `aiohttp` | 3.9+ | Dashboard web async |
| **Tests** | `pytest` | 8.x | Framework de tests |
| **Tests** | `pytest-cov` | 4.x | Coverage de tests |
| **Análisis** | `pillow` | 10.x | Manipulación de imágenes |

### Por qué NO estas librerías

| Librería | Razón para evitar |
|----------|-------------------|
| `dxcam` | DXGI OutputDuplication — hang si proceso previo fue force-killed |
| `pyautogui` | Detectable, lento, no thread-safe |
| `keyboard` | Global hooks detectables por BattlEye |
| `pynput` | Mismo problema que keyboard |
| `win32api.SendMessage` | PostMessage WM_KEYDOWN — muy detectable |
| `Interception driver` | No mueve el personaje correctamente en producción |

---

## PARTE 2 — ARQUITECTURA

---

## 4. Arquitectura en Capas

```
┌──────────────────────────────────────────────────────────────────┐
│                         BotSession                                │
│                    (orquestador central)                          │
│         SessionConfig → inicia, conecta y supervisa todo          │
└─────────────────────────────┬────────────────────────────────────┘
                              │ EventBus (pub/sub)
     ┌────────────────────────┼──────────────────────┐
     │                        │                      │
┌────▼───────┐      ┌─────────▼──────┐      ┌───────▼───────┐
│   CAPA 1   │      │    CAPA 2      │      │    CAPA 3     │
│   DATOS    │      │   DECISIÓN     │      │   ACCIÓN      │
│            │      │                │      │               │
│FrameCapture│      │  AutoHealer    │      │InputController│
│FrameCache  │      │  CombatManager │      │  Humanizer    │
│HpMpDetector│      │  Looter        │      │  MouseBezier  │
│MinimapRadar│      │  Navigator     │      │               │
│ConditionMon│      │  DepotOrchest. │      └───────────────┘
└────────────┘      └────────────────┘
     │                        │
     └────────────────────────┘
              │
     ┌────────▼────────────────────────────────────┐
     │                CAPA 4: ROBUSTEZ              │
     │                                              │
     │  StuckDetector  DeathHandler  AntiKick        │
     │  ReconnectHandler  PvPDetector  GMDetector    │
     │  BreakScheduler  SessionPersistence           │
     └──────────────────────────────────────────────┘
```

### Cómo fluye la información

```
1. mss captura frame cada ~50ms → FrameCache
2. Subsistemas leen frame via frame_getter()
3. HpMpDetector → [hp_pct, mp_pct] → AutoHealer decide
4. CombatManager → detecta monstruos → emite "kill" → Looter actúa
5. MinimapRadar → posición → Navigator calcula A*
6. Navigator → dirección → InputController → tecla de movimiento
7. Todos los eventos importantes → EventBus → suscriptores reaccionan
```

---

## 5. Patrón de Módulo Estándar

**Todos los módulos siguen este patrón exacto:**

```python
import threading
import logging
from typing import Callable, Optional

log = logging.getLogger(__name__)

class MiModulo:
    def __init__(self, ctrl, config):
        self._ctrl = ctrl
        self._config = config
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._frame_getter: Optional[Callable] = None

    # ── API pública ──────────────────────────────────
    def set_frame_getter(self, fn: Callable):
        """Inyecta la función que devuelve el frame actual."""
        self._frame_getter = fn

    def start(self):
        """Inicia el hilo daemon del módulo."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name=f"{self.__class__.__name__}"
        )
        self._thread.start()
        log.info("[%s] Iniciado", self.__class__.__name__)

    def stop(self):
        """Detiene el módulo esperando hasta 3 segundos."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        log.info("[%s] Detenido", self.__class__.__name__)

    # ── Loop interno ──────────────────────────────────
    def _loop(self):
        while not self._stop_event.is_set():
            try:
                frame = self._frame_getter() if self._frame_getter else None
                if frame is not None:
                    self._tick(frame)
            except Exception as e:
                log.warning("[%s] Error en tick: %s", self.__class__.__name__, e)
            self._stop_event.wait(self._config.check_interval)

    def _tick(self, frame):
        """Lógica principal — implementar en subclase."""
        raise NotImplementedError
```

**Reglas del patrón:**
1. `set_frame_getter(fn)` — nunca guardar el frame directamente, siempre una función
2. `threading.Event` para stop — nunca `time.sleep()` sin `wait()`
3. `try/except` en el loop — un error no mata el hilo
4. `daemon=True` — el proceso termina aunque el hilo no se haya detenido
5. `join(timeout=3)` — no esperar indefinidamente al detener

---

## 6. EventBus

**Archivo**: `src/event_bus.py`

El EventBus implementa pub/sub sincrónico. Permite que módulos se comuniquen sin referencias directas.

```python
bus = EventBus()

# Suscribirse a eventos
bus.subscribe("kill", lambda data: looter.notify_kill(data["coord"]))
bus.subscribe("player_died", lambda _: session.pause())
bus.subscribe("stuck_abort", lambda _: session.stop())

# Emitir eventos
bus.emit("kill",    {"name": "Cave Rat", "coord": Coordinate(x,y,z)})
bus.emit("heal",    {"hp_pct": 45.0})
bus.emit("mana",    {"mp_pct": 20.0})
```

### Eventos estándar del sistema

| Evento | Payload | Emisor | Suscriptores |
|--------|---------|--------|-------------|
| `"kill"` | `{"name": str, "coord": Coordinate}` | CombatManager | Looter |
| `"heal"` | `{"hp_pct": float}` | AutoHealer | Dashboard, Stats |
| `"mana"` | `{"mp_pct": float}` | AutoHealer | Dashboard, Stats |
| `"condition"` | `{"condition": str}` | ConditionMonitor | AutoHealer |
| `"condition_clear"` | `{"condition": str}` | ConditionMonitor | - |
| `"mob_detected"` | `{"name": str, "count": int}` | CombatManager | Navigator (pausa) |
| `"loot_done"` | `{"items": int}` | Looter | Navigator (reanuda), Stats |
| `"depot_done"` | `{"items": int, "cycle": int}` | DepotOrchestrator | Stats |
| `"route_done"` | `{"cycle": int}` | Navigator | DepotOrchestrator |
| `"player_died"` | `{}` | DeathHandler | Session (pausa), Stats |
| `"player_stuck"` | `{"duration": float}` | StuckDetector | Session |
| `"stuck_abort"` | `{}` | StuckDetector | Session (stop) |
| `"gm_detected"` | `{}` | GMDetector | Session (pausa/logout) |
| `"pvp_skull"` | `{"action": str}` | PvPDetector | Session |
| `"resupply_needed"` | `{"reason": str}` | InventoryManager | DepotOrchestrator |
| `"resupply_done"` | `{"reason": str}` | DepotOrchestrator | Stats |

---

## 7. Sistema de Configuración

### Principio: Un JSON por módulo

```
waypoint-navigator/
├── session_config.json    # BotSession — parámetros globales
├── hpmp_config.json       # HpMpDetector — ROIs de barras HP/MP
├── heal_config.json       # AutoHealer — thresholds y hotkeys de curación
├── combat_config.json     # CombatManager — monstruos, spells, battle_list ROI
├── loot_config.json       # Looter — whitelist ítems, container ROI
├── depot_config.json      # DepotManager — coords depot, modo depósito
├── inventory_config.json  # InventoryManager — ROI inventario, suministros
├── trade_config.json      # TradeManager — NPCs, listas compra/venta
├── minimap_config.json    # MinimapRadar — ROI minimap, floor activo
└── condition_config.json  # ConditionMonitor — iconos condición, remedios
```

### Patrón de carga de config

```python
import json
from dataclasses import dataclass, field
from pathlib import Path

@dataclass
class MiConfig:
    check_interval: float = 0.3
    confidence: float = 0.65
    roi: list = field(default_factory=lambda: [0, 0, 100, 20])

    @classmethod
    def from_file(cls, path: str = "mi_config.json") -> "MiConfig":
        p = Path(path)
        if not p.exists():
            return cls()  # Defaults si no existe el archivo
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return cls(**{k: v for k, v in data.items()
                      if k in cls.__dataclass_fields__})
```

---

## PARTE 3 — MÓDULOS CORE

---

## 8. Frame Capture

**Archivos**: `src/frame_capture.py`, `src/frame_sources.py`, `src/frame_cache.py`

### Backends disponibles

| Backend | Estado | Notas |
|---------|--------|-------|
| `"mss"` | **PRODUCCIÓN** | GDI/BitBlt. Estable, sin hangs. Usar siempre. |
| `"dxcam"` | **EVITAR** | DXGI hang si proceso fue force-killed. |
| `"wgc"` | Funcional | Windows Graphics Capture. Calidad alta pero más lento. |
| `"printwindow"` | Funcional | Captura ventana en background. |
| `"obs"` | Funcional | OBS WebSocket. Para configuraciones OBS. |

### Por qué mss gana siempre

```
dxcam usa DXGI OutputDuplication (handle al GPU frame buffer).
Si el proceso anterior fue force-killed (Ctrl+C en terminal),
el handle DXGI queda "sucio" y la siguiente llamada cam.start()
se bloquea indefinidamente esperando ese handle.

mss usa GDI/BitBlt — sin DXGI, sin handles compartidos, sin hangs.
```

### Implementación mínima de captura

```python
import mss
import numpy as np

def make_frame_getter(monitor_idx: int = 2):
    """Retorna función que captura el monitor indicado."""
    sct = mss.mss()
    mon = sct.monitors[monitor_idx]  # monitor_idx=2 → Tibia en monitor 2
    def get_frame() -> np.ndarray:
        img = sct.grab(mon)
        return np.array(img)[:, :, :3]  # BGRA → BGR (drop alpha)
    return get_frame
```

### FrameCache con TTL

```python
# src/frame_cache.py
import threading, time
import numpy as np

class FrameCache:
    """Buffer thread-safe con TTL para evitar lecturas duplicadas."""
    def __init__(self, ttl_ms: int = 50):
        self._frame = None
        self._ts = 0.0
        self._ttl = ttl_ms / 1000.0
        self._lock = threading.Lock()

    def update(self, frame: np.ndarray):
        with self._lock:
            self._frame = frame
            self._ts = time.monotonic()

    def get(self) -> np.ndarray | None:
        with self._lock:
            if self._frame is None: return None
            if time.monotonic() - self._ts > self._ttl: return None
            return self._frame.copy()
```

### Parámetros de configuración de frame_source

```python
# En SessionConfig — IMPORTANTE: explícito evita auto-upgrade
SessionConfig(
    frame_source="mss",   # Explícito → _src_explicit=True → NO auto-upgrade
    monitor_idx=2,        # mss.monitors[2] = Monitor 2 (Tibia)
    # hwnd: NO pasar cuando frame_source="mss"
    # dxcam+hwnd con region → crash. mss no necesita hwnd.
)
```

### Verificar captura correcta

```bash
python -X utf8 debug_capture.py
# Genera output/debug_frame.png con ROIs dibujados
# Si la imagen es correcta (se ve Tibia) → frame capture OK
```

---

## 9. Input Controller

**Archivo**: `src/input_controller.py`

### Modos de input y seguridad BattlEye

| Modo | Detectabilidad | Estado en producción |
|------|---------------|---------------------|
| `"winapi"` | Bajo riesgo | **FUNCIONA** — Mueve personaje correctamente |
| `"scancode"` | Bajo riesgo | Funcional — Scancodes más realistas que VKs |
| `"postmessage"` | Alto riesgo | Solo background, detectable |
| `"interception"` | Kernel-level | **NO FUNCIONA** — Personaje no se mueve en producción |
| Arduino HID | Indetectable | Funciona — Input hardware real USB |
| Pico 2 HID | Indetectable | Funciona — Alternativa al Arduino |

> **Lección de producción**: Interception driver envía eventos a nivel kernel pero el juego
> no registra movimiento del personaje. Causa desconocida. Usar `"winapi"` en producción.

### API completa del InputController

```python
ctrl = InputController(target_window="Tibia", input_method="winapi")

# Ventana
ctrl.find_window("Tibia")               # Busca HWND por título
ctrl.ensure_focus()                      # Trae ventana al frente

# Teclado
ctrl.press_key(vk=0x70)                 # F1 — press + release
ctrl.press_key(vk=0x70, duration=0.05)  # Con duración específica
ctrl.press_hotkey(vk=0x71)             # Con timing humanizado
ctrl.type_text("hi")                    # Texto carácter a carácter

# Ratón
ctrl.click(x=960, y=540, button="left")  # Click absoluto en pantalla
ctrl.click(x=960, y=540, button="right") # Click derecho
ctrl.move_mouse(x=960, y=540)            # Hover sin click

# Combos
ctrl.shift_click(x=960, y=540)          # Shift+Click (recoger ítem)
```

### Implementación SendInput (ctypes)

```python
import ctypes
from ctypes import wintypes

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG))]

def send_key_down(vk: int):
    ki = KEYBDINPUT(wVk=vk, dwFlags=0)
    # ... construir INPUT struct y llamar SendInput
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

def send_key_up(vk: int):
    ki = KEYBDINPUT(wVk=vk, dwFlags=KEYEVENTF_KEYUP)
    # ...
```

---

## 10. HP/MP Detector

**Archivo**: `src/hpmp_detector.py`

### Método de detección por color

```python
def read_hp_pct(frame: np.ndarray, roi: list) -> float:
    """Lee HP% analizando color verde en la barra de vida."""
    x, y, w, h = roi
    bar = frame[y:y+h, x:x+w]
    # HP verde: G > 120, G-R > 40, G-B > 40
    g = bar[:,:,1].astype(int)
    r = bar[:,:,0].astype(int)
    b = bar[:,:,2].astype(int)
    mask = (g > 120) & ((g - r) > 40) & ((g - b) > 40)
    return float(mask.sum()) / max(mask.size, 1) * 100

def read_mp_pct(frame: np.ndarray, roi: list) -> float:
    """Lee MP% analizando color azul en la barra de mana."""
    x, y, w, h = roi
    bar = frame[y:y+h, x:x+w]
    # MP azul: B > 100, B-R > 40
    b = bar[:,:,2].astype(int)
    r = bar[:,:,0].astype(int)
    mask = (b > 100) & ((b - r) > 40)
    return float(mask.sum()) / max(mask.size, 1) * 100
```

### hpmp_config.json (producción — mss monitor 2)

```json
{
  "hp_roi":      [12,  29, 769, 13],
  "mp_roi":      [788, 29, 768, 13],
  "hp_text_roi": [484, 310, 1376, 20],
  "mp_text_roi": [374, 322, 1486, 20]
}
```

> **Crítico**: Las coords son **relativas al monitor capturado** (no pantalla virtual).
> Con mss+monitor_idx=2: Tibia en y=0 del monitor 2 → barra HP en y=29.

### Historia de ROIs (lección aprendida)

| Configuración | hp_roi.y | Razón |
|---------------|----------|-------|
| WGC window-relative | 29 | Coords relativas a ventana |
| dxcam full-screen monitor 1 | 52 | +23px por título de ventana (Tibia en y=23 de monitor 1) |
| mss monitor 2 (actual) | 29 | Tibia borderless en y=0 del monitor 2 → vuelve al original |

### Verificación visual

```bash
python -X utf8 debug_capture.py
# Abre output/mon2_rois_new.png
# Si hp_bar.mean_BGR ≈ [37, 172, 37] → verde → HP al 100% → ROI correcta
```

---

## 11. AutoHealer

**Archivo**: `src/healer.py`

### Lógica de curación

```
CADA 0.3s:
  hp_pct, mp_pct = hpmp_detector.read(frame)

  # Prioridad 1: HP emergencia
  if hp_pct < emergency_pct AND cooldown_ok:
    press(emergency_vk)       # F3 → exura gran / exura vita

  # Prioridad 2: HP normal
  elif hp_pct < heal_pct AND cooldown_ok:
    press(heal_vk)            # F1 → exura / health potion

  # Prioridad 3: MP
  if mp_pct < mana_pct AND cooldown_ok:
    press(mana_vk)            # F2 → mana potion

  # Opcional: Utamo (buff escudo knight)
  if utamo_enabled AND tiempo_desde_utamo > utamo_duration:
    press(utamo_vk)
```

### heal_config.json

```json
{
  "hp_threshold_pct": 70,
  "hp_emergency_pct": 30,
  "mp_threshold_pct": 30,
  "heal_hotkey_vk": 112,
  "emergency_hotkey_vk": 114,
  "mana_hotkey_vk": 113,
  "heal_cooldown": 2.0,
  "emergency_cooldown": 1.0,
  "mana_cooldown": 1.5,
  "check_interval": 0.3,
  "utamo_hotkey_vk": 0,
  "utamo_cooldown": 60.0,
  "haste_hotkey_vk": 0,
  "haste_cooldown": 45.0
}
```

### VK hotkeys de curación por clase

| Clase | F1 | F2 | F3 | F5 | F6 |
|-------|----|----|----|----|-----|
| Knight | exura | strong mana | exura gran | utamo vita | haste |
| Paladin | exura san | strong mana | exura gran san | - | haste |
| Sorcerer | exura | strong mana | exura gran | - | - |
| Druid | exura | strong mana | exura gran | antídoto | - |

---

## 12. CombatManager

**Archivo**: `src/combat_manager.py`

### Pipeline de detección de monstruos

```
1. Capturar frame → recortar battle_list_roi
2. Template matching contra cache/templates/monsters/*.png
   result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
   locs = np.where(result >= config.confidence)
3. Si 0 matches Y ocr_detection=True:
   → EasyOCR/pytesseract sobre la región
   → Buscar nombres en config.monsters[]
4. Si hay monstruo detectado:
   a) Click en posición del primer monstruo → auto-attack
   b) Para cada spell configurado:
      if mp_pct >= spell.min_mp AND cooldown_ok:
        press(spell.vk)
5. Emitir "kill" cuando monstruo desaparece de battle list
```

### NMS (Non-Maximum Suppression) para template matching

```python
import cv2
import numpy as np

def count_templates(frame_roi, template, threshold=0.65):
    """Cuenta instancias de template en ROI con NMS greedy."""
    result = cv2.matchTemplate(frame_roi, template, cv2.TM_CCOEFF_NORMED)
    locs = np.where(result >= threshold)
    boxes = [(x, y, template.shape[1], template.shape[0])
             for y, x in zip(*locs)]

    boxes.sort(key=lambda b: result[b[1], b[0]], reverse=True)
    kept = []
    for box in boxes:
        if not any(_iou(box, k) > 0.3 for k in kept):
            kept.append(box)
    return len(kept), kept

def _iou(a, b):
    """Intersection over Union para dos boxes (x,y,w,h)."""
    ax1, ay1 = a[0], a[1]
    ax2, ay2 = a[0]+a[2], a[1]+a[3]
    bx1, by1 = b[0], b[1]
    bx2, by2 = b[0]+b[2], b[1]+b[3]
    ix = max(0, min(ax2,bx2) - max(ax1,bx1))
    iy = max(0, min(ay2,by2) - max(ay1,by1))
    inter = ix * iy
    union = a[2]*a[3] + b[2]*b[3] - inter
    return inter / max(union, 1)
```

### combat_config.json (Aelzerand — Knight Thais)

```json
{
  "battle_list_roi": [1699, 480, 210, 400],
  "monsters": ["Cave Rat", "Wasp", "Poison Spider", "Bug", "Spider"],
  "spells": [
    {"vk": 118, "min_mp": 20, "cooldown": 2.5, "label": "F7"},
    {"vk": 119, "min_mp": 30, "cooldown": 4.0, "label": "F8"},
    {"vk": 120, "min_mp": 50, "cooldown": 6.0, "label": "F9"},
    {"vk": 121, "min_mp": 60, "cooldown": 8.0, "label": "F10"}
  ],
  "ocr_detection": true,
  "ocr_confidence": 0.3,
  "confidence": 0.65,
  "check_interval": 0.3,
  "hp_flee_pct": 25,
  "aoe_mob_threshold": 3,
  "flee_mob_count": 5
}
```

---

## 13. Looter

**Archivo**: `src/looter.py`

### Flujo completo de loot

```
CombatManager emite "kill" → {name: str, coord: Coordinate}
  ↓
Looter.notify_kill(corpse_coord)
  ↓
_loot_in_progress.set()          ← Navigator.walk() pausa aquí
  ↓
Esperar player_coord <= 2 tiles del cadáver
  ↓
Calcular posición en pantalla:
  px = center_x + (corpse.x - player.x) * tile_px
  py = center_y + (corpse.y - player.y) * tile_px
  ↓
Si use_hotkey_quick_loot=True:
  → ctrl.press_key(quick_loot_vk)     (Alt+Q por defecto)
Sino:
  → ctrl.click(px, py, "right")
  → Esperar menú contextual (~0.5s)
  → ctrl.click(px, py + menu_offset)  ("Open")
  → Esperar container abierto (~1.5s)
  → Template matching de ítems en whitelist
  → ctrl.shift_click(item_pos)  para cada ítem encontrado
  ↓
Máximo max_attempts intentos antes de abortar
  ↓
_loot_in_progress.clear()          ← Navigator.walk() reanuda
```

### Coordinación con el Walker

```python
# En Looter:
self._loot_in_progress = threading.Event()

def notify_kill(self, coord):
    self._loot_in_progress.set()
    # ... hacer loot ...
    self._loot_in_progress.clear()

# En Navigator / session walk loop:
if self._loot_in_progress.is_set():
    self._loot_in_progress.wait()  # Espera hasta que Looter termine
```

> **Lección crítica**: Si el walker NO pausa durante loot, el personaje se aleja del cadáver
> haciendo imposible el loot. `threading.Event` es el mecanismo correcto.

### loot_config.json

```json
{
  "viewport_roi": [0, 0, 1920, 1080],
  "tile_size_px": 32,
  "container_roi": [1600, 600, 320, 400],
  "context_menu_offset_y": 18,
  "loot_mode": "whitelist",
  "loot_whitelist": ["gold_coin", "spider_fangs", "meat"],
  "confidence": 0.60,
  "click_delay": 0.10,
  "open_container_delay": 1.5,
  "max_attempts": 5,
  "use_hotkey_quick_loot": true,
  "quick_loot_vk": 18
}
```

> `Alt+Q` (VK=18 es Alt, pero en el código se usa 0x12 para ALT) con quick loot habilitado
> en el cliente Tibia es **más confiable** que template matching de ítems.
> `quick_loot_vk: 18` corresponde a Alt en combinación con Q.

---

## 14. Navigator + Pathfinder

**Archivos**: `src/navigator.py`, `src/pathfinder.py`, `src/map_loader.py`, `src/transitions.py`

### TibiaMapLoader — Carga de mapas

```python
# Descarga automática desde tibiamaps.io (solo primera vez)
loader = TibiaMapLoader(cache_dir="maps/")
walkability = loader.get_walkability(floor=7)  # np.ndarray bool 2D
# Pixel (px, py) en el array = walkable si True
# Coordenada Tibia (x,y) → pixel: (x-31744, y-30976)
```

### AStarPathfinder

```python
pathfinder = AStarPathfinder(
    walkability=walkability,
    max_nodes=2_000_000,
    allow_diagonal=False,   # Solo 4 dirs (teclado arrow keys)
    path_jitter=0.15        # Variación ±15% para rutas más humanas
)

route = pathfinder.find_path(start_coord, end_coord)
# route.steps    = [Coordinate, ...]  secuencia de tiles
# route.found    = bool
# route.distance = float
```

### Formato de ruta JSON

```json
{
  "name": "Thais Rat Hunt",
  "start": {"x": 32369, "y": 32241, "z": 7},
  "waypoints": [
    {"x": 32369, "y": 32241, "z": 7, "label": "start"},
    {"x": 32371, "y": 32243, "z": 7},
    {"x": 32374, "y": 32245, "z": 7, "label": "spawn1"},
    {"x": 32370, "y": 32248, "z": 7},
    {"x": 32369, "y": 32241, "z": 7, "label": "back_to_start"}
  ],
  "loop": true,
  "transitions": []
}
```

### Transiciones de piso

```json
{
  "transitions": [
    {
      "entry": {"x": 32369, "y": 32241, "z": 7},
      "exit":  {"x": 32369, "y": 32241, "z": 8},
      "kind":  "rope"
    }
  ]
}
```

Tipos de transición: `"walk"` | `"use"` | `"rope"` | `"shovel"` | `"ladder"`

### Cómo el Navigator ejecuta un step

```
1. Obtener posición actual (MinimapRadar)
2. A* desde posición → siguiente waypoint
3. Para cada step en route.steps:
   a) Calcular dirección: dx/dy entre tiles consecutivos → arrow key VK
   b) press(arrow_vk)
   c) jittered_sleep(step_interval, jitter_pct)
   d) Si _loot_in_progress.is_set(): wait hasta clear()
   e) Cada N steps: verificar posición real vs esperada (recalibrar si drift > threshold)
```

### Dirección → VK mapping

```python
DIRECTION_VK = {
    ( 0, -1): 0x26,  # UP (Norte)
    ( 0,  1): 0x28,  # DOWN (Sur)
    (-1,  0): 0x25,  # LEFT (Oeste)
    ( 1,  0): 0x27,  # RIGHT (Este)
}
# Tibia solo 4 direcciones con teclas de flecha
```

### Blocked tiles — problema conocido

```
Si un NPC/jugador bloquea tiles temporalmente:
  → A* marca esos tiles como blocked_tiles
  → blocked_tiles solo se limpian al REINICIAR el bot
  → Si el obstáculo era temporal, las rutas pueden degradarse

Workaround: Reiniciar el bot limpia blocked_tiles.
Solución a futuro: _decay_used debería limpiar tiles periódicamente.
```

---

## 15. PositionResolver + MinimapRadar

**Archivos**: `src/position_resolver.py`, `src/minimap_radar.py`

### Cadena de fallback de posición

```
MinimapRadar (template matching minimap vs PNG de tibiamaps.io)
    ↓ si confidence < threshold o match fallido
CoordinateOCR (EasyOCR sobre texto de coordenadas en pantalla)
    ↓ si OCR falla 3 veces seguidas
LastKnown (última posición válida — puede derivar si está mucho tiempo)
```

### Cómo funciona MinimapRadar

```
1. Recortar minimap_roi del frame
2. Detectar marcador blanco del personaje (círculo ~5px en centro)
3. Enmascarar el marcador (evitar que confunda el template matching)
4. Template matching: minimap_crop vs floor-07-map.png (tibiamaps.io, escala 4x)
5. Mejor match → offset en píxeles → convertir a Coordinate:
   x = (px_offset / 4) + 31744
   y = (py_offset / 4) + 30976
```

### minimap_config.json

```json
{
  "roi": [1725, 25, 186, 120],
  "floor": 7,
  "confidence": 0.35,
  "mask_center": true,
  "scale_factors": [1.0, 0.9, 1.1],
  "temporal_smoothing": 1
}
```

> ROI del minimapa: esquina superior derecha de la UI de Tibia.
> `confidence: 0.35` — bajo porque el minimapa tiene poco contraste con el mapa de fondo.
> `scale_factors` — probar 3 escalas para manejar zoom del minimapa.

### world_to_screen — coordenadas de mapa a pantalla

```python
def world_to_screen(world: Coordinate, player: Coordinate,
                    center_px: tuple, tile_px: int) -> tuple:
    """Coordenada Tibia → posición en pantalla (píxeles)."""
    dx = world.x - player.x
    dy = world.y - player.y
    sx = center_px[0] + dx * tile_px
    sy = center_px[1] + dy * tile_px
    return (int(sx), int(sy))
```

---

## PARTE 4 — MÓDULOS AVANZADOS

---

## 16. DepotOrchestrator

**Archivos**: `src/depot_orchestrator.py`, `src/depot_manager.py`

### Cuándo activar resupply

```python
def should_resupply(self) -> bool:
    # Throttle: no chequear más de 1 vez por minuto
    if time.time() - self._last_check < 60: return False
    # Límite de ciclos por sesión
    if self._cycle_count >= self._config.max_resupply_per_session:
        return False
    return self._inventory_mgr.needs_depot()
```

### Flujo completo de resupply

```
1. Guardar return_pos = posición actual
2. navigator.go_to(depot_coord)
3. DepotManager.run_depot_cycle():
   a) Navegar a chest coord (depot_chest_coord)
   b) ctrl.click(chest_pos, "right") → esperar menú
   c) Si deposit_mode == "stow_all":
      → ctrl.click(stow_all_option_pos)
   d) Si deposit_mode == "shift_click":
      → shift+click en cada slot del backpack
   e) Cerrar containers (Escape o click X)
4. Si bank_withdraw_before_buy:
   → Hablar con NPC banco, retirar gold
5. Si buy_supplies_after_depot:
   → TradeManager.run_cycle()
6. navigator.go_to(return_pos)
7. Reanudar ruta de hunting
8. Emitir "resupply_done"
```

### depot_config.json

```json
{
  "depot_chest_coord": [32352, 32226, 7],
  "deposit_mode": "stow_all",
  "tile_size_px": 75,
  "container_roi": [600, 300, 400, 300],
  "open_wait": 0.8,
  "container_detect_wait": 3.0,
  "abort_on_container_timeout": true,
  "max_items_per_cycle": 20,
  "stow_container_index": 1,
  "bank_withdraw_before_buy": false,
  "buy_supplies_after_depot": true,
  "max_resupply_per_session": 10
}
```

---

## 17. InventoryManager

**Archivo**: `src/inventory_manager.py`

### Detección de inventario lleno

```python
def _detect_fill_ratio(self, frame) -> float:
    """Calcula qué fracción de slots del backpack están ocupados."""
    roi = frame[y:y+h, x:x+w]
    slot_w = roi.shape[1] // cols
    slot_h = roi.shape[0] // rows
    occupied = 0
    for r in range(rows):
        for c in range(cols):
            slot = roi[r*slot_h:(r+1)*slot_h, c*slot_w:(c+1)*slot_w]
            # Slot ocupado: mean > 30 (no negro) AND std > 15 (tiene textura)
            if slot.mean() > 30 and slot.std() > 15:
                occupied += 1
    return occupied / (rows * cols)
```

### Estados de inventario

```python
FULL         = fill_ratio >= 0.95   → trigger depot inmediato
NEARLY_FULL  = fill_ratio >= 0.80   → warning, preparar depot
OK           = fill_ratio < 0.80    → continuar
```

### Detección de pociones (template matching)

```python
# slot_roi = recorte de la celda del slot de pociones
matches = cv2.matchTemplate(slot_roi, potion_template, cv2.TM_CCOEFF_NORMED)
count = count_templates_nms(matches, threshold=config.confidence)

if count == 0:    status = EMPTY    → depot urgente
if count < 10:    status = CRITICAL → depot pronto
if count < 50:    status = LOW      → warning
else:             status = OK
```

### inventory_config.json

```json
{
  "inventory_roi": [1600, 350, 320, 450],
  "capacity_slots": 20,
  "full_threshold": 0.95,
  "nearly_full_threshold": 0.80,
  "check_interval_s": 10.0,
  "supplies": [
    {
      "name": "health_potion",
      "slot_roi": [1610, 400, 32, 32],
      "template": "cache/templates/items/health_potion.png",
      "min_count": 100,
      "low_threshold": 50,
      "critical_threshold": 10
    }
  ]
}
```

---

## 18. TradeManager

**Archivo**: `src/trade_manager.py`

### Flujo de compra de pociones

```
1. Navigator.go_to(npc_coord)
2. ctrl.type_text("hi") + Enter
3. Esperar apertura de menú trade (template matching en window_roi)
4. Para cada item en buy_list:
   a) Si use_search_field:
      → ctrl.click(search_field_pos)
      → ctrl.type_text(item.name)
      → Esperar actualización lista (~0.5s)
   b) ctrl.click(primer resultado en item_list_roi)
   c) ctrl.click(qty_field_roi)  → triple-click para seleccionar todo
   d) ctrl.type_text(str(item.quantity))
   e) ctrl.click(buy_btn_pos)
   f) Esperar confirmación (~1.0s)
5. ctrl.type_text("bye") + Enter
```

### Fallback OCR en find_item()

```python
def find_item(self, frame, item_name: str) -> tuple | None:
    """Busca item en la lista del trade. Primero template, luego OCR."""
    # Intento 1: template matching
    pos = self._match_template(frame, item_name)
    if pos: return pos
    # Intento 2: OCR fallback
    roi = frame[y:y+h, x:x+w]
    text_locs = self._ocr.read_locations(roi)
    for text, loc in text_locs:
        if item_name.lower() in text.lower():
            return loc
    return None
```

### trade_config.json

```json
{
  "window_roi": [610, 280, 700, 500],
  "item_list_roi": [620, 320, 460, 350],
  "qty_field_roi": [900, 693, 120, 28],
  "buy_btn_pos": [943, 736],
  "sell_btn_pos": [1015, 736],
  "search_field_pos": [850, 350],
  "use_search_field": true,
  "confidence": 0.62,
  "greet_delay": 1.2,
  "click_delay": 0.15,
  "buy_list": [
    {"name": "strong health potion", "quantity": 200},
    {"name": "strong mana potion", "quantity": 100}
  ],
  "sell_list": []
}
```

---

## 19. ConditionMonitor

**Archivo**: `src/condition_monitor.py`

### Detección por color (HSV)

```python
import cv2
import numpy as np

CONDITION_COLORS = {
    "poison":   {"h_low": 40,  "h_high": 80,  "s_min": 100},
    "paralyze": {"h_low": 100, "h_high": 140, "s_min": 80},
    "burning":  {"h_low": 8,   "h_high": 25,  "s_min": 100},
    "drunk":    {"h_low": 26,  "h_high": 45,  "s_min": 80},
    "bleeding": {"h_low": 170, "h_high": 180, "s_min": 100},
    "freezing": {"h_low": 85,  "h_high": 105, "s_min": 80},
}

def detect_conditions(frame, roi):
    x, y, w, h = roi
    icons_region = frame[y:y+h, x:x+w]
    hsv = cv2.cvtColor(icons_region, cv2.COLOR_BGR2HSV)
    detected = []
    for name, params in CONDITION_COLORS.items():
        lower = np.array([params["h_low"], params["s_min"], 50])
        upper = np.array([params["h_high"], 255, 255])
        mask = cv2.inRange(hsv, lower, upper)
        if mask.sum() > 200:  # Suficientes píxeles del color
            detected.append(name)
    return detected
```

### condition_config.json

```json
{
  "condition_icons_roi": [1700, 470, 200, 50],
  "detection_method": "color",
  "check_interval": 0.5,
  "confidence": 0.60,
  "remedies": {
    "poison":   {"hotkey_vk": 116, "cooldown": 2.0},
    "paralyze": {"hotkey_vk": 117, "cooldown": 1.0},
    "burning":  {"hotkey_vk": 116, "cooldown": 2.0}
  }
}
```

---

## 20. Human Input System

**Directorio**: `human_input_system/`

### ¿Por qué hardware HID?

```
WinAPI SendInput → Nivel de aplicación → Detectable en kernel
Arduino/Pico HID → Nivel de hardware (USB) → Indetectable por BattlEye

El driver del kernel ve el input como si viniera de un teclado/ratón físico.
No hay diferencia a nivel de driver entre el bot y una persona pulsando teclas.
```

### Opciones de hardware

| Dispositivo | Librería | Velocidad | Notas |
|-------------|----------|-----------|-------|
| Arduino Leonardo/Micro | `pyserial` | ~5ms latencia | Nativo HID USB |
| Raspberry Pi Pico 2 | `pyserial` | ~3ms latencia | Más potente, USB-C |
| Teensy 4.x | `pyserial` | ~1ms latencia | El más rápido |

### Integración en BotSession

```python
# session.py — detección automática del hardware disponible
try:
    from human_input_system.core.arduino_hid_controller import ArduinoHIDController
    _ARDUINO_AVAILABLE = True
except ImportError:
    _ARDUINO_AVAILABLE = False

# En SessionConfig:
use_arduino_hid: bool = False
arduino_port: str = "COM3"
arduino_baud: int = 115200

# En BotSession._startup_input():
if config.use_arduino_hid and _ARDUINO_AVAILABLE:
    ctrl = ArduinoHIDController(port=config.arduino_port)
else:
    ctrl = InputController("Tibia", "winapi")
```

### Protocolo de comunicación con Arduino

```
PC → Arduino (serial): "K:<VK>:<duration_ms>\n"   # Tecla
PC → Arduino (serial): "M:<x>:<y>:<button>\n"     # Mouse click
Arduino ejecuta → envía HID report al OS → llega a Tibia como input físico
```

### Firmware Arduino (sketch)

```cpp
// El Arduino actúa como teclado HID
#include <Keyboard.h>
#include <Mouse.h>

void loop() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    if (cmd.startsWith("K:")) {
      // Parsear VK y duration, ejecutar Keyboard.press/release
    } else if (cmd.startsWith("M:")) {
      // Parsear x,y,button, ejecutar Mouse.click
    }
  }
}
```

---

## PARTE 5 — ROBUSTEZ Y SEGURIDAD

---

## 21. Sistemas de Seguridad

### DeathHandler

```
Detecta muerte por:
  a) Template matching: "You are dead" / pantalla de respawn
  b) Frame completamente negro por > 2 segundos
  c) HP% = 0 durante > 1 segundo

Acciones post-muerte:
  1. Emitir "player_died"
  2. Pausar todos los módulos
  3. Esperar pantalla de respawn (polling cada 500ms)
  4. Click en "Ok" del respawn
  5. Si re_equip_enabled: presionar hotkeys de equipar
  6. sleep(5)  ← tiempo para resucitar
  7. Reiniciar ruta desde waypoint inicial
```

### StuckDetector

```
Cada 8 segundos:
  Comparar posición actual vs posición hace 8s
  Si delta_tiles < 1:
    Incrementar stuck_count
    Intento 1: A* repath (nueva ruta al mismo destino)
    Intento 2: Nudge (presionar flechas random 3-5 veces)
    Intento 3: Escape route (5 tiles en dirección opuesta)
    Intento 4: Emitir "stuck_abort" → stop session
  Else:
    stuck_count = 0

IMPORTANTE: stuck_count se acumula si hay A* blocked_tiles.
Solución inmediata: reiniciar el bot limpia blocked_tiles.
```

### AntiKick

```
Cada 60s (configurable):
  Opción A: press(VK_SHIFT)     ← no mueve personaje
  Opción B: click en viewport + drag 5px (rota cámara)
  Opción C: open/close backpack (Ctrl+B)
```

### BreakScheduler

```
Sesión activa: random.uniform(45*60, 120*60) segundos
Break:         random.uniform(3*60,  15*60)  segundos
Break largo (tras 4h): random.uniform(20*60, 45*60) segundos

Durante break:
  1. Detener Navigator, CombatManager, Looter
  2. Mantener AutoHealer activo (por si queda en combate)
  3. AntiKick sigue activo
  4. BreakScheduler.sleep_until_resume()
```

### PvPDetector

```
Template matching de cráneos PvP en viewport cada 0.5s
Si detectado:
  "ignore"   → log warning, continuar
  "warn"     → alerta Discord/Telegram
  "pause"    → pausar bot hasta input manual
  "flee"     → mover a tile safe configurado
  "logout"   → cerrar cliente Tibia
```

### GMDetector

```
Template matching de mensajes de GM / staff de Tibia
Texto amarillo con formato "[GM Name]: ..."
Si detectado:
  "pause"    → pausar bot inmediatamente
  "logout"   → logout del personaje
```

### ReconnectHandler

```
Detecta desconexión:
  a) Template matching de pantalla de login
  b) Frame completamente negro por > 5s sin muerte

Acciones:
  1. Esperar 30s (tiempo de reconexión del servidor)
  2. Escribir password
  3. Click en "Ok"
  4. Esperar carga del personaje
  5. Reanudar sesión
```

---

## 22. Humanización Anti-Detección

### Jitter en timing

```python
# src/humanizer.py
import time, random

def jittered_sleep(base_s: float, jitter_pct: float = 0.15):
    """Duerme base ± jitter con distribución normal truncada."""
    sigma = base_s * jitter_pct
    actual = max(0.05, random.gauss(base_s, sigma))
    time.sleep(actual)

# Uso:
jittered_sleep(0.45, jitter_pct=0.15)
# Duerme entre ~0.38 y ~0.52 segundos
```

### Fatigue model

```python
# src/humanizer.py
_fatigue_factor = 1.0

def apply_fatigue(elapsed_hours: float):
    """Incrementa step_interval gradualmente (simula cansancio)."""
    global _fatigue_factor
    if elapsed_hours < 1: _fatigue_factor = 1.0
    elif elapsed_hours < 2: _fatigue_factor = 1.05
    elif elapsed_hours < 4: _fatigue_factor = 1.15
    else: _fatigue_factor = 1.25

def reset_fatigue():
    global _fatigue_factor
    _fatigue_factor = 1.0

def set_jitter(base: float) -> float:
    return base * _fatigue_factor
```

### Movimiento Bezier del ratón

```python
# src/mouse_bezier.py
import numpy as np
import time

def bezier_path(p0, p1, p2, p3, steps=20):
    """Curva Bezier cúbica entre 4 puntos de control."""
    t = np.linspace(0, 1, steps)
    x = (1-t)**3*p0[0] + 3*(1-t)**2*t*p1[0] + 3*(1-t)*t**2*p2[0] + t**3*p3[0]
    y = (1-t)**3*p0[1] + 3*(1-t)**2*t*p1[1] + 3*(1-t)*t**2*p2[1] + t**3*p3[1]
    return list(zip(x.astype(int), y.astype(int)))

def human_move(ctrl, x_dest, y_dest, current_pos=None):
    """Mueve el ratón en curva Bezier con velocidad variable."""
    if current_pos is None:
        # Obtener posición actual del cursor
        import ctypes
        pt = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        current_pos = (pt.x, pt.y)

    # Puntos de control con perturbación aleatoria
    dx, dy = x_dest - current_pos[0], y_dest - current_pos[1]
    cp1 = (current_pos[0] + dx*0.3 + random.randint(-20, 20),
           current_pos[1] + dy*0.3 + random.randint(-20, 20))
    cp2 = (current_pos[0] + dx*0.7 + random.randint(-20, 20),
           current_pos[1] + dy*0.7 + random.randint(-20, 20))

    path = bezier_path(current_pos, cp1, cp2, (x_dest, y_dest), steps=15)
    duration = max(0.08, min(0.20, (abs(dx)+abs(dy)) / 2000))
    delay = duration / len(path)

    for px, py in path:
        ctrl.move_mouse_absolute(px, py)
        time.sleep(delay)
```

### Patrones de variación adicionales

- No usar siempre la misma secuencia de hotkeys de spell
- Variar timing entre heal y spell (±200ms random)
- Ocasionalmente "perder" un ciclo de ataque (1 de cada 30 aprox.)
- Micro-breaks: pause 2-5s cada 20-40 minutos
- No hacer loot siempre igual: variar delay entre abrir y shift-click

---

## 23. Alert System

**Archivo**: `src/alert_system.py`

### Integración Discord Webhook

```python
import urllib.request
import json

class AlertSystem:
    def __init__(self, webhook_url: str = ""):
        self._webhook = webhook_url
        self._enabled = bool(webhook_url)

    def send(self, message: str, level: str = "info"):
        """Envía alerta a Discord. Falla silenciosamente si no hay webhook."""
        if not self._enabled: return
        color = {"info": 0x3498db, "warn": 0xf39c12, "error": 0xe74c3c}.get(level, 0)
        payload = {
            "embeds": [{
                "title": f"Bot Tibia — {level.upper()}",
                "description": message,
                "color": color
            }]
        }
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._webhook,
            data=data,
            headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass  # Falla silenciosamente
```

### Eventos que generan alertas

```python
# En BotSession:
bus.subscribe("player_died",  lambda _: alert.send("Personaje murió!", "error"))
bus.subscribe("stuck_abort",  lambda _: alert.send("Bot abortado por stuck", "error"))
bus.subscribe("gm_detected",  lambda _: alert.send("GM detectado — pausado", "warn"))
bus.subscribe("resupply_done", lambda d: alert.send(f"Resupply #{d['reason']} done", "info"))
bus.subscribe("route_done",   lambda d: alert.send(f"Ciclo #{d['cycle']} completado", "info"))
```

---

## PARTE 6 — TESTING Y DEPLOY

---

## 24. Estrategia de Tests

### Niveles de test

```
nivel0: Imports y sintaxis
nivel1: Modelos de datos (Coordinate, Route, Waypoint)
nivel2: Detección visual (HP/MP, templates) con imágenes sintéticas
nivel3: Pathfinder A* con grids conocidos
nivel4: Navigator + script parser con mocks
nivel5: Session startup (sin Tibia abierto)
nivel6: Integration tests (con Tibia abierto)
nivel7: Live tests (bot corriendo en producción)
```

### Correr tests por nivel

```bash
python -X utf8 run_nivel0_tests.py   # Imports básicos
python -X utf8 run_nivel1_tests.py   # Modelos de datos
python -X utf8 run_nivel2_tests.py   # Detección visual
python -X utf8 run_nivel3_tests.py   # Pathfinder
python -X utf8 run_nivel4_tests.py   # Navigator
python -X utf8 run_nivel5_tests.py   # Session
python -X utf8 run_check.py         # Pre-flight check
```

### Test de HP detector con imagen sintética

```python
import numpy as np
import pytest
from src.hpmp_detector import HpMpDetector, HpMpConfig

def test_hp_full():
    """HP al 100% → barra completamente verde."""
    frame = np.zeros((100, 800, 3), dtype=np.uint8)
    # Rellenar ROI con verde HP
    frame[29:42, 12:781, 1] = 172  # Canal G
    frame[29:42, 12:781, 0] = 37   # Canal B
    frame[29:42, 12:781, 2] = 37   # Canal R

    config = HpMpConfig(hp_roi=[12, 29, 769, 13])
    detector = HpMpDetector(config)
    hp, _ = detector.read(frame)
    assert hp > 95.0, f"HP esperado >95%, obtenido {hp:.1f}%"

def test_hp_empty():
    """HP al 0% → barra completamente negra."""
    frame = np.zeros((100, 800, 3), dtype=np.uint8)
    config = HpMpConfig(hp_roi=[12, 29, 769, 13])
    detector = HpMpDetector(config)
    hp, _ = detector.read(frame)
    assert hp < 5.0
```

### Test de pathfinder con obstáculos

```python
import numpy as np
from src.pathfinder import AStarPathfinder
from src.models import Coordinate

def test_pathfinder_straight():
    walkability = np.ones((100, 100), dtype=bool)
    pf = AStarPathfinder(walkability, allow_diagonal=False)
    start = Coordinate(32350, 32240, 7)
    end   = Coordinate(32355, 32240, 7)
    route = pf.find_path(start, end)
    assert route.found
    assert len(route.steps) == 6  # start + 5 pasos al este

def test_pathfinder_obstacle():
    walkability = np.ones((100, 100), dtype=bool)
    # Muro vertical en x=5
    walkability[0:100, 5] = False
    pf = AStarPathfinder(walkability, allow_diagonal=False)
    start = Coordinate(31744+4, 30976+5, 7)
    end   = Coordinate(31744+6, 30976+5, 7)
    route = pf.find_path(start, end)
    assert route.found
    # La ruta debe rodear el obstáculo
    xs = [c.x for c in route.steps]
    assert (31744+5) not in xs  # No atraviesa el muro
```

### Mock de frame_getter para tests

```python
import numpy as np

def make_mock_frame_getter(frame: np.ndarray):
    """Retorna un frame fijo. Útil para tests sin pantalla."""
    def getter():
        return frame.copy()
    return getter
```

---

## 25. Pre-flight Check

**Script**: `run_check.py`

```bash
python -X utf8 run_check.py
```

### Qué verifica

```
[CHECK 1] Imports       → Todos los módulos cargan sin error
[CHECK 2] Frame source  → mss captura monitor 2, frame no es None
[CHECK 3] Frame quality → No es negro, no está congelado
[CHECK 4] HP/MP ROIs    → hp_bar.mean_BGR ≈ verde → ROI apunta a barra
[CHECK 5] Input ctrl    → FindWindow("Tibia") retorna HWND válido
[CHECK 6] Position      → MinimapRadar retorna Coordinate válida
[CHECK 7] Templates     → monsters/ tiene al menos 1 template .png
[CHECK 8] Routes        → route_file existe y parsea correctamente
[CHECK 9] Config JSONs  → Todos los JSON requeridos son válidos
```

### Resultado esperado

```
[CHECK 1] ✓ Imports OK
[CHECK 2] ✓ Frame capturado: 1920x1080 BGR
[CHECK 3] ✓ Frame quality OK (mean=87, no negro)
[CHECK 4] ✓ HP bar OK (mean_G=172, verde)
[CHECK 5] ✓ Tibia HWND: 0x1234ABC
[CHECK 6] ✓ Posición: Coordinate(32369, 32241, 7)
[CHECK 7] ✓ Templates: 5 monster templates encontrados
[CHECK 8] ✓ Ruta: thais_rat_hunt.json — 24 waypoints
[CHECK 9] ✓ Configs: todos OK
─────────────────────────
✓ PRE-FLIGHT PASS — Listo para iniciar
```

---

## 26. Diagnóstico y Debug

### Síntomas comunes y soluciones

| Síntoma | Causa probable | Solución |
|---------|---------------|----------|
| Bot cuelga en inicio | dxcam DXGI dirty state | Usar `frame_source="mss"` explícito |
| HP/MP siempre 0% | ROI desajustada | Correr `debug_capture.py`, ajustar hpmp_config.json |
| Frames None streak | Tibia no visible en monitor 2 | Verificar que Tibia está en monitor 2 sin minimizar |
| OCR no detecta mobs | battle_list_roi mal calibrado | Ajustar ROI en combat_config.json |
| Personaje no se mueve | Interception no funciona | Usar `input_method="winapi"` |
| Walker no pausa en loot | _loot_in_progress no conectado | Verificar logs: `[W] Loot en curso` |
| A* crashea con blocked_tiles | NPC/jugador bloquea tiles | Reiniciar bot (limpia blocked_tiles al inicio) |
| Loot falla consistentemente | open_container_delay corto | Aumentar a 1.8-2.0 en loot_config.json |
| Bot no compra pociones | NPC coords incorrectas | Verificar y calibrar trade_config.json |
| Posición siempre None | minimap_config.json no calibrado | Calibrar ROI del minimapa con debug_capture.py |
| Spells no se lanzan | min_mp muy alto o cooldown activo | Verificar mp_pct actual vs min_mp en combat_config |
| Bot muere en spawn | heal_threshold demasiado bajo | Subir hp_threshold_pct (ej: 70 → 80) |

### debug_capture.py — diagnóstico visual

```bash
python -X utf8 debug_capture.py
# Captura frame, dibuja todos los ROIs configurados
# Guarda en output/debug_frame.png

# Interpretar resultado:
# hp_bar.mean_BGR ≈ [37, 172, 37]  → ROI correcta, HP lleno (verde)
# hp_bar.mean_BGR ≈ [0, 0, 0]      → ROI en área negra (desajustada)
# minimap_roi visible               → ROI del minimapa correcta
# battle_list_roi visible            → Combat ROI correcta
```

### Logging — dos canales

```python
# 1. session._log() → stdout (print) — inmediato en terminal
# 2. logging.getLogger("frbit.*") → stdout + output/phase3.log

# Para diagnosticar hangs: leer AMBAS fuentes
# El log de archivo puede tener el último mensaje antes del hang
tail -f output/phase3.log

# En Windows PowerShell:
Get-Content output/phase3.log -Wait
```

### ROI History para Aelzerand (monitor 2)

| Setup | hp_roi.y | Razón del cambio |
|-------|----------|-----------------|
| WGC window-relative | 29 | Coords relativas a ventana |
| dxcam full-screen monitor 1 | 52 | Tibia en y=23 del monitor 1 → +23 offset |
| mss monitor 2 (producción) | **29** | Tibia borderless en y=0 del monitor 2 |

---

## 27. Scripts de Producción

### run_phase3.py (script principal)

```python
from src.session import BotSession, SessionConfig

config = SessionConfig(
    # Ruta
    route_file       = "routes/thais_rat_hunt.json",
    loop_route       = True,
    step_interval    = 0.45,
    jitter_pct       = 0.15,

    # Frame — EXPLÍCITO para evitar auto-upgrade a dxcam
    frame_source     = "mss",
    monitor_idx      = 2,

    # Input — Interception NO funciona en producción
    input_method     = "winapi",

    # Posición
    position_source        = "minimap",
    use_position_resolver  = True,

    # Curación
    heal_hp_pct          = 70,
    heal_emergency_pct   = 30,
    mana_threshold_pct   = 30,
    heal_hotkey_vk       = 0x70,   # F1
    emergency_hotkey_vk  = 0x72,   # F3
    mana_hotkey_vk       = 0x71,   # F2

    # Subsistemas on/off
    auto_combat      = True,
    auto_loot        = True,
    auto_refill      = False,
    death_handler    = True,
    anti_kick        = True,
    stuck_detector   = True,
    break_scheduler  = False,
    pvp_detector     = False,
    gm_detector      = False,
    dashboard        = False,
)

session = BotSession(config)
session.start()
```

### Comando de ejecución

```bash
# Terminal con encoding correcto
PYTHONUNBUFFERED=1 python -X utf8 run_phase3.py 2>&1

# Con logging a archivo
PYTHONUNBUFFERED=1 python -X utf8 run_phase3.py 2>&1 | tee output/phase3.log
```

### run_check.py — antes de iniciar siempre

```bash
python -X utf8 run_check.py
# Verificar que todos los checks son ✓ antes de run_phase3.py
```

---

## PARTE 7 — RECETAS

---

## 28. Setup desde Cero

### Paso 1: Entorno Python

```bash
cd waypoint-navigator
python -m venv .venv
source .venv/Scripts/activate

pip install mss opencv-python numpy pywin32 easyocr Pillow PyYAML aiohttp pyserial pytest
```

### Paso 2: Tibia en posición correcta

1. Mover cliente Tibia a Monitor 2
2. Modo borderless (sin título de ventana)
3. Resolución del cliente: 1920×1080
4. Zoom del mapa al mismo nivel (nota el nivel para calibración)

### Paso 3: Descargar mapa de tibiamaps.io

```python
# Ejecutar una vez para descargar mapas
from src.map_loader import TibiaMapLoader
loader = TibiaMapLoader(cache_dir="maps/")
loader.download_floor(7)   # Piso ground level
loader.download_floor(8)   # Si vas a cuevas
```

### Paso 4: Capturar frame de referencia

```bash
python -X utf8 debug_capture.py
# Abre output/debug_frame.png en tu editor de imágenes
# Necesitas este frame para calibrar todos los ROIs
```

### Paso 5: Calibrar ROIs (con debug_capture.py como referencia)

```python
# Usar herramienta de calibración o editar JSON manualmente
# Medir píxeles en el frame de referencia:

# hpmp_config.json:
# HP bar: medir x_inicio, y_inicio, ancho, alto de la barra verde
# MP bar: medir igual para la barra azul

# combat_config.json:
# battle_list_roi: la columna derecha donde aparecen nombres de monstruos

# minimap_config.json:
# roi: el área pequeña del minimapa en la esquina superior derecha
```

### Paso 6: Colectar templates de monstruos

```bash
# Capturar screenshots mientras monstruos están en battle list
# Recortar el área del nombre del monstruo
# Guardar en cache/templates/monsters/<nombre_monstruo>.png

# O usar dataset_collector.py para captura automática
```

### Paso 7: Crear ruta de hunting

```json
{
  "name": "Mi Spawn",
  "start": {"x": XXXX, "y": YYYY, "z": Z},
  "waypoints": [
    {"x": XXXX, "y": YYYY, "z": Z, "label": "start"},
    ... (copiar desde tibiamaps.io los tiles del spawn)
  ],
  "loop": true
}
```

### Paso 8: Verificar con run_check.py

```bash
python -X utf8 run_check.py
# Todos ✓ → listo para producción
```

### Paso 9: Primera ejecución supervisada

```bash
python -X utf8 run_phase3.py
# Supervisar los primeros 10 minutos
# Verificar: HP se mantiene, loot funciona, ruta correcta
```

---

## 29. Calibrar Nuevo Personaje

### Variables que cambian por personaje

1. **HP/MP ROIs** — si la UI está en posición diferente
2. **Battle list ROI** — resolución/zoom diferente del cliente
3. **Spells** — cada clase tiene diferentes spells y VKs
4. **Heal thresholds** — knight aguanta más que mago
5. **Inventory ROI** — si la mochila está en posición diferente

### Proceso de calibración rápida

```bash
# 1. Capturar frame de referencia del nuevo personaje
python -X utf8 debug_capture.py
# Guardar output/debug_frame.png como referencia

# 2. Medir ROIs en el frame (usar Photoshop/GIMP/Paint.net)
# - Hover sobre cada pixel y leer coordenadas (x, y)
# - El ROI es [x_inicio, y_inicio, ancho, alto]

# 3. Actualizar JSONs
# 4. Correr run_check.py para verificar

# 5. Test en vivo 5 minutos
```

### Calibración de HP bar (proceso)

```
En el frame de referencia con HP al 100%:
1. Identificar la barra verde de HP
2. Medir: x_start (pixel más a la izquierda de la barra)
3. Medir: y_start (pixel superior de la barra)
4. Medir: width (ancho total de la barra)
5. Medir: height (alto de la barra, suele ser 10-15px)

ROI = [x_start, y_start, width, height]

Verificar: python -X utf8 -c "
import mss, numpy as np
sct = mss.mss()
frame = np.array(sct.grab(sct.monitors[2]))[:,:,:3]
roi = frame[y_start:y_start+height, x_start:x_start+width]
print('mean BGR:', roi.mean(axis=(0,1)))
# Si HP lleno: B≈37, G≈172, R≈37
"
```

---

## 30. Adaptar a Nuevo Spawn

### Variables del spawn

```json
{
  "ruta": "Crear routes/<spawn>.json con waypoints del spawn",
  "monstruos": "combat_config.json → monsters[]",
  "spells": "combat_config.json → spells[] (según HP de monstruos)",
  "loot": "loot_config.json → loot_whitelist[]",
  "heal_threshold": "Ajustar según daño por segundo del spawn",
  "step_interval": "Más alto si el spawn es denso (más combate)"
}
```

### Template matching de nuevos monstruos

```bash
# 1. Correr el bot con el personaje en el nuevo spawn
# 2. Cuando aparezcan monstruos en battle list → debug_capture.py
# 3. Recortar el nombre del monstruo de output/debug_frame.png
# 4. Guardar como cache/templates/monsters/<nombre>.png
# 5. Actualizar monsters[] en combat_config.json
```

### Ajuste de heal thresholds por spawn

```
Spawn seguro (Cave Rats, nivel bajo):
  hp_threshold_pct: 70
  hp_emergency_pct: 30

Spawn medio (Wasps, Scorpions):
  hp_threshold_pct: 80
  hp_emergency_pct: 40

Spawn peligroso (Elder Druid, Banshee):
  hp_threshold_pct: 90
  hp_emergency_pct: 60
```

---

## 31. Arquitectura Greenfield

Si construyeras este bot desde cero con todas las lecciones aprendidas:

### Stack mínimo funcional (en orden de desarrollo)

```
Semana 1: Frame + HP/MP + Healer
  mss → frame_getter → HpMpDetector → AutoHealer

Semana 2: Input + Navegación básica
  InputController → AStarPathfinder → WaypointNavigator

Semana 3: Combate + Loot
  CombatManager (template+OCR) → Looter (quick loot)

Semana 4: Posición + Orquestación
  MinimapRadar → PositionResolver → BotSession + EventBus

Semana 5: Robustez
  StuckDetector → DeathHandler → AntiKick

Semana 6: Depot + Trade
  InventoryManager → DepotOrchestrator → TradeManager
```

### Decisiones de arquitectura críticas

```
1. mss > dxcam
   Razón: DXGI hangs, GDI no tiene ese problema

2. winapi > interception
   Razón: Interception no mueve personaje en producción

3. threading.Event > sleep loops
   Razón: _loot_in_progress.wait() pausa walker correctamente

4. EventBus para comunicación entre módulos
   Razón: Elimina referencias circulares, facilita testing

5. JSON por módulo (no config global)
   Razón: Cambiar spawn solo modifica combat_config.json y routes/

6. frame_getter inyectado > frame guardado
   Razón: Siempre obtienes el frame más reciente, no uno obsoleto

7. Explicit frame_source > auto-upgrade
   Razón: Auto-upgrade a dxcam → hang en producción

8. daemon threads con join(timeout=3)
   Razón: El proceso termina aunque el hilo esté atascado

9. try/except en cada _loop()
   Razón: Un error no mata el hilo, el bot sigue corriendo

10. Fallback chains en detección
    Razón: Template falla → OCR, OCR falla → LastKnown
```

### Diagrama de flujo completo

```
INICIO: python -X utf8 run_phase3.py
  ↓
BotSession(config).start()
  ↓
[STARTUP]
  → MSSSource(monitor_idx=2) → FrameCache(50ms)
  → InputController("Tibia", "winapi")
  → TibiaMapLoader → AStarPathfinder
  → HpMpDetector(hpmp_config)
  → AutoHealer.start()          [HILO 1]
  → CombatManager.start()       [HILO 2]
  → Looter.start()              [HILO 3]
  → StuckDetector.start()       [HILO 4]
  → AntiKick.start()            [HILO 5]
  → MinimapRadar (inline)
  ↓
sleep(3)  [dar tiempo al jugador para posicionarse]
  ↓
[LOOP PRINCIPAL — HILO MAIN]
  WHILE running:
    pos = MinimapRadar.read(frame)
    route = A*.find_path(pos, next_waypoint)
    FOR step IN route.steps:
      press(direction_vk)
      jittered_sleep(0.45, 0.15)
      IF _loot_in_progress: wait()   ← Looter lo setea
    NEXT WAYPOINT
    IF should_resupply(): DepotOrchestrator.run()
```

---

## APÉNDICE

---

## A. Modelos de Datos

### Coordinate

```python
@dataclass(frozen=True, order=True, slots=True)
class Coordinate:
    x: int          # 31744–34048 (Este)
    y: int          # 30976–32768 (Sur)
    z: int = 7      # 0=cielo, 7=suelo, 8+=subterráneo

    def distance_to(self, other: "Coordinate") -> float:
        """Distancia Chebyshev (diagonal = 1)."""
        return max(abs(self.x - other.x), abs(self.y - other.y))

    def manhattan_to(self, other: "Coordinate") -> int:
        return abs(self.x - other.x) + abs(self.y - other.y)

    def offset(self, dx: int, dy: int, dz: int = 0) -> "Coordinate":
        return Coordinate(self.x + dx, self.y + dy, self.z + dz)

    def is_adjacent_to(self, other: "Coordinate") -> bool:
        return (abs(self.x - other.x) <= 1 and
                abs(self.y - other.y) <= 1 and
                self.z == other.z)

    @property
    def is_surface(self) -> bool:
        return self.z == 7

    @property
    def to_map_pixel(self) -> tuple:
        """Convierte a píxel en PNG de tibiamaps.io (escala 4x)."""
        return ((self.x - 31744) * 4, (self.y - 30976) * 4)
```

### Route

```python
@dataclass(slots=True)
class Route:
    start: Coordinate
    end: Coordinate
    steps: list          # List[Coordinate]
    total_distance: float
    found: bool

    def reversed(self) -> "Route":
        return Route(self.end, self.start,
                     list(reversed(self.steps)),
                     self.total_distance, self.found)

    def slice(self, start_idx: int, end_idx: int) -> "Route":
        sliced = self.steps[start_idx:end_idx]
        return Route(sliced[0], sliced[-1], sliced,
                     len(sliced)-1, True)
```

### FloorTransition

```python
@dataclass(slots=True)
class FloorTransition:
    entry: Coordinate       # Tile donde usar la transición
    exit: Coordinate        # Destino
    kind: str = "walk"      # walk|use|rope|shovel|ladder

    @property
    def is_rope(self) -> bool:
        return self.kind == "rope"

    @property
    def floor_delta(self) -> int:
        return self.exit.z - self.entry.z
```

---

## B. VK Codes

### Teclas de función

```python
F1=0x70, F2=0x71, F3=0x72,  F4=0x73,
F5=0x74, F6=0x75, F7=0x76,  F8=0x77,
F9=0x78, F10=0x79, F11=0x7A, F12=0x7B
```

### Movimiento

```python
ARROW_UP    = 0x26  # Norte
ARROW_DOWN  = 0x28  # Sur
ARROW_LEFT  = 0x25  # Oeste
ARROW_RIGHT = 0x27  # Este
```

### Modificadores

```python
SHIFT = 0x10
CTRL  = 0x11
ALT   = 0x12
```

### Numpad

```python
NUM0=0x60, NUM1=0x61, NUM2=0x62, NUM3=0x63, NUM4=0x64,
NUM5=0x65, NUM6=0x66, NUM7=0x67, NUM8=0x68, NUM9=0x69
```

### Letras (ASCII mayúscula)

```python
A=0x41, B=0x42, C=0x43, D=0x44, E=0x45, F=0x46,
G=0x47, H=0x48, I=0x49, J=0x4A, K=0x4B, L=0x4C,
M=0x4D, N=0x4E, O=0x4F, P=0x50, Q=0x51, R=0x52,
S=0x53, T=0x54, U=0x55, V=0x56, W=0x57, X=0x58,
Y=0x59, Z=0x5A
```

### Hotkeys por clase (configuración estándar)

| VK | Knight | Paladin | Sorcerer | Druid |
|----|--------|---------|---------|-------|
| F1 (0x70) | exura | exura san | exura | exura |
| F2 (0x71) | strong mana | strong mana | strong mana | strong mana |
| F3 (0x72) | exura gran | exura gran san | exura gran | exura gran |
| F5 (0x74) | utamo vita | - | - | antídoto |
| F6 (0x75) | haste | haste | haste | haste |
| F7 (0x76) | exori | exori san | terra strike | - |
| F8 (0x77) | exori gran | exori gran san | terra wave | - |

---

## C. Sistema de Coordenadas

### Tibia world coordinates

```
Eje X: 31744 → 34048 (Oeste → Este)
Eje Y: 30976 → 32768 (Norte → Sur, Y crece hacia el sur)
Eje Z: 0=cielo, 7=superficie terrestre, 8+=subterráneo

Thais superficie: z=7
Centro aproximado de Thais: (32369, 32241, 7)
```

### Conversión coordenadas ↔ píxel en mapa PNG

```python
# Tibia coord → pixel en floor-07-map.png (tibiamaps.io, escala 4x)
px = (x - 31744) * 4
py = (y - 30976) * 4

# Pixel en mapa → Tibia coord
x = (px // 4) + 31744
y = (py // 4) + 30976
```

### Conversión coordenadas ↔ posición en pantalla

```python
# center_px: centro de la pantalla en píxeles (viewport del juego)
# tile_px: tamaño de un tile en píxeles (suele ser 32px en Tibia)

def world_to_screen(world, player, center_px=(960, 540), tile_px=32):
    dx = world.x - player.x
    dy = world.y - player.y
    return (center_px[0] + dx*tile_px, center_px[1] + dy*tile_px)
```

### Monitor virtual vs monitor relativo

```
PANTALLA VIRTUAL (2 monitores):
  Monitor 1: left=0,    top=0, width=1920, height=1080
  Monitor 2: left=1920, top=0, width=1920, height=1080

mss.monitors[0] = pantalla virtual completa (3840×1080)
mss.monitors[1] = Monitor 1 solo
mss.monitors[2] = Monitor 2 solo ← USAR ESTE

CRÍTICO: Las ROIs del bot son RELATIVAS al monitor capturado.
Con mss+monitor_idx=2: coords empiezan en (0,0) del Monitor 2.
Si Tibia está en y=0 del Monitor 2: hp_bar ROI y=29.
Si Tibia estuviera en y=23: hp_bar ROI y=52 (29+23).
```

---

## D. Flujos de Ejecución

### Startup completo

```
python -X utf8 run_phase3.py
  ↓
BotSession(config).start()
  ↓
_startup_frame_source():
  → MSSScreenSource(monitor_idx=2)
  → _src_explicit = True  ← NO auto-upgrade a dxcam
  → FrameCache(ttl_ms=50)
  → FrameWatchdog(idle_threshold=15s)
  ↓
_startup_input():
  → InputController("Tibia", "winapi")
  → hwnd = FindWindow("Tibia") — requiere Tibia en foreground al inicio
  ↓
_startup_navigator():
  → TibiaMapLoader.get_walkability(floor=7)
  → WaypointNavigator(cache_dir="maps/")
  → load_route("routes/thais_rat_hunt.json")
  ↓
_startup_healer():
  → HpMpDetector(HpMpConfig.from_file("hpmp_config.json"))
  → AutoHealer(ctrl, HealConfig.from_file("heal_config.json"))
  → healer.set_frame_getter(frame_getter)
  → healer.start()  ← HILO independiente
  ↓
_startup_subsystems():
  → CombatManager(ctrl, CombatConfig.from_file("combat_config.json"))
  → CombatManager.set_frame_getter(frame_getter)
  → CombatManager.start()
  → Looter(ctrl, LootConfig.from_file("loot_config.json"))
  → Looter.start()
  → DeathHandler.start()
  → StuckDetector.start()
  → AntiKick.start()
  ↓
sleep(3)  ← dar tiempo al jugador para posicionarse manualmente
  ↓
_exec_route():  ← LOOP PRINCIPAL (hilo main)
```

### Loop de combate (CombatManager, hilo paralelo)

```
WHILE running (cada 0.3s):
  frame = frame_getter()
  IF frame is None: continue

  roi = frame[bl_y:bl_y+bl_h, bl_x:bl_x+bl_w]  # battle_list ROI

  mobs = []
  FOR template IN monster_templates:
    matches = cv2.matchTemplate(roi, template, TM_CCOEFF_NORMED)
    IF matches.max() >= config.confidence:
      mobs.append(extract_match_position(matches))

  IF not mobs AND config.ocr_detection:
    text = ocr.read(roi)
    mobs = [m for m in config.monsters if m.lower() in text.lower()]

  IF mobs:
    ctrl.click(first_mob_screen_pos)  ← auto-attack
    FOR spell IN config.spells:
      IF mp_pct >= spell.min_mp AND cooldown_ok(spell):
        ctrl.press_key(spell.vk)
    bus.emit("mob_detected", {"name": mobs[0]})

  ELIF was_combat AND not mobs:
    bus.emit("kill", {"name": last_mob, "coord": player_coord})
    was_combat = False
```

### Loop de curación (AutoHealer, hilo paralelo)

```
WHILE running (cada 0.3s):
  frame = frame_getter()
  IF frame is None: continue

  hp_pct, mp_pct = hpmp_detector.read(frame)

  IF hp_pct == 0 AND mp_pct == 0:
    skip (frame mal capturado)

  IF hp_pct < config.hp_emergency_pct AND cooldown_emergency_ok:
    ctrl.press_key(config.emergency_hotkey_vk)
    cooldown_emergency = now()
    bus.emit("heal", {"hp_pct": hp_pct, "type": "emergency"})

  ELIF hp_pct < config.hp_threshold_pct AND cooldown_heal_ok:
    ctrl.press_key(config.heal_hotkey_vk)
    cooldown_heal = now()
    bus.emit("heal", {"hp_pct": hp_pct, "type": "normal"})

  IF mp_pct < config.mp_threshold_pct AND cooldown_mana_ok:
    ctrl.press_key(config.mana_hotkey_vk)
    cooldown_mana = now()
    bus.emit("mana", {"mp_pct": mp_pct})
```

---

## E. Configuraciones de Producción

### session_config.json (Aelzerand — Knight Thais)

```json
{
  "route_file": "routes/thais_rat_hunt.json",
  "frame_source": "mss",
  "monitor_idx": 2,
  "input_method": "winapi",
  "position_source": "minimap",
  "use_position_resolver": true,
  "step_interval": 0.45,
  "jitter_pct": 0.15,
  "loop_route": true,
  "auto_combat": true,
  "auto_loot": true,
  "auto_refill": false,
  "death_handler": true,
  "anti_kick": true,
  "stuck_detector": true,
  "heal_hp_pct": 70,
  "heal_emergency_pct": 30,
  "mana_threshold_pct": 30,
  "heal_hotkey_vk": 112,
  "emergency_hotkey_vk": 114,
  "mana_hotkey_vk": 113
}
```

### hpmp_config.json (producción — mss monitor 2)

```json
{
  "hp_roi":      [12,  29, 769, 13],
  "mp_roi":      [788, 29, 768, 13],
  "hp_text_roi": [484, 310, 1376, 20],
  "mp_text_roi": [374, 322, 1486, 20]
}
```

### heal_config.json (Knight)

```json
{
  "hp_threshold_pct": 70,
  "hp_emergency_pct": 30,
  "mp_threshold_pct": 30,
  "heal_hotkey_vk": 112,
  "emergency_hotkey_vk": 114,
  "mana_hotkey_vk": 113,
  "heal_cooldown": 2.0,
  "emergency_cooldown": 1.0,
  "mana_cooldown": 1.5,
  "check_interval": 0.3,
  "utamo_hotkey_vk": 0,
  "utamo_cooldown": 60.0,
  "haste_hotkey_vk": 0,
  "haste_cooldown": 45.0
}
```

### combat_config.json (Aelzerand — Thais surface)

```json
{
  "battle_list_roi": [1699, 480, 210, 400],
  "monsters": ["Cave Rat", "Wasp", "Poison Spider", "Bug", "Spider"],
  "spells": [
    {"vk": 118, "min_mp": 20, "cooldown": 2.5, "label": "F7"},
    {"vk": 119, "min_mp": 30, "cooldown": 4.0, "label": "F8"},
    {"vk": 120, "min_mp": 50, "cooldown": 6.0, "label": "F9"},
    {"vk": 121, "min_mp": 60, "cooldown": 8.0, "label": "F10"}
  ],
  "ocr_detection": true,
  "ocr_confidence": 0.3,
  "confidence": 0.65,
  "check_interval": 0.3,
  "hp_flee_pct": 25
}
```

### loot_config.json

```json
{
  "viewport_roi": [0, 0, 1920, 1080],
  "tile_size_px": 32,
  "container_roi": [1600, 600, 320, 400],
  "context_menu_offset_y": 18,
  "loot_mode": "whitelist",
  "loot_whitelist": ["gold_coin", "spider_fangs", "meat"],
  "confidence": 0.60,
  "click_delay": 0.10,
  "open_container_delay": 1.5,
  "max_attempts": 5,
  "use_hotkey_quick_loot": true,
  "quick_loot_vk": 18
}
```

### minimap_config.json

```json
{
  "roi": [1725, 25, 186, 120],
  "floor": 7,
  "confidence": 0.35,
  "mask_center": true,
  "scale_factors": [1.0, 0.9, 1.1],
  "temporal_smoothing": 1
}
```

---

*Última actualización: 2026-03-23*
*Personaje activo: Aelzerand Neeymas — Knight nivel 8+, Thais surface*
*Script de producción: `PYTHONUNBUFFERED=1 python -X utf8 run_phase3.py`*
*Repositorio: `waypoint-navigator/` — 60+ módulos Python*
