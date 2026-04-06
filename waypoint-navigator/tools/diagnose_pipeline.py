from __future__ import annotations

import ctypes
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

import cv2
import numpy as np

from src.combat_manager import BattleDetector, CombatConfig
from src.condition_monitor import ConditionConfig, ConditionDetector
from src.detector_config import DetectorConfig
from src.frame_capture import build_frame_getter
from src.frame_sources import OBSWebSocketSource, VirtualCameraSource
from src.hpmp_detector import HpMpConfig, HpMpDetector
from src.input_controller import find_window
from src.map_loader import TibiaMapLoader
from src.minimap_radar import MinimapConfig, MinimapRadar


ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_REF_W = 1920
_DEFAULT_REF_H = 1080


class FrameGetter(Protocol):
    def __call__(self) -> Optional[np.ndarray]:
        ...

    def close(self) -> None:
        ...


@dataclass
class DetectorSnapshot:
    ok: bool
    summary: str
    details: dict[str, Any]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def _attach_close(fn: Callable[[], Optional[np.ndarray]], close_fn: Callable[[], None]) -> FrameGetter:
    fn.close = close_fn  # type: ignore[attr-defined]
    return fn  # type: ignore[return-value]


def _find_projector_window() -> Any | None:
    for title in ("Proyector", "Projector"):
        info = find_window(title)
        if info is not None:
            return info
    return None


def _monitor_idx_for_hwnd(hwnd: int) -> int | None:
    try:
        import mss

        user32 = ctypes.windll.user32
        monitor = user32.MonitorFromWindow(hwnd, 2)
        if not monitor:
            return None

        monitor_info = _MONITORINFO()
        monitor_info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(monitor_info)):
            return None

        target_rect = (
            monitor_info.rcMonitor.left,
            monitor_info.rcMonitor.top,
            monitor_info.rcMonitor.right,
            monitor_info.rcMonitor.bottom,
        )

        with mss.mss() as sct:
            for idx in range(1, len(sct.monitors)):
                mon = sct.monitors[idx]
                rect = (
                    int(mon["left"]),
                    int(mon["top"]),
                    int(mon["left"] + mon["width"]),
                    int(mon["top"] + mon["height"]),
                )
                if rect == target_rect:
                    return idx
    except Exception:
        return None

    return None


def _make_frame_getter(source: str, window_title: str) -> FrameGetter:
    source_key = source.lower().strip()
    projector_info = _find_projector_window()

    if source_key == "obs-ws":
        cfg = DetectorConfig.load()
        obs_capture = OBSWebSocketSource(cfg)
        obs_capture.connect()
        return _attach_close(obs_capture.get_frame, obs_capture.disconnect)

    if source_key == "virtual-cam":
        cfg = DetectorConfig.load()
        camera_capture = VirtualCameraSource(cfg.obs_cam_index)
        camera_capture.connect()
        for _ in range(5):
            camera_capture.get_frame()
        return _attach_close(camera_capture.get_frame, camera_capture.disconnect)

    if source_key == "wgc":
        title = window_title or "Proyector"
        info = find_window(title)
        if info is None and projector_info is not None:
            info = projector_info
        if info is None:
            raise RuntimeError(f"Window not found for WGC capture: {title}")
        return build_frame_getter("wgc", hwnd=info.hwnd)  # type: ignore[return-value]

    if source_key == "mss":
        title = window_title or "Proyector"
        info = find_window(title)
        if info is None and projector_info is not None:
            info = projector_info
        if info is not None:
            monitor_idx = _monitor_idx_for_hwnd(info.hwnd)
            if monitor_idx is not None:
                return build_frame_getter("mss", monitor_idx=monitor_idx)  # type: ignore[return-value]
            return build_frame_getter("mss", hwnd=info.hwnd)  # type: ignore[return-value]
        return build_frame_getter("mss")  # type: ignore[return-value]

    if source_key == "screen":
        if projector_info is not None:
            monitor_idx = _monitor_idx_for_hwnd(projector_info.hwnd)
            if monitor_idx is not None:
                return build_frame_getter("mss", monitor_idx=monitor_idx)  # type: ignore[return-value]
        return build_frame_getter("mss")  # type: ignore[return-value]

    raise ValueError(f"Unsupported diagnose source: {source}")


def _capture_frame(getter: FrameGetter, attempts: int = 15, delay_s: float = 0.2) -> np.ndarray:
    frame: Optional[np.ndarray] = None
    for _ in range(attempts):
        frame = getter()
        if frame is not None:
            return frame
        time.sleep(delay_s)
    raise RuntimeError("Unable to capture a frame from the selected source")


def _scale_roi(roi: list[int], ref_w: int, ref_h: int, frame: np.ndarray) -> tuple[int, int, int, int]:
    frame_h, frame_w = frame.shape[:2]
    rx = frame_w / ref_w
    ry = frame_h / ref_h
    x, y, w, h = roi
    return int(x * rx), int(y * ry), int(w * rx), int(h * ry)


def _draw_roi(frame: np.ndarray, roi: tuple[int, int, int, int], label: str, color: tuple[int, int, int]) -> None:
    x, y, w, h = roi
    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
    cv2.putText(frame, label, (x, max(18, y - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)


def _diagnose_minimap(frame: np.ndarray) -> DetectorSnapshot:
    cfg = MinimapConfig.load()
    loader = TibiaMapLoader()
    radar = MinimapRadar(loader=loader, config=cfg)
    coord = radar.read(frame)
    ok = coord is not None
    return DetectorSnapshot(
        ok=ok,
        summary=str(coord) if coord is not None else "No position detected",
        details={
            "floor": cfg.floor,
            "confidence_threshold": cfg.confidence,
            "roi": cfg.roi,
            "position": None if coord is None else {"x": coord.x, "y": coord.y, "z": coord.z},
        },
    )


def _diagnose_hpmp(frame: np.ndarray) -> DetectorSnapshot:
    cfg = HpMpConfig.load()
    detector = HpMpDetector(cfg)
    hp, mp = detector.read_bars(frame)
    ok = hp is not None or mp is not None
    return DetectorSnapshot(
        ok=ok,
        summary=f"HP={hp if hp is not None else '?'} MP={mp if mp is not None else '?'}",
        details={
            "hp": hp,
            "mp": mp,
            "hp_roi": cfg.hp_roi,
            "mp_roi": cfg.mp_roi,
            "ref_width": _DEFAULT_REF_W,
            "ref_height": _DEFAULT_REF_H,
        },
    )


def _diagnose_battle(frame: np.ndarray) -> DetectorSnapshot:
    cfg = CombatConfig.load()
    detector = BattleDetector(cfg)
    detections = detector.detect_ocr(frame) if cfg.ocr_detection else detector.detect(frame)
    names = [name for _, _, _, name in detections]
    return DetectorSnapshot(
        ok=True,
        summary=f"{len(detections)} detections",
        details={
            "ocr_detection": cfg.ocr_detection,
            "battle_list_roi": cfg.battle_list_roi,
            "detections": [
                {"x": x, "y": y, "confidence": confidence, "name": name}
                for x, y, confidence, name in detections
            ],
            "names": names,
        },
    )


def _diagnose_conditions(frame: np.ndarray) -> DetectorSnapshot:
    cfg = ConditionConfig.load()
    detector = ConditionDetector(cfg)
    conditions = sorted(detector.detect(frame))
    return DetectorSnapshot(
        ok=True,
        summary=", ".join(conditions) if conditions else "No active conditions",
        details={
            "condition_icons_roi": cfg.condition_icons_roi,
            "detection_mode": cfg.detection_mode,
            "conditions": conditions,
        },
    )


def _annotate_frame(
    frame: np.ndarray,
    minimap: DetectorSnapshot,
    hpmp: DetectorSnapshot,
    battle: DetectorSnapshot,
    conditions: DetectorSnapshot,
) -> np.ndarray:
    annotated = frame.copy()

    minimap_cfg = MinimapConfig.load()
    hpmp_cfg = HpMpConfig.load()
    combat_cfg = CombatConfig.load()
    condition_cfg = ConditionConfig.load()

    _draw_roi(annotated, _scale_roi(minimap_cfg.roi, _DEFAULT_REF_W, _DEFAULT_REF_H, annotated), "minimap", (0, 255, 255))
    _draw_roi(annotated, _scale_roi(hpmp_cfg.hp_roi, _DEFAULT_REF_W, _DEFAULT_REF_H, annotated), "hp", (0, 0, 255))
    _draw_roi(annotated, _scale_roi(hpmp_cfg.mp_roi, _DEFAULT_REF_W, _DEFAULT_REF_H, annotated), "mp", (255, 0, 0))
    _draw_roi(annotated, _scale_roi(combat_cfg.battle_list_roi, combat_cfg.ref_width, combat_cfg.ref_height, annotated), "battle", (0, 165, 255))
    _draw_roi(annotated, _scale_roi(condition_cfg.condition_icons_roi, condition_cfg.ref_width, condition_cfg.ref_height, annotated), "conditions", (0, 255, 0))

    lines = [
        f"Minimap: {minimap.summary}",
        f"HP/MP: {hpmp.summary}",
        f"Battle: {battle.summary}",
        f"Conditions: {conditions.summary}",
    ]
    y = 24
    for line in lines:
        cv2.putText(annotated, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(annotated, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (25, 25, 25), 1, cv2.LINE_AA)
        y += 24

    battle_details = battle.details.get("detections", [])
    for item in battle_details:
        cx = int(item["x"])
        cy = int(item["y"])
        label = str(item["name"])
        cv2.circle(annotated, (cx, cy), 4, (0, 165, 255), -1)
        cv2.putText(annotated, label, (cx + 6, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 165, 255), 1, cv2.LINE_AA)

    return annotated


def run_diagnose(
    source: str,
    window_title: str,
    output_dir: str = "output",
    save_json: bool = True,
    save_image: bool = True,
    show: bool = False,
) -> dict[str, Any]:
    """Capture one frame, run key detectors, and persist a diagnostic report."""
    out_dir = ROOT / output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    getter = _make_frame_getter(source=source, window_title=window_title)
    try:
        frame = _capture_frame(getter)
    finally:
        getter.close()

    minimap = _diagnose_minimap(frame)
    hpmp = _diagnose_hpmp(frame)
    battle = _diagnose_battle(frame)
    conditions = _diagnose_conditions(frame)

    report: dict[str, Any] = {
        "source": source,
        "window_title": window_title,
        "frame": {
            "width": int(frame.shape[1]),
            "height": int(frame.shape[0]),
            "channels": int(frame.shape[2]) if frame.ndim == 3 else 1,
        },
        "detectors": {
            "minimap": asdict(minimap),
            "hpmp": asdict(hpmp),
            "battle": asdict(battle),
            "conditions": asdict(conditions),
        },
    }

    annotated = _annotate_frame(frame, minimap, hpmp, battle, conditions)

    if save_image:
        cv2.imwrite(str(out_dir / "diagnose_frame.png"), frame)
        cv2.imwrite(str(out_dir / "diagnose_overlay.png"), annotated)

    if save_json:
        (out_dir / "diagnose_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    print("[diagnose] Frame captured:", f"{frame.shape[1]}x{frame.shape[0]}")
    print("[diagnose] Minimap:", minimap.summary)
    print("[diagnose] HP/MP:", hpmp.summary)
    print("[diagnose] Battle:", battle.summary)
    print("[diagnose] Conditions:", conditions.summary)
    if save_json:
        print(f"[diagnose] JSON report: {out_dir / 'diagnose_report.json'}")
    if save_image:
        print(f"[diagnose] Overlay image: {out_dir / 'diagnose_overlay.png'}")

    if show:
        cv2.imshow("diagnose overlay", annotated)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    return report