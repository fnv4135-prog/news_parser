"""
utils/watermark_remover.py — автоматическое удаление водяных знаков через IOPaint LaMa.
"""

import base64
import httpx
import numpy as np
import cv2
from pathlib import Path

IOPAINT_URL = "http://127.0.0.1:8081/api/v1/inpaint"
EDGE_RATIO = 0.15  # 15% от края — зона поиска водяного знака


def _detect_watermark_mask(img: np.ndarray) -> np.ndarray:
    """Детектирует водяной знак по краям изображения, возвращает маску."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Ищем только по краям (15% от каждого края)
    edge_h = int(h * EDGE_RATIO)
    edge_w = int(w * EDGE_RATIO)

    mask = np.zeros((h, w), dtype=np.uint8)

    # Края: верх, низ, лево, право
    regions = [
        (0, edge_h, 0, w),           # верх
        (h - edge_h, h, 0, w),       # низ
        (0, h, 0, edge_w),           # лево
        (0, h, w - edge_w, w),       # право
    ]

    best_rect = None
    best_score = 0

    for y1, y2, x1, x2 in regions:
        region = gray[y1:y2, x1:x2]

        # Ищем контрастные области через порогование
        _, thresh = cv2.threshold(region, 200, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < 100:  # слишком маленький
                continue
            x, y, cw, ch = cv2.boundingRect(cnt)
            score = area / (cw * ch + 1)  # плотность контура
            if score > best_score:
                best_score = score
                best_rect = (x + x1, y + y1, cw, ch)

    if best_rect:
        x, y, cw, ch = best_rect
        # Расширяем маску на 10px
        pad = 10
        x1m = max(0, x - pad)
        y1m = max(0, y - pad)
        x2m = min(w, x + cw + pad)
        y2m = min(h, y + ch + pad)
        mask[y1m:y2m, x1m:x2m] = 255

    return mask


async def remove_watermark(image_path: str) -> str | None:
    """
    Удаляет водяной знак с изображения.
    Возвращает путь к обработанному файлу или None при ошибке.
    """
    path = Path(image_path)
    if not path.exists():
        return None

    img = cv2.imread(str(path))
    if img is None:
        return None

    mask = _detect_watermark_mask(img)

    if mask.max() == 0:
        # Водяной знак не найден — возвращаем оригинал
        return image_path

    # Кодируем в base64
    _, img_encoded = cv2.imencode('.jpg', img)
    _, mask_encoded = cv2.imencode('.png', mask)
    img_b64 = base64.b64encode(img_encoded.tobytes()).decode()
    mask_b64 = base64.b64encode(mask_encoded.tobytes()).decode()

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(IOPAINT_URL, json={
                "image": img_b64,
                "mask": mask_b64,
            })
            resp.raise_for_status()

        # Сохраняем результат
        result_bytes = base64.b64decode(resp.content)
        out_path = path.with_stem(path.stem + "_clean")
        out_path.write_bytes(result_bytes)
        return str(out_path)

    except Exception as e:
        import logging
        logging.error(f"[WATERMARK] Ошибка IOPaint: {e}")
        return None
