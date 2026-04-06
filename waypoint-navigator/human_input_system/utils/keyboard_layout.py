"""Modelo de layout de teclado QWERTY para errores de teclas adyacentes."""

from __future__ import annotations

import random
from typing import List


class KeyboardLayout:
    """Layout QWERTY estándar con mapeo de teclas adyacentes."""

    LAYOUT = {
        "q": ["w", "a", "1", "2"],
        "w": ["q", "e", "s", "a", "2", "3"],
        "e": ["w", "r", "d", "s", "3", "4"],
        "r": ["e", "t", "f", "d", "4", "5"],
        "t": ["r", "y", "g", "f", "5", "6"],
        "y": ["t", "u", "h", "g", "6", "7"],
        "u": ["y", "i", "j", "h", "7", "8"],
        "i": ["u", "o", "k", "j", "8", "9"],
        "o": ["i", "p", "l", "k", "9", "0"],
        "p": ["o", "l", "0", "-"],
        "a": ["q", "w", "s", "z"],
        "s": ["a", "w", "e", "d", "x", "z"],
        "d": ["s", "e", "r", "f", "c", "x"],
        "f": ["d", "r", "t", "g", "v", "c"],
        "g": ["f", "t", "y", "h", "b", "v"],
        "h": ["g", "y", "u", "j", "n", "b"],
        "j": ["h", "u", "i", "k", "m", "n"],
        "k": ["j", "i", "o", "l", "m"],
        "l": ["k", "o", "p"],
        "z": ["a", "s", "x"],
        "x": ["z", "s", "d", "c"],
        "c": ["x", "d", "f", "v"],
        "v": ["c", "f", "g", "b"],
        "b": ["v", "g", "h", "n"],
        "n": ["b", "h", "j", "m"],
        "m": ["n", "j", "k"],
        "1": ["2", "q"],
        "2": ["1", "3", "q", "w"],
        "3": ["2", "4", "w", "e"],
        "4": ["3", "5", "e", "r"],
        "5": ["4", "6", "r", "t"],
        "6": ["5", "7", "t", "y"],
        "7": ["6", "8", "y", "u"],
        "8": ["7", "9", "u", "i"],
        "9": ["8", "0", "i", "o"],
        "0": ["9", "-", "o", "p"],
    }

    # Mapeo de VK codes a letras para el bot de Tibia
    VK_TO_KEY = {
        0x30: "0", 0x31: "1", 0x32: "2", 0x33: "3", 0x34: "4",
        0x35: "5", 0x36: "6", 0x37: "7", 0x38: "8", 0x39: "9",
        0x41: "a", 0x42: "b", 0x43: "c", 0x44: "d", 0x45: "e",
        0x46: "f", 0x47: "g", 0x48: "h", 0x49: "i", 0x4A: "j",
        0x4B: "k", 0x4C: "l", 0x4D: "m", 0x4E: "n", 0x4F: "o",
        0x50: "p", 0x51: "q", 0x52: "r", 0x53: "s", 0x54: "t",
        0x55: "u", 0x56: "v", 0x57: "w", 0x58: "x", 0x59: "y",
        0x5A: "z",
    }

    KEY_TO_VK = {v: k for k, v in VK_TO_KEY.items()}

    @classmethod
    def get_adjacent_keys(cls, key: str) -> List[str]:
        """Retorna teclas adyacentes a *key*."""
        return cls.LAYOUT.get(key.lower(), [])

    @classmethod
    def get_random_adjacent(cls, key: str) -> str:
        """Retorna una tecla adyacente aleatoria, o la misma si no hay."""
        adjacent = cls.get_adjacent_keys(key)
        if not adjacent:
            return key
        return random.choice(adjacent)

    @classmethod
    def get_adjacent_vk(cls, vk: int) -> int:
        """Dado un VK code, retorna el VK de una tecla adyacente."""
        key = cls.VK_TO_KEY.get(vk)
        if key is None:
            return vk
        adj_key = cls.get_random_adjacent(key)
        return cls.KEY_TO_VK.get(adj_key, vk)
