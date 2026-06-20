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
    """Детектирует водяной знак в углах изображения, возвращает маску."""
    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    corner_h = int(h * EDGE_RATIO)
    corner_w = int(w * 0.4)  # 40% ширины — углы могут быть широкими

    mask = np.zeros((h, w), dtype=np.uint8)

    # Только углы — там обычно логотипы
    corners = [
        (0, corner_h, 0, corner_w),                    # верхний левый
        (0, corner_h, w - corner_w, w),                # верхний правый
        (h - corner_h, h, 0, corner_w),                # нижний левый
        (h - corner_h, h, w - corner_w, w),            # нижний правый
    ]

    best_rect = None
    best_score = 0

    for y1, y2, x1, x2 in corners:
        region = gray[y1:y2, x1:x2]

        # Адаптивный порог — работает для любого цвета знака
        thresh = cv2.adaptiveThreshold(
            region, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 11, 2
        )
        # Морфология — соединяем буквы в единый блок
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 5))
        thresh = cv2.dilate(thresh, kernel, iterations=1)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < 200:
                continue
            x, y, cw, ch = cv2.boundingRect(cnt)
            # Соотношение сторон — логотип/текст обычно горизонтальный
            aspect = cw / (ch + 1)
            if aspect < 1.5 or aspect > 20:
                continue
            score = area
            if score > best_score:
                best_score = score
                best_rect = (x + x1, y + y1, cw, ch)

    if best_rect:
        x, y, cw, ch = best_rect
        pad = 15
        x1m = max(0, x - pad)
        y1m = max(0, y - pad)
        x2m = min(w, x + cw + pad)
        y2m = min(h, y + ch + pad)
        mask[y1m:y2m, x1m:x2m] = 255

    return mask



def _zone_to_mask(img: np.ndarray, zone: str) -> np.ndarray:
    """Создаёт маску для выбранной зоны."""
    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    zh = int(h * 0.20)  # 20% высоты
    zw = int(w * 0.40)  # 40% ширины
    hm = int(h * 0.12)  # 12% — тонкая полоса

    zones = {
        'tl':     (0, zh, 0, zw),                      # верхний левый
        'tc':     (0, zh, w//2 - zw//2, w//2 + zw//2), # верхний центр
        'tr':     (0, zh, w - zw, w),                   # верхний правый
        'ml':     (h//2 - zh//2, h//2 + zh//2, 0, zw), # средний левый
        'center': (h//3, 2*h//3, w//4, 3*w//4),         # центр
        'mr':     (h//2 - zh//2, h//2 + zh//2, w-zw, w),# средний правый
        'bl':     (h - zh, h, 0, zw),                   # нижний левый
        'bc':     (h - zh, h, w//2 - zw//2, w//2 + zw//2), # нижний центр
        'br':     (h - zh, h, w - zw, w),               # нижний правый
        'top':    (0, hm, 0, w),                         # верхняя полоса
        'bottom': (h - hm, h, 0, w),                     # нижняя полоса
    }
    y1, y2, x1, x2 = zones.get(zone, zones['tl'])
    mask[y1:y2, x1:x2] = 255
    return mask


async def remove_watermark_zone(image_path: str, zone: str) -> str | None:
    """Убирает водяной знак в указанной зоне."""
    import logging
    log = logging.getLogger(__name__)
    path = Path(image_path)
    if not path.exists():
        log.warning(f"[WATERMARK] Файл не найден: {image_path}")
        return None

    img = cv2.imread(str(path))
    if img is None:
        return None

    mask = _zone_to_mask(img, zone)
    _, img_encoded = cv2.imencode('.jpg', img)
    _, mask_encoded = cv2.imencode('.png', mask)
    img_b64 = base64.b64encode(img_encoded.tobytes()).decode()
    mask_b64 = base64.b64encode(mask_encoded.tobytes()).decode()

    log.info(f"[WATERMARK] Зона {zone}, отправляем в IOPaint...")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(IOPAINT_URL, json={
                "image": img_b64,
                "mask": mask_b64,
            })
            resp.raise_for_status()

        result_bytes = resp.content
        out_path = path.with_stem(path.stem + f"_clean_{zone}")
        out_path.write_bytes(result_bytes)
        log.info(f"[WATERMARK] Готово: {out_path}")
        return str(out_path)

    except Exception as e:
        log.error(f"[WATERMARK] Ошибка IOPaint: {e}")
        return None

async def remove_watermark(image_path: str) -> str | None:
    """
    Удаляет водяной знак с изображения.
    Возвращает путь к обработанному файлу или None при ошибке.
    """
    import logging
    log = logging.getLogger(__name__)
    log.info(f"[WATERMARK] Начало обработки: {image_path}")

    path = Path(image_path)
    if not path.exists():
        log.warning(f"[WATERMARK] Файл не найден: {image_path}")
        return None

    img = cv2.imread(str(path))
    if img is None:
        log.warning(f"[WATERMARK] Не удалось прочитать изображение: {image_path}")
        return None

    mask = _detect_watermark_mask(img)

    if mask.max() == 0:
        log.info(f"[WATERMARK] Водяной знак не обнаружен: {image_path}")
        return image_path

    # Кодируем в base64
    _, img_encoded = cv2.imencode('.jpg', img)
    _, mask_encoded = cv2.imencode('.png', mask)
    img_b64 = base64.b64encode(img_encoded.tobytes()).decode()
    mask_b64 = base64.b64encode(mask_encoded.tobytes()).decode()

    log.info(f"[WATERMARK] Маска найдена, отправляем в IOPaint...")
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(IOPAINT_URL, json={
                "image": img_b64,
                "mask": mask_b64,
            })
            resp.raise_for_status()

        result_bytes = resp.content
        out_path = path.with_stem(path.stem + "_clean")
        out_path.write_bytes(result_bytes)
        log.info(f"[WATERMARK] Готово: {out_path}")
        return str(out_path)

    except Exception as e:
        log.error(f"[WATERMARK] Ошибка IOPaint: {e}")
        return None
