from __future__ import annotations

import logging
from typing import Any

from .models import Coordinate


def update_session_position(*, session: Any, logger: logging.Logger) -> bool:
    if session._frame_cache is None and session._frame_getter is None:
        return False

    getter = session._frame_cache.get_frame if session._frame_cache else session._frame_getter
    frame = getter()  # type: ignore[misc]
    if frame is None:
        logger.debug("_update_position: frame is None")
        return False

    if session._frame_quality is not None:
        result = session._frame_quality.check(frame)
        if result.name != "OK":
            logger.debug("Frame rejected: %s", result.name)
            return False

    coord = None
    if session._position_from_deadreckon:
        hint = None
        if session._radar is not None:
            session._radar._last_coord = None
            session._radar._hit_count = 0
    else:
        hint = session._get_position() if hasattr(session, "_get_position") else session._position

    if session._pos_resolver is not None:
        coord = session._pos_resolver.resolve(frame, hint=hint)
        if coord is None:
            return False
    elif session._radar is not None:
        coord = session._radar.read(frame, hint=hint)
        if coord is None:
            logger.debug(
                "MinimapRadar.read returned None (frame %dx%d)",
                frame.shape[1],
                frame.shape[0],
            )
            return False
    else:
        return False

    current_position = session._get_position() if hasattr(session, "_get_position") else session._position
    if current_position is not None and coord is not None and not session._position_from_deadreckon:
        if coord.z != current_position.z:
            logger.warning(
                "_update_position: REJECTED floor jump (%d,%d,%d)->(%d,%d,%d)",
                current_position.x,
                current_position.y,
                current_position.z,
                coord.x,
                coord.y,
                coord.z,
            )
            return False
        jdx = abs(coord.x - current_position.x)
        jdy = abs(coord.y - current_position.y)
        if (
            jdx > session._MAX_POS_JUMP
            or jdy > session._MAX_POS_JUMP
            or (jdx + jdy) > session._MAX_POS_MANHATTAN_JUMP
        ):
            logger.warning(
                "_update_position: REJECTED jump (%d,%d)->(%d,%d) d=(%d,%d)",
                current_position.x,
                current_position.y,
                coord.x,
                coord.y,
                jdx,
                jdy,
            )
            return False

    if session._position_from_deadreckon:
        logger.info(
            "_update_position: reacquired position after dead-reckoning: %s",
            coord,
        )
        session._position_from_deadreckon = False

    if hasattr(session, "_set_position"):
        session._set_position(coord)
    else:
        session._position = coord
    return True


def check_frame_extras(*, session: Any, frame: Any) -> None:
    if session._pvp_detector is not None:
        session._pvp_detector.scan(frame)

    if session._inventory_mgr is not None and session._inventory_mgr.should_check():
        session._inventory_mgr.check_inventory(frame)


def get_real_position(*, update_position_fn: Any, get_position_fn: Any) -> Coordinate | None:
    if update_position_fn():
        return get_position_fn()
    return None


def set_position_from_executor(*, session: Any, coord: Coordinate) -> None:
    if hasattr(session, "_set_position"):
        session._set_position(coord)
    else:
        session._position = coord
    session._position_from_deadreckon = True
    if session._radar is not None:
        session._radar._last_coord = coord