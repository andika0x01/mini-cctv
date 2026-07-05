from pathlib import Path

import cv2
import numpy as np


class YoloPersonDetector:
    def __init__(self, model_path: str, input_size: int, confidence_threshold: float):
        if not 0 < confidence_threshold <= 1:
            raise ValueError("PERSON_CONFIDENCE_THRESHOLD must be between 0 and 1")
        model = Path(model_path)
        if not model.exists():
            raise RuntimeError(
                f"YOLO model not found: {model_path}. "
                "Place yolov8n.onnx at backend/models/yolov8n.onnx or set YOLO_MODEL_PATH."
            )
        self._input_size = input_size
        self._confidence_threshold = confidence_threshold
        self._net = cv2.dnn.readNetFromONNX(str(model))

    def detect_person_confidence(self, frame: np.ndarray) -> float:
        blob = cv2.dnn.blobFromImage(
            frame,
            scalefactor=1 / 255.0,
            size=(self._input_size, self._input_size),
            swapRB=True,
            crop=False,
        )
        self._net.setInput(blob)
        output = self._net.forward()
        predictions = np.squeeze(output)

        if predictions.ndim == 1:
            return 0.0
        if predictions.ndim == 2 and predictions.shape[0] < predictions.shape[1]:
            predictions = predictions.T
        if predictions.ndim != 2 or predictions.shape[1] < 5:
            return 0.0

        if predictions.shape[1] >= 85:
            person_scores = predictions[:, 4] * predictions[:, 5]
        else:
            person_scores = predictions[:, 4]
        if person_scores.size == 0:
            return 0.0
        return float(np.max(person_scores))

    def has_person(self, frame: np.ndarray) -> tuple[bool, float]:
        confidence = self.detect_person_confidence(frame)
        return confidence >= self._confidence_threshold, confidence

