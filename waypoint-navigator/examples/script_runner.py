"""
script_runner.py
================
Carga un archivo .in de waypoints de frbot, simula la ruta completa
usando A* entre cada par de nodos consecutivos y genera:

  output/run_<script>/
    summary.txt              – resumen completo con todas las instrucciones
    segment_XX_floor_YY.png  – imagen de cada segmento en su piso de mapa
    full_route_animated.gif  – GIF animado de toda la ruta
    stats.json               – estadísticas de la simulación

Uso:
    python examples/script_runner.py --script path/to/waypoints.in
    python examples/script_runner.py --script "c:/Users/gmast/Documents/GitHub/frbot/Waypoints/antiguos/buy_blessing/waypoints.in"
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
import re
warnings.filterwarnings("ignore")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import numpy as np
from PIL import Image, ImageDraw

from src.models import Coordinate, BOUNDS
from src.navigator import WaypointNavigator
from src.script_parser import Instruction, ScriptParser, ScriptCoord


# ── Colores BGR (OpenCV) / RGB (PIL) ─────────────────────────────────────────
_COLS = {
    "node"   : (0,   220, 255),   # cyan
    "stand"  : (0,   180,  80),   # verde
    "ladder" : (255, 160,   0),   # naranja
    "shovel" : (180,  80, 200),   # púrpura
    "rope"   : (255, 255,   0),   # amarillo
    "action" : (200, 200, 200),   # gris
}
_RED    = (255,  60,  60)
_WHITE  = (255, 255, 255)
_YELLOW = (255, 220,   0)
_DARK   = ( 20,  20,  20)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _coord_of(ins: Instruction) -> Coordinate | None:
    if ins.coord:
        return ins.coord.to_tibia_coord()
    return None


def _instrs_with_coord(instructions: list[Instruction]) -> list[Instruction]:
    return [i for i in instructions if i.coord is not None]


# ─────────────────────────────────────────────────────────────────────────────
# Segmentador: agrupa instrucciones por (floor, ciudad aproximada)
# ─────────────────────────────────────────────────────────────────────────────

_CITY_MAP = [
    # (x_min, x_max, y_min, y_max, floor_range, name)
    (32100, 32500, 32100, 32400, (6,7),  "Thais"),
    (32290, 32700, 31700, 32000, (6,7),  "Carlin"),
    (32280, 32800, 31600, 31800, (6,7),  "Ab'Dendriel"),
    (32990, 33200, 31700, 31850, (6,7),  "Edron"),
    (33250, 33400, 31930, 32020, (5,6),  "Edron-Underground"),
    (33260, 33380, 31850, 31950, (6,7),  "Edron-Town"),
    (32550, 32780, 31940, 32050, (11,15),"Kazordoon"),
    (32490, 32620, 31900, 32000, (15,),  "Kaz-Cart"),
    (32480, 32640, 32730, 32850, (6,7),  "Port Hope"),
    (32750, 32950, 32480, 32650, (7,),   "Banuta"),
    (31900, 32450, 31050, 31300, (6,7),  "Svargrond"),
    (33050, 33400, 31850, 32100, (6,7),  "Cormaya"),
]

def _city_name(c: Coordinate) -> str:
    for xmin,xmax,ymin,ymax,floors,name in _CITY_MAP:
        floor_ok = (not floors) or (c.z in floors) or (len(floors)==2 and floors[0]<=c.z<=floors[1])
        if xmin<=c.x<=xmax and ymin<=c.y<=ymax and floor_ok:
            return name
    return f"Floor{c.z:02d}"


def segment_by_floor(instructions: list[Instruction]) -> list[tuple[int, str, list[Instruction]]]:
    """
    Returns list of (floor, city_name, [instructions]) grouped by consecutive floor.
    Floor transitions (ladder/stand with z-change) create a new segment.
    """
    segments: list[tuple[int, str, list[Instruction]]] = []
    current_z: int | None = None
    current_city: str = ""
    buf: list[Instruction] = []

    for ins in instructions:
        c = _coord_of(ins)
        if c is not None:
            city = _city_name(c)
            if c.z != current_z or city != current_city:
                if buf:
                    segments.append((current_z or 7, current_city, buf))
                buf = []
                current_z    = c.z
                current_city = city
        buf.append(ins)

    if buf:
        segments.append((current_z or 7, current_city, buf))

    return segments


# ─────────────────────────────────────────────────────────────────────────────
# Visualizador de segmento
# ─────────────────────────────────────────────────────────────────────────────

SCALE = 6
PAD   = 30    # tiles de margen alrededor de la ruta


def _load_map_region(loader, floor: int, coords: list[Coordinate]):
    px_list = [c.x - BOUNDS["xMin"] for c in coords]
    py_list = [c.y - BOUNDS["yMin"] for c in coords]
    x0 = max(0, min(px_list) - PAD)
    x1 = min(2559, max(px_list) + PAD)
    y0 = max(0, min(py_list) - PAD)
    y1 = min(2047, max(py_list) + PAD)

    path_arr = loader._load_png(f"floor-{floor:02d}-path.png")
    map_arr  = loader._load_png(f"floor-{floor:02d}-map.png")

    rp = path_arr[y0:y1, x0:x1]
    rm = map_arr [y0:y1, x0:x1]

    rh, rw = rp.shape[:2]
    IW, IH = rw * SCALE, rh * SCALE

    # Base: mapa en color
    base = Image.fromarray(rm[:,:,:3], "RGB").resize((IW, IH), Image.Resampling.NEAREST).convert("RGBA")

    # Overlay paredes (amarillo = no caminable)
    r2 = rp[:,:,0].astype(int)
    g2 = rp[:,:,1].astype(int)
    b2 = rp[:,:,2].astype(int)
    is_wall = (r2==255)&(g2==255)&(b2==0)
    ov = np.zeros((rh, rw, 4), dtype=np.uint8)
    ov[is_wall] = [120, 20, 20, 130]
    base.alpha_composite(Image.fromarray(ov, "RGBA").resize((IW, IH), Image.Resampling.NEAREST))
    base = base.convert("RGB")

    return base, x0, y0, IW, IH


def _to_img(c: Coordinate, x0: int, y0: int) -> tuple[int,int]:
    px = (c.x - BOUNDS["xMin"] - x0) * SCALE
    py = (c.y - BOUNDS["yMin"] - y0) * SCALE
    return int(px), int(py)


def visualize_segment(
    seg_idx: int,
    floor: int,
    city: str,
    instructions: list[Instruction],
    nav: WaypointNavigator,
    loader,
    out_dir: Path,
) -> Path | None:
    """
    Renderiza un segmento sobre el mapa real correspondiente.
    Traza la ruta A* entre nodos consecutivos del mismo piso.
    """
    coord_instrs = _instrs_with_coord(instructions)
    coords = [c for i in coord_instrs if (c := _coord_of(i)) and c.z == floor]
    if not coords:
        return None

    try:
        base, x0, y0, IW, IH = _load_map_region(loader, floor, coords)
    except Exception as e:
        print(f"    [vis] No se pudo cargar mapa floor {floor}: {e}")
        return None

    draw = ImageDraw.Draw(base)

    # ── Dibujar ruta A* entre nodos del mismo piso ──────────────────────
    if not nav.is_floor_loaded(floor):
        try:
            nav.load_floor(floor)
        except Exception as e:
            print(f"    [vis] Error cargando piso {floor}: {e}")

    prev_coord: Coordinate | None = None
    for ins in coord_instrs:
        c = _coord_of(ins)
        if c is None or c.z != floor:
            prev_coord = None
            continue

        if prev_coord is not None and prev_coord.z == c.z and nav.is_floor_loaded(floor):
            try:
                route = nav.navigate(prev_coord, c)
                if route.found and len(route.steps) > 1:
                    pts = [_to_img(s, x0, y0) for s in route.steps]
                    col = _COLS.get(ins.kind, (150,150,150))
                    draw.line(pts, fill=col, width=2)
            except Exception:
                # Fallback: línea recta
                a, b = _to_img(prev_coord, x0, y0), _to_img(c, x0, y0)
                draw.line([a, b], fill=(100,100,100), width=1)

        prev_coord = c

    # ── Dibujar marcadores de instrucción ───────────────────────────────
    for ins in coord_instrs:
        c = _coord_of(ins)
        if c is None or c.z != floor:
            continue
        px, py = _to_img(c, x0, y0)
        col  = _COLS.get(ins.kind, (180,180,180))
        size = {"node":3, "stand":4, "ladder":6, "shovel":6, "rope":6}.get(ins.kind, 4)

        if ins.kind in ("ladder","shovel","rope"):
            # Rombo
            draw.polygon([(px,py-size),(px+size,py),(px,py+size),(px-size,py)],
                         fill=col, outline="black")
        else:
            draw.ellipse([px-size,py-size,px+size,py+size], fill=col, outline="black")

    # ── Labels de acciones ───────────────────────────────────────────────
    for ins in instructions:
        if ins.kind == "label":
            # Marcar el primer coord que sigue al label
            pass
        if ins.kind in ("action", "talk_npc", "say"):
            # texto en el último coord antes de la acción
            pass  # evitar saturar la imagen

    # ── Título ───────────────────────────────────────────────────────────
    draw.rectangle([2, 2, IW-2, 22], fill=_DARK)
    draw.text((6, 4), f"Segmento {seg_idx:02d} | {city} | Floor {floor:02d} | {len(coord_instrs)} instrucciones", fill=_YELLOW)

    # ── Leyenda ───────────────────────────────────────────────────────────
    legend_items = [
        ("node",   "● node (A*)"),
        ("stand",  "● stand"),
        ("ladder", "◆ ladder/stairs"),
        ("shovel", "◆ shovel"),
        ("rope",   "◆ rope"),
    ]
    lx = IW - 160
    ly = 28
    draw.rectangle([lx-4, ly-4, IW-2, ly + len(legend_items)*14 + 4], fill=_DARK)
    for kind, label in legend_items:
        col = _COLS.get(kind, _WHITE)
        draw.text((lx, ly), label, fill=col)
        ly += 14

    fname = out_dir / f"segment_{seg_idx:02d}_floor{floor:02d}_{city.replace(' ','_').replace(chr(39),'')}.png"
    base.save(str(fname))
    return fname


# ─────────────────────────────────────────────────────────────────────────────
# Runner principal
# ─────────────────────────────────────────────────────────────────────────────

def run(script_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 62)
    print(f"  Script Runner — {script_path.name}")
    print("=" * 62)

    # ── 1. Parsear el script ─────────────────────────────────────────────
    instructions = ScriptParser.parse_file(script_path)
    print(f"  Instrucciones totales: {len(instructions)}")

    coord_ins   = _instrs_with_coord(instructions)
    floors_used = sorted({c.z for i in coord_ins if (c := _coord_of(i))})
    print(f"  Instrucciones con coord: {len(coord_ins)}")
    print(f"  Pisos usados: {floors_used}")

    labels   = [i for i in instructions if i.kind == "label"]
    actions  = [i for i in instructions if i.kind == "action"]
    dialogs  = [i for i in instructions if i.kind in ("talk_npc","say")]
    ladders  = [i for i in instructions if i.kind == "ladder"]
    print(f"  Labels: {len(labels)} | Acciones: {len(actions)} | "
          f"Dialogos: {len(dialogs)} | Ladders: {len(ladders)}")

    # ── 2. Summary de texto ──────────────────────────────────────────────
    summary_lines = [
        f"Script: {script_path}",
        f"Instrucciones totales: {len(instructions)}",
        f"Pisos usados: {floors_used}",
        "",
        "INSTRUCCIONES COMPLETAS:",
        "-" * 50,
    ]
    for idx, ins in enumerate(instructions):
        prefix = ""
        if ins.kind == "label":
            prefix = f"\n>>> LABEL: {ins.label.upper()} <<<"
            summary_lines.append(prefix)
        elif ins.kind == "action":
            summary_lines.append(f"  [{idx:03d}] ACTION  : {ins.action}")
        elif ins.kind == "talk_npc":
            summary_lines.append(f"  [{idx:03d}] TALK_NPC: {ins.words}")
        elif ins.kind == "say":
            summary_lines.append(f"  [{idx:03d}] SAY     : {ins.sentence!r}")
        elif ins.kind == "cond_jump":
            summary_lines.append(f"  [{idx:03d}] COND_JUMP: var={ins.var_name} -> {ins.label_jump} / {ins.label_skip}")
        elif ins.coord:
            c = _coord_of(ins)
            if c is not None:
                city = _city_name(c)
                summary_lines.append(f"  [{idx:03d}] {ins.kind:7s}: ({c.x},{c.y},{c.z}) [{city}]")
        else:
            summary_lines.append(f"  [{idx:03d}] {ins.kind:7s}: {ins.raw}")

    (out_dir / "summary.txt").write_text("\n".join(summary_lines), encoding="utf-8")
    print(f"\n  Summary guardado -> {out_dir/'summary.txt'}")

    # ── 3. Cargar navigator ──────────────────────────────────────────────
    from src.map_loader import TibiaMapLoader
    loader = TibiaMapLoader()
    nav    = WaypointNavigator()

    # Pre-cargar pisos (solo el 7 es grande, el resto son pequeños)
    for fl in floors_used:
        print(f"  Cargando piso {fl:02d} ...")
        nav.load_floor(fl)

    # ── 4. Segmentar y visualizar ────────────────────────────────────────
    print("\n" + "=" * 62)
    print("  Generando visualizaciones por segmento ...")
    print("=" * 62)

    segments = segment_by_floor(instructions)
    print(f"  Segmentos detectados: {len(segments)}")

    generated: list[Path] = []
    stats: dict = {
        "script": str(script_path),
        "total_instructions": len(instructions),
        "floors": floors_used,
        "segments": [],
        "total_coord_instructions": len(coord_ins),
        "labels": [i.label for i in labels],
        "actions": [i.action for i in actions],
    }

    for seg_idx, (floor, city, seg_instrs) in enumerate(segments):
        ci = _instrs_with_coord(seg_instrs)
        cities_in_seg = set(_city_name(c) for i in ci if (c := _coord_of(i)))
        print(f"\n  [{seg_idx:02d}] Floor {floor:02d} | {city} | {len(ci)} coords | {len(seg_instrs)} instrucciones")

        # Imprimir instrucciones del segmento
        for ins in seg_instrs:
            if ins.kind == "label":
                print(f"       >>> LABEL: {ins.label} <<<")
            elif ins.kind == "action":
                print(f"       ACTION: {ins.action}")
            elif ins.kind == "talk_npc":
                print(f"       TALK_NPC: {ins.words}")
            elif ins.kind == "say":
                print(f"       SAY: {ins.sentence!r}")
            elif ins.kind == "cond_jump":
                print(f"       COND_JUMP: {ins.var_name} -> {ins.label_jump}")
            elif ins.coord:
                c = ins.coord.to_tibia_coord()
                if c:
                    print(f"       {ins.kind:7s} ({c.x},{c.y},{c.z})")

        # Generar imagen
        img_path = visualize_segment(seg_idx, floor, city, seg_instrs, nav, loader, out_dir)
        if img_path:
            generated.append(img_path)
            print(f"       -> {img_path.name}")
        else:
            print(f"       -> (sin imagen)")

        stats["segments"].append({
            "idx": seg_idx, "floor": floor, "city": city,
            "instructions": len(seg_instrs),
            "coord_instructions": len(ci),
            "image": str(img_path) if img_path else None,
        })

    # ── 5. Mapa de ruta completa (todos los pisos juntos) ────────────────
    print("\n" + "=" * 62)
    print("  Generando mapa de ruta completa (floor 7) ...")

    all_floor7 = [c for i in coord_ins if (c := _coord_of(i)) and c.z == 7]
    if len(all_floor7) >= 2:
        try:
            base, x0, y0, IW, IH = _load_map_region(loader, 7, all_floor7)
            draw = ImageDraw.Draw(base)
            prev = None
            for c in all_floor7:
                if prev is not None:
                    try:
                        rt = nav.navigate(prev, c)
                        if rt.found:
                            pts = [_to_img(s, x0, y0) for s in rt.steps]
                            draw.line(pts, fill=(0,200,255), width=2)
                    except Exception:
                        draw.line([_to_img(prev,x0,y0), _to_img(c,x0,y0)], fill=(100,100,100), width=1)
                # Marcador
                px, py = _to_img(c, x0, y0)
                draw.ellipse([px-2,py-2,px+2,py+2], fill=(0,220,255), outline="black")
                prev = c

            draw.rectangle([2,2,IW-2,18], fill=_DARK)
            draw.text((6,3), f"Ruta completa floor 7 | {len(all_floor7)} puntos", fill=_YELLOW)
            fp = out_dir / "full_route_floor7.png"
            base.save(str(fp))
            generated.append(fp)
            print(f"  -> {fp.name}")
        except Exception as e:
            print(f"  Error generando ruta completa: {e}")

    # ── 6. GIF de segmentos ───────────────────────────────────────────────
    print("\n  Generando GIF de segmentos ...")
    if generated:
        try:
            import imageio
            frames = []
            for p in generated:
                img = Image.open(p).convert("RGB")
                # Reducir los frames muy grandes
                max_side = 900
                if img.width > max_side or img.height > max_side:
                    ratio = min(max_side/img.width, max_side/img.height)
                    _lanczos = getattr(Image, 'Resampling', Image).LANCZOS
                    img = img.resize((int(img.width*ratio), int(img.height*ratio)), _lanczos)
                frames.append(np.array(img))

            # Normalizar tamaños al máximo
            max_w = max(f.shape[1] for f in frames)
            max_h = max(f.shape[0] for f in frames)
            padded = []
            for f in frames:
                h, w = f.shape[:2]
                canvas = np.zeros((max_h, max_w, 3), dtype=np.uint8)
                canvas[:h, :w] = f
                padded.append(canvas)

            gif_path = out_dir / "segments_tour.gif"
            with imageio.get_writer(str(gif_path), mode="I", fps=1, loop=0) as wr:
                for f in padded:
                    wr.append_data(f)  # type: ignore[attr-defined]
            print(f"  -> {gif_path.name}  ({len(padded)} frames)")
        except Exception as e:
            print(f"  Error generando GIF: {e}")

    # ── 7. Stats JSON ─────────────────────────────────────────────────────
    stats_path = out_dir / "stats.json"
    stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── 8. Resumen final ──────────────────────────────────────────────────
    print("\n" + "=" * 62)
    print("  COMPLETADO")
    print("=" * 62)
    print(f"  Script             : {script_path.name}")
    print(f"  Instrucciones      : {len(instructions)}")
    print(f"  Segmentos          : {len(segments)}")
    print(f"  Imagenes generadas : {len(generated)}")
    print(f"  Output dir         : {out_dir}")
    print("=" * 62)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="script_runner",
        description="Ejecuta y visualiza un archivo .in de waypoints frbot",
    )
    p.add_argument("--script", required=True, help="Ruta al archivo .in")
    p.add_argument("--out", default="", help="Directorio de salida (default: output/run_<nombre>)")
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    script_path = Path(args.script)
    if not script_path.exists():
        print(f"ERROR: No se encontro el archivo: {script_path}")
        sys.exit(1)

    out_base = Path(__file__).parent.parent / "output"
    out_dir  = Path(args.out) if args.out else out_base / f"run_{script_path.stem}"

    run(script_path, out_dir)
