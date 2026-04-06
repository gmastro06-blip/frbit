"""
image_processing.py
-------------------
Preprocesamiento de imágenes para OCR y calibración.
Pipeline OpenCV puro sin modelos ML.
"""

from __future__ import annotations

import cv2
import numpy as np


class ImageProcessor:
    """
    Procesa un fotograma para facilitar la lectura de texto numérico.
    No usa ningún modelo ML propio; solo OpenCV.
    """

    def __init__(self, scale: int = 4) -> None:
        self.scale = scale

    def preprocess(self, img: np.ndarray) -> np.ndarray:
        """
        img : BGR o RGBA numpy array
        returns: imagen binarizada lista para OCR
        """
        # 1. Convertir a grises
        if img.ndim == 3 and img.shape[2] == 4:
            gray = cv2.cvtColor(img, cv2.COLOR_BGRA2GRAY)
        elif img.ndim == 3:
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        else:
            gray = img.copy()

        # 2. Escalar 4× con interpolación cúbica (mejora legibilidad dígitos pequeños)
        h, w = gray.shape
        scaled = cv2.resize(
            gray, (w * self.scale, h * self.scale),
            interpolation=cv2.INTER_CUBIC,
        )

        # 3. Filtro bilateral para suavizar ruido preservando bordes
        filtered = cv2.bilateralFilter(scaled, 9, 75, 75)

        # 4. Umbral adaptativo (mejor que Otsu en fondos variables)
        binary = cv2.adaptiveThreshold(
            filtered, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            blockSize=15, C=8,
        )

        # 5. Dilatación leve para fortalecer trazo de dígitos
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        binary = cv2.dilate(binary, kernel, iterations=1)

        return binary

    def debug_save(self, img: np.ndarray, path: str = "debug_roi.png") -> None:
        """Guarda el ROI procesado para ayudar en la calibración."""
        import sys
        if getattr(sys, 'frozen', False):
            return
        cv2.imwrite(path, img)
