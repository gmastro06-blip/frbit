# Rutas y Scripts de Navegación

Carpeta para guardar rutas verificadas y scripts `.in` de navegación automática.

---

## 1. Rutas JSON (waypoints)

Cada ruta es un archivo `.json` con la siguiente estructura:

```json
{
  "name": "Nombre descriptivo de la ruta",
  "description": "Descripción opcional",
  "start": { "x": 32377, "y": 32222, "z": 7 },
  "end":   { "x": 32256, "y": 32244, "z": 7 },
  "dest_waypoint": "Nombre del waypoint destino (cache/markers.json)",
  "verified": true,
  "steps_count": 153,
  "duration_sec": 38,
  "notes": "Notas adicionales"
}
```

### Rutas incluidas

| Archivo | Origen → Destino | Piso | Pasos |
|---|---|---|---|
| `thais_depot_bank_to_temple.json`           | Depot/Bank → Temple    | 7    | 38  |
| `thais_depot_to_temple.json`                | Depot → Temple         | 7    | 153 |
| `thais_spawn_to_depot.json`                 | Spawn → Depot          | 7    | 144 |
| `thais_temple_to_depot_bank.json`           | Temple → Depot/Bank    | 7    | 38  |
| `wasp_thais/wasp_thais_ek.json`             | Cavebot completo EK    | 7-9  | 97 instrucciones |
| `wasp_thais/wasp_thais_ek_nopvp.json`       | Cavebot EK NoPvP       | 7-9  | 96 instrucciones |
| `wasp_thais/wasp_thais_ek_nopvp_live.json`  | Cavebot EK NoPvP live  | 7-9  | 96 instrucciones |

> **Resolución automática de ruta:** si el path no existe tal cual, el bot busca
> automáticamente en `routes/`. Puedes omitir el prefijo:
> ```powershell
> python main.py run --route wasp_thais/wasp_thais_ek.json
> python main.py run --route wasp_thais/wasp_thais_ek_nopvp.json
> python main.py run --route wasp_thais/wasp_thais_ek_nopvp_live.json
> python main.py run --route thais_temple_to_depot_bank.json
> ```

### Cómo usar una ruta guardada

```powershell
# Sin verificación (modo rápido, 180 ms/paso):
python examples/auto_walker.py --dest "Temple" --x 32256 --y 32244 --floor 7 --target "Tibia"

# Con verificación de movimiento pixel-diff (recomendado, 250 ms/paso):
python examples/auto_walker.py --dest "Temple" --x 32256 --y 32244 --floor 7 --target "Tibia" --interval 0.25 --verify-pos
```

## Sistema de verificación de movimiento

`--verify-pos` activa el `MotionDetector`:
- Captura un frame del viewport del juego **antes** de enviar cada tecla
- Captura otro frame **después** de `--interval` segundos
- Calcula la diferencia media de píxeles (`diff`)
- `diff >= 4.0` → `MOVED(x)` ✓ — personaje se movió
- `diff < 4.0` → `STUCK(x)` ⚠ — posible obstáculo
- 4 STUCK consecutivos → aborta automáticamente

---

## 2. Scripts `.in` — Formato y referencia completa

Los scripts `.in` son archivos de texto plano que describen secuencias de acciones
para el bot. Se parsean con `src/script_parser.py` y se ejecutan paso a paso.

### Instrucciones de movimiento

| Instrucción | Sintaxis | Descripción |
|---|---|---|
| `node`   | `node (X,Y,Z)`   | Navega a la coordenada usando A* |
| `stand`  | `stand (X,Y,Z)`  | Navega al tile exacto (preciso, sin A*) |
| `ladder` | `ladder (X,Y,Z)` | Usa la escalera/agujero en ese tile |
| `shovel` | `shovel (X,Y,Z)` | Usa la pala en ese tile |
| `rope`   | `rope (X,Y,Z)`   | Usa la soga en ese tile |
| `depot`  | `depot`          | Ejecuta un ciclo de depot (vaciar mochila, depositar) |

```
node (32377,32222,7)
stand (32378,32222,7)
ladder (32378,32220,6)
shovel (32010,31814,8)
rope   (32010,31814,8)
```

### Control de flujo

| Instrucción | Sintaxis | Descripción |
|---|---|---|
| `label` | `label <nombre>`          | Define un punto de salto nombrado |
| `goto`  | `goto <nombre>`           | Salto incondicional a un label |
| `action`| `action <nombre>`         | Acción especial (ver lista abajo) |
| `wait`  | `wait <segundos>`         | Pausa la ejecución N segundos |

```
label inicio
node (32377,32222,7)
goto inicio

action end
action travel
wait 2.5
```

**Acciones disponibles** para `action`:
- `end` — termina el script
- `travel` — marca el inicio de un viaje (uso interno)
- `wait` — alias de espera
- Cualquier string personalizado se almacena como `action_name`

### Uso de ítems y hotkeys

| Instrucción | Sintaxis | Descripción |
|---|---|---|
| `use_item`   | `use_item <nombre> [vk=N]`  | Usa un ítem por nombre y hotkey opcional |
| `use_hotkey` | `use_hotkey <vk>`           | Presiona una tecla virtual directamente |

El parámetro `vk` acepta decimal o hexadecimal (`0x71`).

```
use_item exura vk=0x70
use_item utevo lux
use_hotkey 0x71
use_hotkey 113
```

### Condicionales (if/goto)

```
if hp < 30 goto huir
if hp > 90 goto atacar
if mp < 20 goto recuperar_mana
if mp > 80 goto lanzar_hechizo
if hp <= 50 goto curar
if mp >= 60 goto continuar
```

**Operadores soportados:** `<`, `>`, `<=`, `>=`

Cuando la condición se cumple, el script salta al label especificado.
Si el label no existe en el script, la instrucción se ignora silenciosamente.

### Diálogo con NPCs

```
call talk_npc("list_words": ["hola", "depositar"], "sentence": "", "var_name": "", "label_jump": "ok", "label_skip": "fallo")
call say("hola")
call conditional_jump_script_options("var_name": "response", "list_words": ["si", "no"], "label_jump": "acepto", "label_skip": "rechace")
```

---

### Ejemplo completo — ruta de caza con healer y loot

```
; === Ruta: Spawn → Centro de caza → Vuelta al depot ===
; Formato de comentarios: punto y coma al inicio de línea

label spawn_salida
node (32377,32222,7)
node (32380,32210,7)

label loot_loop
; Revisar HP antes de continuar
if hp < 25 goto emergencia
if mp < 20 goto recuperar_mana

node (32390,32200,7)
node (32395,32195,7)
use_item exura vk=0x70
wait 0.5
goto loot_loop

label recuperar_mana
use_hotkey 0x71
wait 3.0
goto loot_loop

label emergencia
use_hotkey 0x72
wait 1.0
if hp < 15 goto depot
goto loot_loop

label depot
node (32377,32222,7)
node (32350,32244,7)
action end
```

---

### Coordenadas de Tibia

| Campo | Rango válido | Notas |
|---|---|---|
| X | 31744 – 34048 | Eje horizontal |
| Y | 30976 – 32768 | Eje vertical (norte = valores menores) |
| Z | 0 – 15        | Piso (7 = superficie, 8+ = subsuelo, 0-6 = edificios) |

El parser acepta cualquier entero positivo pero los valores fuera del rango
del mapa de Tibia producirán rutas vacías en el pathfinder.

---

### Parsear un script desde Python

```python
from src.script_parser import ScriptParser

parser = ScriptParser()
instructions = parser.parse_file("routes/mi_ruta.in")

for instr in instructions:
    print(instr.kind, instr.coord or instr.label or instr.action)
```

O desde una cadena de texto:

```python
script = """
node (32377,32222,7)
wait 1.0
label fin
action end
"""
instructions = parser.parse(script)
```


Valores observados en movimiento normal: **diff ≈ 25-30** (muy por encima del umbral).

## Rutas verificadas

| Archivo | Origen | Destino | Pasos | Duración | Fallos | Estado |
|---|---|---|---|---|---|---|
| `thais_spawn_to_depot.json` | Thais spawn (32377,32222,7) | Thais Depot (32256,32244,7) | 153 | ~38s | 0 | ✅ verificada |
| `thais_depot_to_temple.json` | Thais Depot (32256,32244,7) | Temple (32369,32241,7) | 152 | ~28s | 0 | ✅ verificada |
| `thais_temple_to_depot_bank.json` | Temple (32369,32241,7) | Depot & Bank (32347,32226,7) | 53 | ~10s | 0 | ✅ verificada |
| `thais_depot_bank_to_temple.json` | Depot & Bank (32347,32226,7) | Temple (32369,32241,7) | 53 | ~10s | 0 | ✅ verificada |
| `wasp_thais/wasp_thais_ek.json` | Thais (32349,32225,8) | Cavebot (pisos 7-9) | 97 instr. | — | — | 🔧 pendiente test |
| `wasp_thais/wasp_thais_ek_nopvp.json` | Thais (32349,32225,8) | Cavebot EK NoPvP (pisos 7-9) | 96 instr. | — | — | 🔧 pendiente test |
| `wasp_thais/wasp_thais_ek_nopvp_live.json` | Thais (32349,32225,8) | Cavebot EK NoPvP live (pisos 7-9) | 96 instr. | — | — | 🔧 pendiente test |
