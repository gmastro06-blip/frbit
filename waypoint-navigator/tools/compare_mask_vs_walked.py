"""Compare walkability mask vs actual walked positions from the last run.

Generates: output/mask_vs_walked.png
  - Background: walkability mask (green=walkable, red=wall, black=non-walkable)
  - White dots: actual walked positions (from log)
  - Yellow crosses: route waypoints
  - Cyan line: planned A* path between waypoints
  - Prints summary: how many actual positions landed on walls vs walkable tiles
"""
import re
import sys
from pathlib import Path

import cv2
import numpy as np

# ── project paths ──
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.map_loader import TibiaMapLoader
from src.models import BOUNDS, Coordinate
from src.pathfinder import AStarPathfinder

XMIN = BOUNDS["xMin"]  # 31744
YMIN = BOUNDS["yMin"]  # 30976

# ── Config: area of interest around Thais route ──
# Tile coords encompassing the full route plus a generous margin
AREA_X = (32330, 32395)
AREA_Y = (32205, 32250)
SCALE = 6  # pixels per tile in output image


def extract_walked_positions(log_path: Path, after_time: str = "16:07") -> list[tuple[int, int]]:
    """Parse log and return list of (x, y) actual positions."""
    positions = []
    pattern = re.compile(r"\[walk\] step.*actual=\((\d+),(\d+)\)")
    time_pattern = re.compile(r"(\d{2}:\d{2}:\d{2})")
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            tm = time_pattern.search(line)
            if tm and tm.group(1) < after_time:
                continue
            m = pattern.search(line)
            if m:
                positions.append((int(m.group(1)), int(m.group(2))))
    return positions


def extract_route_waypoints(route_path: Path) -> list[tuple[int, int]]:
    """Extract (x, y) coordinates from route JSON 'stand' instructions."""
    import json
    with open(route_path) as f:
        data = json.load(f)
    wps = []
    for inst in data.get("script", []):
        if inst.get("kind") == "stand":
            wps.append((inst["x"], inst["y"]))
    return wps


def main():
    loader = TibiaMapLoader(log_fn=print)
    walkability = loader.get_walkability(7)  # 2D bool array

    # ── Build color image of the walkability mask ──
    ax0, ax1 = AREA_X
    ay0, ay1 = AREA_Y
    w_tiles = ax1 - ax0 + 1
    h_tiles = ay1 - ay0 + 1
    img_w = w_tiles * SCALE
    img_h = h_tiles * SCALE

    # Create RGB image: green=walkable, dark red=wall (yellow in path png)
    img = np.zeros((img_h, img_w, 3), dtype=np.uint8)
    for ty in range(ay0, ay1 + 1):
        for tx in range(ax0, ax1 + 1):
            px = tx - XMIN
            py = ty - YMIN
            if 0 <= py < walkability.shape[0] and 0 <= px < walkability.shape[1]:
                is_walk = walkability[py, px]
            else:
                is_walk = False

            # tile rectangle in image
            ix = (tx - ax0) * SCALE
            iy = (ty - ay0) * SCALE
            if is_walk:
                img[iy:iy+SCALE, ix:ix+SCALE] = (40, 80, 40)   # dark green
            else:
                img[iy:iy+SCALE, ix:ix+SCALE] = (30, 30, 110)   # dark red

    # ── Draw grid lines ──
    for tx in range(ax0, ax1 + 2):
        ix = (tx - ax0) * SCALE
        if 0 <= ix < img_w:
            cv2.line(img, (ix, 0), (ix, img_h - 1), (50, 50, 50), 1)
    for ty in range(ay0, ay1 + 2):
        iy = (ty - ay0) * SCALE
        if 0 <= iy < img_h:
            cv2.line(img, (0, iy), (img_w - 1, iy), (50, 50, 50), 1)

    # ── Draw planned A* paths between waypoints ──
    route_path = ROOT / "routes" / "thais_rat_hunt.json"
    waypoints = extract_route_waypoints(route_path)
    walkability = loader.get_walkability(7)
    pathfinder = AStarPathfinder(walkability)

    print(f"\n=== A* paths between {len(waypoints)} waypoints ===")
    for i in range(len(waypoints) - 1):
        sx, sy = waypoints[i]
        ex, ey = waypoints[i + 1]
        start = Coordinate(sx, sy, 7)
        end = Coordinate(ex, ey, 7)
        route = pathfinder.find_path(start, end)
        if route.found and route.steps:
            path = route.steps
            for j in range(len(path) - 1):
                p1 = path[j]
                p2 = path[j + 1]
                cx1 = (p1.x - ax0) * SCALE + SCALE // 2
                cy1 = (p1.y - ay0) * SCALE + SCALE // 2
                cx2 = (p2.x - ax0) * SCALE + SCALE // 2
                cy2 = (p2.y - ay0) * SCALE + SCALE // 2
                cv2.line(img, (cx1, cy1), (cx2, cy2), (255, 200, 0), 1)  # cyan=planned

    # ── Draw route waypoints (yellow crosses) ──
    for wx, wy in waypoints:
        cx = (wx - ax0) * SCALE + SCALE // 2
        cy = (wy - ay0) * SCALE + SCALE // 2
        arm = SCALE
        cv2.line(img, (cx - arm, cy), (cx + arm, cy), (0, 255, 255), 2)  # yellow
        cv2.line(img, (cx, cy - arm), (cx, cy + arm), (0, 255, 255), 2)

    # ── Draw actual walked positions ──
    log_path = ROOT / "logs" / "app.log"
    walked = extract_walked_positions(log_path, after_time="16:07")
    print(f"\n=== Walked positions: {len(walked)} steps ===")

    n_walkable = 0
    n_wall = 0
    wall_positions = []
    for i, (wx, wy) in enumerate(walked):
        px = wx - XMIN
        py = wy - YMIN
        if 0 <= py < walkability.shape[0] and 0 <= px < walkability.shape[1]:
            is_walk = walkability[py, px]
        else:
            is_walk = False

        if is_walk:
            n_walkable += 1
        else:
            n_wall += 1
            wall_positions.append((wx, wy))

        # draw on image
        if ax0 <= wx <= ax1 and ay0 <= wy <= ay1:
            cx = (wx - ax0) * SCALE + SCALE // 2
            cy = (wy - ay0) * SCALE + SCALE // 2
            color = (255, 255, 255) if is_walk else (0, 0, 255)  # white=ok, red=wall
            cv2.circle(img, (cx, cy), max(2, SCALE // 2), color, -1)

            # Connect sequential positions with a line
            if i > 0:
                pwx, pwy = walked[i - 1]
                if ax0 <= pwx <= ax1 and ay0 <= pwy <= ay1:
                    pcx = (pwx - ax0) * SCALE + SCALE // 2
                    pcy = (pwy - ay0) * SCALE + SCALE // 2
                    cv2.line(img, (pcx, pcy), (cx, cy), color, 1)

    # ── Summary ──
    print(f"\n{'='*60}")
    print(f"Total walked steps:   {len(walked)}")
    print(f"On walkable tiles:    {n_walkable}  ({100*n_walkable/max(len(walked),1):.1f}%)")
    print(f"On WALL tiles:        {n_wall}  ({100*n_wall/max(len(walked),1):.1f}%)")
    if wall_positions:
        unique_walls = sorted(set(wall_positions))
        print(f"Unique wall positions: {len(unique_walls)}")
        for wp in unique_walls[:20]:
            print(f"  WALL: ({wp[0]}, {wp[1]})")

    # ── Check for "drift" — actual position far from planned direction ──
    print(f"\n=== Drift analysis (planned direction vs actual movement) ===")
    drift_pattern = re.compile(
        r"\[walk\] step (\d+)/(\d+): d=\((-?\d+),(-?\d+)\).*actual=\((\d+),(\d+)\)"
    )
    drifts = []
    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "16:07" <= line[11:16] <= "16:30":
                m = drift_pattern.search(line)
                if m:
                    step_i = int(m.group(1))
                    dx_planned = int(m.group(3))
                    dy_planned = int(m.group(4))
                    ax = int(m.group(5))
                    ay = int(m.group(6))
                    drifts.append((step_i, dx_planned, dy_planned, ax, ay))

    if len(drifts) >= 2:
        wrong_dir = 0
        total_moves = 0
        for i in range(1, len(drifts)):
            _, dx_p, dy_p, ax, ay = drifts[i]
            _, _, _, pax, pay = drifts[i - 1]
            actual_dx = ax - pax
            actual_dy = ay - pay
            total_moves += 1
            # Check if actual move opposes planned direction
            if (dx_p != 0 and actual_dx != 0 and
                    (dx_p > 0) != (actual_dx > 0)):
                wrong_dir += 1
            elif (dy_p != 0 and actual_dy != 0 and
                    (dy_p > 0) != (actual_dy > 0)):
                wrong_dir += 1
        print(f"Total moves analyzed: {total_moves}")
        print(f"Wrong-direction moves: {wrong_dir} ({100*wrong_dir/max(total_moves,1):.1f}%)")

    # ── Legend ──
    legend_y = 10
    cv2.putText(img, "GREEN: walkable", (5, legend_y), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (40, 200, 40), 1)
    legend_y += 14
    cv2.putText(img, "RED bg: wall", (5, legend_y), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (50, 50, 200), 1)
    legend_y += 14
    cv2.putText(img, "YELLOW +: waypoints", (5, legend_y), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)
    legend_y += 14
    cv2.putText(img, "CYAN: A* path", (5, legend_y), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 200, 0), 1)
    legend_y += 14
    cv2.putText(img, "WHITE: walked (ok)", (5, legend_y), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)
    legend_y += 14
    cv2.putText(img, "RED dot: walked on WALL", (5, legend_y), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 0, 255), 1)

    # ── Save ──
    out_path = ROOT / "output" / "mask_vs_walked.png"
    cv2.imwrite(str(out_path), img)
    print(f"\nSaved: {out_path}")
    print(f"Image size: {img_w}x{img_h} px  ({w_tiles}x{h_tiles} tiles)")


if __name__ == "__main__":
    main()
