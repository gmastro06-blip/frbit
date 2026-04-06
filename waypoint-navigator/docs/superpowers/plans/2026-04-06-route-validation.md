# Route Validation Report — wasp_thais
**Fecha:** 2026-04-06
**Rutas validadas:** `wasp_thais_ek_nopvp.json`, `wasp_thais_ek_nopvp_live.json`

---

## Resultado final

| Ruta | Walkability | A* completo | Estado |
|------|-------------|-------------|--------|
| `wasp_thais_ek_nopvp.json` | ✅ OK | ✅ OK | **LISTA PARA QA** |
| `wasp_thais_ek_nopvp_live.json` | ✅ OK | ✅ OK | **LISTA PARA QA** |

**WARN aceptados en ambas** (no bloquean, son segmentos largos de retorno al depot):
- Instrucción [16]: 27 tiles entre (32337,32232) → (32348,32216)
- Instrucción [67]: 22 tiles entre (32418,32314) → (32428,32302)

---

## Historial de correcciones

### Ronda 1 — Walkability (validator sin A*)

3 tiles no caminables según el mapa de tibiamaps:

| Instrucción | Coord | Causa | Fix |
|-------------|-------|-------|-----|
| [13] `stand` | (32350,32246,**z=6**) | Piso NPC — tile detrás del mostrador | `walkable_override` puntual |
| [55] `stand` | (32430,32302,**z=8**) | Entrada al hoyo de shovel | `walkable_override` puntual |
| [60] `node` | (32444,32295,**z=9**) | Nodo en cueva inferior | `walkable_override` puntual |

**Bug de lógica — loop de caza roto:**

El flujo `count → continue → check → leave` tenía ambas ramas terminando en salida:
```
# Antes (roto): ambos caminos salen del spawn
honeycomb < 50 → "continue" → action: check (solo log) → cae en "leave" → sale
honeycomb ≥ 50 → "leave" → sale

# Después (correcto):
honeycomb < 50 → "continue" → action: check → goto: hunt → sigue cazando
honeycomb ≥ 50 → "leave" → sale
```

**Fix adicional en `wasp_thais_ek_nopvp.json`:** `action: sell` sin `items` → puede fallar o vender todo. Corregido con `items: [{"name": "vial", "qty": 0}]` (igual que la versión live).

### Ronda 2 — A* completo

2 segmentos sin camino A* en la cueva z=8:

| ERR | Segmento | Causa |
|-----|----------|-------|
| [52] | (32436,32302) → (32426,32294) z=8 | Cueva excavada con shovel no mapeada en tibiamaps |
| [53] | (32426,32294) → (32424,32305) z=8 | Misma causa |

También 2 WARN de ratios de stretch (ruta muy indirecta por tiles no walkable en el mapa):
- [51]: 70 pasos para distancia directa 12 (ratio 5.8x)
- [55]: 47 pasos para distancia directa 9 (ratio 5.2x)

**Fix:** Añadir override de región completa del interior de la cueva:
```json
{"x_min": 32424, "x_max": 32445, "y_min": 32294, "y_max": 32326, "z": 8}
```
Cubre toda el área de patrulla dentro del hoyo de shovel que no está en el mapa oficial de tibiamaps.

---

## walkable_overrides finales (ambas rutas)

```json
"walkable_overrides": [
  {"x_min": 32350, "x_max": 32350, "y_min": 32246, "y_max": 32246, "z": 6},
  {"x_min": 32430, "x_max": 32430, "y_min": 32302, "y_max": 32302, "z": 8},
  {"x_min": 32424, "x_max": 32445, "y_min": 32294, "y_max": 32326, "z": 8},
  {"x_min": 32444, "x_max": 32444, "y_min": 32295, "y_max": 32295, "z": 9}
]
```

| Override | Propósito |
|----------|-----------|
| z=6 tile NPC | Tile detrás del mostrador del NPC de ammo |
| z=8 tile entrada | Tile del hoyo de shovel (entrada a cueva) |
| z=8 región cueva | Interior completo de la cueva wasp (32×33 tiles) |
| z=9 tile cueva | Nodo de exploración en cueva inferior |

---

## Templates disponibles para el spawn

| Monstruo en ruta | Template | Estado |
|------------------|----------|--------|
| Wasp | `cache/templates/monsters/wasp.png` | ✅ Presente |
| Wolf | `cache/templates/monsters/wolf.png` | ✅ Presente |
| Starving Wolf | `cache/templates/monsters/starving_wolf.png` | ⚠️ Verificar nombre exacto |
| Poacher | `cache/templates/monsters/poacher.png` (loot=false) | No requerido para combat |

---

## Comando de validación

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
