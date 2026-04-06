"""
capture_templates.py
====================
Captura templates de monstruos (battle list), cadáveres (viewport)
e ítems de loot (contenedor) directamente desde un frame de OBS.

Uso interactivo:
    python examples/capture_templates.py --source obs-ws --type monster
    python examples/capture_templates.py --source obs-ws --type corpse
    python examples/capture_templates.py --source obs-ws --type item

Controles en la ventana de previsualización:
    Arrastra el ratón  → dibuja un rectángulo de selección
    ENTER / S          → guarda el recorte seleccionado
    R                  → repetir captura (nuevo frame de OBS)
    ESC / Q            → salir

El script guarda los PNG en:
    cache/templates/monsters/   (type=monster)
    cache/templates/corpses/    (type=corpse)
    cache/templates/loot_items/ (type=item)
    cache/templates/conditions/ (type=condition)

--roi-hint  muestra el ROI de la battle list / viewport encima del frame
            para guiarte donde mirar.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.character_detector import DetectorConfig, OBSWebSocketSource


# ─── Captura de pantalla directa (sin OBS) ────────────────────────────────
def get_frame_screenshot(monitor_idx: int = 1) -> np.ndarray | None:
    """Captura un monitor específico usando mss (sin OBS).

    monitor_idx: 1 = monitor principal, 2 = segundo monitor, etc.
    (mss.monitors[0] es el virtual que une todos los monitores)
    """
    try:
        import mss
        with mss.mss() as sct:
            total = len(sct.monitors) - 1  # -1 porque el índice 0 es el virtual
            if monitor_idx < 1 or monitor_idx > total:
                print(f"  ⚠ Monitor {monitor_idx} no existe. Monitores disponibles: 1–{total}")
                print(f"     Usando monitor 1 como fallback.")
                monitor_idx = 1
            monitor = sct.monitors[monitor_idx]
            print(f"  Capturando monitor {monitor_idx}: "
                  f"{monitor['width']}×{monitor['height']} "
                  f"@ ({monitor['left']},{monitor['top']})")
            sct_img = sct.grab(monitor)
            frame = np.array(sct_img, dtype=np.uint8)
            # mss devuelve BGRA → convertir a BGR
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR).astype(np.uint8)
            return frame
    except ImportError:
        print("  ⚠ mss no instalado. Ejecuta: pip install mss")
        return None
    except Exception as exc:
        print(f"  ✗ Error al capturar pantalla: {exc}")
        return None

# ─── Directorios de destino ────────────────────────────────────────────────
_TYPE_DIRS = {
    "monster":   project_root / "cache" / "templates" / "monsters",
    "corpse":    project_root / "cache" / "templates" / "corpses",
    "item":      project_root / "cache" / "templates" / "loot_items",
    "condition": project_root / "cache" / "templates" / "conditions",
}

# ROIs de referencia para cada tipo (x, y, w, h en 1920×1080)
_TYPE_ROI_HINTS = {
    "monster":   [1699, 480, 210, 400],   # battle list
    "corpse":    [0,    0,  1460, 1080],  # viewport completo
    "item":      [1470, 500, 420, 400],   # contenedor de loot
    "condition": [1709, 462, 200, 30],    # barra de condiciones
}

_REF_W = 1920
_REF_H = 1080


# ─── Captura de frame ──────────────────────────────────────────────────────
def get_frame_obs(args) -> np.ndarray | None:
    cfg = DetectorConfig(
        obs_ws_host=args.obs_host,
        obs_ws_port=args.obs_port,
        obs_ws_password=args.obs_password,
    )
    if args.obs_scene_source:
        cfg.obs_source = args.obs_scene_source
    src = OBSWebSocketSource(cfg, capture_width=0)
    try:
        src.connect()
        print("  OBS conectado. Capturando frame…")
        for _ in range(15):
            frame = src.get_frame()
            if frame is not None and frame.size > 0:
                print(f"  Frame: {frame.shape[1]}×{frame.shape[0]}")
                return frame
            time.sleep(0.3)
        print("  ⚠ No se recibió frame de OBS.")
        return None
    finally:
        try:
            src.disconnect()
        except Exception:
            pass


# ─── Selector interactivo de recortes (Tkinter — sin dependencia de GUI OpenCV)
class TemplateSelector:
    """
    Ventana Tkinter con selección de recorte rectangular por arrastre del ratón.
    Usa PIL para renderizar la imagen — no requiere opencv-python con GUI.
    """

    def __init__(
        self,
        frame: np.ndarray,
        dest_dir: Path,
        roi_hint: list[int] | None,
        template_type: str,
    ) -> None:
        self._orig   = frame.copy()
        self._dest   = dest_dir
        self._roi    = roi_hint
        self._ttype  = template_type

        self._start_pt: tuple[int, int] | None = None
        self._cur_pt:   tuple[int, int] | None = None
        self._rect_id   = None
        self._saved     = 0
        self._result    = "exit"   # 'exit' | 'refresh'

        # Escala de visualización (máx 1280 de ancho)
        h, w = frame.shape[:2]
        self._scale = min(1.0, 1280 / w, 900 / h)
        self._disp_w = int(w * self._scale)
        self._disp_h = int(h * self._scale)

    # ── Conversión coordenadas canvas → píxeles originales ─────────────────
    def _to_orig(self, cx: int, cy: int) -> tuple[int, int]:
        return int(cx / self._scale), int(cy / self._scale)

    # ── Construir imagen PIL con overlays ──────────────────────────────────
    def _build_pil(self) -> "Image.Image":
        from PIL import ImageDraw, ImageFont
        # BGR → RGB
        rgb = cv2.cvtColor(self._orig, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb).resize(
            (self._disp_w, self._disp_h),
            getattr(Image, "Resampling", Image).LANCZOS,
        )
        draw = ImageDraw.Draw(img, "RGBA")

        # ROI hint (verde)
        if self._roi:
            h, w = self._orig.shape[:2]
            sx, sy = self._disp_w / w, self._disp_h / h
            x, y, rw, rh = self._roi
            draw.rectangle(
                [int(x*sx), int(y*sy), int((x+rw)*sx), int((y+rh)*sy)],
                outline=(0, 220, 80), width=2,
            )
            draw.text((int(x*sx)+4, int(y*sy)+4), f"ROI {self._ttype}", fill=(0, 220, 80))

        # Selección actual (azul semi-transparente)
        if self._start_pt and self._cur_pt:
            x0 = int(self._start_pt[0] * self._scale)
            y0 = int(self._start_pt[1] * self._scale)
            x1 = int(self._cur_pt[0] * self._scale)
            y1 = int(self._cur_pt[1] * self._scale)
            draw.rectangle([x0, y0, x1, y1], outline=(255, 100, 0), width=2,
                           fill=(255, 100, 0, 40))

        # Instrucciones
        lines = [
            "Arrastra: seleccionar recorte",
            "S/Enter : guardar template",
            "R       : nuevo frame",
            "Q/Esc   : salir",
            f"Guardados: {self._saved}",
        ]
        for i, line in enumerate(lines):
            draw.text((10, 10 + i * 18), line, fill=(220, 220, 40))

        return img

    # ── Guardar recorte seleccionado ───────────────────────────────────────
    def _save_selection(self) -> None:
        import tkinter.simpledialog as sd
        if self._start_pt is None or self._cur_pt is None:
            print("  Primero selecciona una región arrastrando el ratón.")
            return
        x0, y0 = self._start_pt
        x1, y1 = self._cur_pt
        if abs(x1 - x0) < 4 or abs(y1 - y0) < 4:
            print("  Selección demasiado pequeña.")
            return

        lx, rx = (x0, x1) if x0 < x1 else (x1, x0)
        ty, by = (y0, y1) if y0 < y1 else (y1, y0)
        crop = self._orig[ty:by, lx:rx]

        self._dest.mkdir(parents=True, exist_ok=True)
        existing = sorted(self._dest.glob(f"{self._ttype}_*.png"))
        idx = len(existing) + 1
        default_name = f"{self._ttype}_{idx}"
        name = sd.askstring(
            "Guardar template",
            f"Nombre del template:",
            initialvalue=default_name,
        )
        if name is None:   # usuario canceló
            return
        name = name.strip() or default_name
        if not name.endswith(".png"):
            name += ".png"

        out_path = self._dest / name
        cv2.imwrite(str(out_path), crop)
        self._saved += 1
        print(f"  ✓ Guardado: {out_path}  ({crop.shape[1]}×{crop.shape[0]} px)")
        # Reset selección
        self._start_pt = None
        self._cur_pt   = None

    # ── Loop principal Tkinter ─────────────────────────────────────────────
    def run(self) -> str:
        import tkinter as tk
        from PIL import ImageTk

        root = tk.Tk()
        root.title(f"Captura de template — {self._ttype}  |  S=guardar  R=refresh  Q=salir")
        root.resizable(True, True)

        canvas = tk.Canvas(root, width=self._disp_w, height=self._disp_h,
                           cursor="crosshair", bg="black")
        canvas.pack(fill="both", expand=True)

        # Estado de imagen en canvas
        _tk_img: list = [None]

        def _refresh_canvas():
            pil = self._build_pil()
            _tk_img[0] = ImageTk.PhotoImage(pil)
            canvas.delete("all")
            canvas.create_image(0, 0, anchor="nw", image=_tk_img[0])

        # Eventos de ratón
        def _on_press(e):
            self._start_pt = self._to_orig(e.x, e.y)
            self._cur_pt   = self._start_pt
            _refresh_canvas()

        def _on_drag(e):
            self._cur_pt = self._to_orig(e.x, e.y)
            _refresh_canvas()

        def _on_release(e):
            self._cur_pt = self._to_orig(e.x, e.y)
            _refresh_canvas()

        canvas.bind("<ButtonPress-1>",   _on_press)
        canvas.bind("<B1-Motion>",       _on_drag)
        canvas.bind("<ButtonRelease-1>", _on_release)

        # Teclas
        def _on_key(e):
            k = e.keysym.lower()
            if k in ("escape", "q"):
                self._result = "exit"
                root.destroy()
            elif k in ("return", "s"):
                self._save_selection()
                _refresh_canvas()
            elif k == "r":
                self._result = "refresh"
                root.destroy()

        root.bind("<Key>", _on_key)

        _refresh_canvas()
        root.mainloop()
        return self._result


# ─── Main ──────────────────────────────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(
        description="Captura templates de monstruos, cadáveres, ítems o condiciones"
    )
    ap.add_argument(
        "--type", default="monster",
        choices=["monster", "corpse", "item", "condition"],
        help="Tipo de template a capturar",
    )
    ap.add_argument("--source",           default="obs-ws", choices=["obs-ws"])
    ap.add_argument("--obs-host",         default="localhost")
    ap.add_argument("--obs-port",         type=int, default=4455)
    ap.add_argument("--obs-password",     default="")
    ap.add_argument("--obs-scene-source", default="",
                    help="Nombre de la fuente / escena en OBS")
    ap.add_argument("--no-roi-hint",      action="store_true",
                    help="Ocultar el rectángulo de ROI de referencia")
    ap.add_argument("--image",            default="",
                    help="Usar imagen local en vez de OBS (ruta al PNG/JPG)")
    ap.add_argument("--screenshot",       action="store_true",
                    help="Capturar la pantalla completa ahora mismo (sin OBS, sin fichero)")
    ap.add_argument("--monitor",           type=int, default=2,
                    help="Número de monitor a capturar con --screenshot (1=principal, 2=segundo…)")
    args = ap.parse_args()

    dest_dir  = _TYPE_DIRS[args.type]
    roi_hint  = None if args.no_roi_hint else _TYPE_ROI_HINTS[args.type]

    print(f"\n  Modo: {args.type.upper()}  →  {dest_dir}")
    print(f"  Arrastra en la ventana para seleccionar el icono.")
    print(f"  Presiona ENTER/S para guardar, R para nuevo frame, ESC para salir.\n")

    while True:
        # Obtener frame
        if args.screenshot:
            print(f"  Capturando monitor {args.monitor} en 2 segundos — pon Tibia en primer plano…")
            time.sleep(2)
            frame = get_frame_screenshot(args.monitor)
            if frame is None:
                return
        elif args.image:
            img_path = Path(args.image)
            if not img_path.is_absolute():
                img_path = (project_root / img_path).resolve()
            if not img_path.exists():
                print(f"  ✗ Fichero no encontrado: {img_path}")
                return
            frame = cv2.imdecode(
                np.frombuffer(img_path.read_bytes(), dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            if frame is None:
                print(f"  ✗ No se pudo decodificar: {img_path}")
                return
        else:
            frame = get_frame_obs(args)
            if frame is None:
                print("  ✗ Sin frame de OBS.")
                return

        selector = TemplateSelector(
            frame=frame,
            dest_dir=dest_dir,
            roi_hint=roi_hint,
            template_type=args.type,
        )
        result = selector.run()

        if result == "exit":
            break
        # result == "refresh" → volver a capturar

    print(f"\n  Sesión terminada. Templates guardados en: {dest_dir}")


if __name__ == "__main__":
    main()
