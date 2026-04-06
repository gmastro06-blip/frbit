# Raspberry Pi Pico 2 — HID Emulator Setup

## Qué es

Firmware CircuitPython para Raspberry Pi Pico 2 (RP2350) que emula un
teclado y mouse USB reales. El PC lo ve como hardware físico — BattlEye
no puede distinguirlo de un teclado/mouse de verdad.

## Requisitos

- Raspberry Pi Pico 2 (RP2350)
- CircuitPython 9.0+ ([descargar](https://circuitpython.org/board/raspberry_pi_pico2/))
- Librería `adafruit_hid` (incluida en el bundle de CircuitPython)

## Instalación

### 1. Instalar CircuitPython

1. Mantén presionado **BOOTSEL** en el Pico 2 mientras lo conectás por USB
2. Aparece como unidad **RPI-RP2**
3. Arrastrá el archivo `.uf2` de CircuitPython 9.x
4. Se reinicia y aparece como **CIRCUITPY**

### 2. Instalar Librería adafruit_hid

1. Descargá el [Adafruit CircuitPython Bundle](https://circuitpython.org/libraries)
2. Copiá la carpeta `adafruit_hid/` a `CIRCUITPY/lib/`

### 3. Copiar Firmware

```
CIRCUITPY/
├── boot.py       ← copia de pico2/boot.py
├── code.py       ← copia de pico2/code.py
└── lib/
    └── adafruit_hid/
        ├── __init__.py
        ├── keyboard.py
        ├── keycode.py
        └── mouse.py
```

### 4. Verificar

El Pico 2 aparece como:
- **Teclado USB** + **Mouse USB** (dispositivos HID)
- **Puerto serial** (COM en Windows, /dev/ttyACM en Linux)

## Protocolo

Mismo protocolo que el Arduino (pipe-delimited, newline-terminated):

| Comando | Formato | Respuesta |
|---------|---------|-----------|
| Ping | `PING` | `PONG` |
| Tecla | `KEY_PRESS\|F1\|150` | `ACK` |
| Soltar tecla | `KEY_RELEASE\|F1` | `ACK` |
| Mover mouse | `MOUSE_MOVE\|100\|50\|1` | `ACK` |
| Click | `MOUSE_CLICK\|LEFT` | `ACK` |
| Scroll | `MOUSE_SCROLL\|3` | `ACK` |
| Estado | `STATUS` | `OK\|uptime\|cmds` |

## Uso con el Bot

En la configuración del bot:
```python
BotSessionConfig(
    pico_enabled=True,
    pico_port="auto",   # auto-detecta el Pico 2
)
```

O en `human_input_system/config.yaml`:
```yaml
pico:
  enabled: true
  port: null           # null = auto-detect
  baudrate: 115200
```

## Ventajas sobre Arduino

- **CPU dual-core ARM Cortex-M33** a 150 MHz (vs 16 MHz del ATmega32u4)
- **520 KB SRAM** (vs 2.5 KB)
- **No necesita Arduino IDE** — solo copiar archivos
- **CircuitPython** — modificable sin recompilar
- **USB nativo** — latencia más baja que CDC serial del Arduino
- Mismo precio (~$5 USD)

## Troubleshooting

- **No aparece CIRCUITPY**: Reinstalar CircuitPython con BOOTSEL
- **No responde PONG**: Verificar que `code.py` y `boot.py` están copiados
- **ERR en comandos**: Verificar formato (pipe-delimited, sin espacios extra)
- **Latencia alta**: Verificar cable USB (usar cable de datos, no solo carga)
