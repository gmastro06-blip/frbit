"""MouseMovementEngine — trayectorias Bézier con micro-movimientos y overshoot."""

from __future__ import annotations

import math
import random
from typing import List, Tuple

from ..config.models import MouseConfig


class MouseMovementEngine:
    """Genera trayectorias de mouse naturales con curvas Bézier cúbicas."""

    def __init__(self, config: MouseConfig) -> None:
        self._cfg = config

    # ------------------------------------------------------------------
    # Bézier path generation  (Req 4)
    # ------------------------------------------------------------------

    def generate_bezier_path(
        self,
        start: Tuple[int, int],
        end: Tuple[int, int],
        num_points: int | None = None,
    ) -> List[Tuple[int, int]]:
        """Genera trayectoria Bézier cúbica entre *start* y *end*.

        Pasos:
        1. Calcular distancia euclidiana.
        2. Puntos de control con offset perpendicular aleatorio.
        3. Evaluar curva en *num_points* puntos.
        4. Aplicar micro-movimientos.
        """
        if num_points is None:
            num_points = self._cfg.points_per_movement

        x0, y0 = float(start[0]), float(start[1])
        x3, y3 = float(end[0]), float(end[1])
        dx = x3 - x0
        dy = y3 - y0
        dist = math.hypot(dx, dy)

        if dist < 1.0:
            return [start, end]

        # Perpendicular unitario
        perp_x = -dy / dist
        perp_y = dx / dist

        offset_ratio = random.uniform(
            self._cfg.bezier_control_offset_min,
            self._cfg.bezier_control_offset_max,
        )
        offset1 = dist * offset_ratio * random.choice((-1.0, 1.0))
        offset2 = dist * offset_ratio * random.choice((-1.0, 1.0))

        # P1 al 33%, P2 al 66%
        p1x = x0 + dx * 0.33 + perp_x * offset1
        p1y = y0 + dy * 0.33 + perp_y * offset1
        p2x = x0 + dx * 0.66 + perp_x * offset2
        p2y = y0 + dy * 0.66 + perp_y * offset2

        points: List[Tuple[int, int]] = []
        for i in range(num_points):
            t = i / max(num_points - 1, 1)
            u = 1.0 - t
            bx = (u ** 3) * x0 + 3 * (u ** 2) * t * p1x + 3 * u * (t ** 2) * p2x + (t ** 3) * x3
            by = (u ** 3) * y0 + 3 * (u ** 2) * t * p1y + 3 * u * (t ** 2) * p2y + (t ** 3) * y3
            points.append((int(round(bx)), int(round(by))))

        return self.apply_micro_movements(points)

    # ------------------------------------------------------------------
    # Micro-movements  (Req 4)
    # ------------------------------------------------------------------

    def apply_micro_movements(
        self, points: List[Tuple[int, int]]
    ) -> List[Tuple[int, int]]:
        """Aplica perturbaciones N(0, 1.5) a puntos intermedios."""
        if len(points) <= 2:
            return points

        result: List[Tuple[int, int]] = [points[0]]
        for px, py in points[1:-1]:
            ox = max(-3.0, min(3.0, random.gauss(0.0, 1.5)))
            oy = max(-3.0, min(3.0, random.gauss(0.0, 1.5)))
            result.append((int(round(px + ox)), int(round(py + oy))))
        result.append(points[-1])
        return result

    # ------------------------------------------------------------------
    # Velocity profile  (Req 4)
    # ------------------------------------------------------------------

    def calculate_velocity_profile(self, num_points: int) -> List[float]:
        """Perfil de velocidad sigmoide: aceleración → crucero → desaceleración.

        Retorna factores 0.0-1.0 para cada punto.
        """
        if num_points <= 1:
            return [1.0]

        profile: List[float] = []
        for i in range(num_points):
            t = i / (num_points - 1)
            if t < 0.3:
                v = 0.5 + 0.5 * math.tanh(4.0 * (t - 0.15))
            elif t <= 0.7:
                v = 1.0
            else:
                v = 0.5 + 0.5 * math.tanh(4.0 * (0.85 - t))
            profile.append(max(0.0, min(1.0, v)))
        return profile

    # ------------------------------------------------------------------
    # Overshoot  (Req 4)
    # ------------------------------------------------------------------

    def should_overshoot(self) -> bool:
        """True con probabilidad ``overshoot_probability`` (default 0.3)."""
        return random.random() < self._cfg.overshoot_probability

    def generate_overshoot_point(
        self,
        target: Tuple[int, int],
        approach_vector: Tuple[float, float],
    ) -> Tuple[int, int]:
        """Punto de overshoot: target + approach * U(5, 15) px."""
        dist = random.uniform(
            self._cfg.overshoot_distance_min,
            self._cfg.overshoot_distance_max,
        )
        ox = int(round(target[0] + approach_vector[0] * dist))
        oy = int(round(target[1] + approach_vector[1] * dist))
        return (ox, oy)

    def calculate_approach_vector(
        self, path: List[Tuple[int, int]]
    ) -> Tuple[float, float]:
        """Vector de aproximación normalizado desde los últimos ≤5 puntos."""
        tail = path[-5:] if len(path) >= 5 else path
        if len(tail) < 2:
            return (1.0, 0.0)

        sum_dx = 0.0
        sum_dy = 0.0
        for i in range(1, len(tail)):
            sum_dx += tail[i][0] - tail[i - 1][0]
            sum_dy += tail[i][1] - tail[i - 1][1]
        mag = math.hypot(sum_dx, sum_dy)
        if mag < 1e-9:
            return (1.0, 0.0)
        return (sum_dx / mag, sum_dy / mag)

    # ------------------------------------------------------------------
    # Full movement with optional overshoot
    # ------------------------------------------------------------------

    def generate_full_movement(
        self,
        start: Tuple[int, int],
        end: Tuple[int, int],
        num_points: int | None = None,
    ) -> List[Tuple[int, int]]:
        """Genera path completo con posible overshoot + corrección."""
        if self.should_overshoot():
            # Primera trayectoria hacia más allá del target
            preliminary = self.generate_bezier_path(start, end, num_points)
            vec = self.calculate_approach_vector(preliminary)
            overshoot_pt = self.generate_overshoot_point(end, vec)
            path_to_overshoot = self.generate_bezier_path(start, overshoot_pt, num_points)
            # Corrección: volver al target real
            correction_pts = max(10, (num_points or self._cfg.points_per_movement) // 3)
            path_correction = self.generate_bezier_path(overshoot_pt, end, correction_pts)
            return path_to_overshoot + path_correction[1:]
        else:
            return self.generate_bezier_path(start, end, num_points)
