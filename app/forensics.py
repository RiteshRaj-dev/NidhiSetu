"""Document forensics: Error Level Analysis (ELA) on the first rendered page.

The page is re-encoded as JPEG at quality 90 and compared with the original. Regions that were pasted in or
edited after the last compression tend to carry a different error level than their surroundings. The heatmap
makes that visible; the score is a *screening heuristic* that routes a document to a human, never a verdict."""
import io
import cv2
import numpy as np
from PIL import Image

QUALITY = 90
AMPLIFY = 20          # gain applied to the absolute difference before colouring
BLOCK = 16            # px, analysis block size
SUSPECT_AT = 55       # tamper_score at or above this flags the document for scrutiny


def _diff(img: Image.Image) -> np.ndarray:
    rgb = img.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=QUALITY)
    buf.seek(0)
    again = Image.open(buf).convert("RGB")
    return cv2.absdiff(np.array(rgb), np.array(again))


def ela_heatmap(img: Image.Image) -> np.ndarray:
    """BGR heatmap (COLORMAP_JET) of the amplified ELA difference."""
    amp = cv2.convertScaleAbs(_diff(img), alpha=AMPLIFY)
    gray = cv2.cvtColor(amp, cv2.COLOR_BGR2GRAY)
    return cv2.applyColorMap(gray, cv2.COLORMAP_JET)


def analyse(img: Image.Image) -> dict:
    """Return {"tamper_score": 0-100, "suspect": bool}.

    Score = how far the most anomalous 16 px block (99.9th percentile) sits above the typical content block,
    mapped linearly (ratio 1.8 -> 0, ratio 3.6 -> 100). Demo-grade screening heuristic: it was checked on
    synthetic receipts with a pasted-in amount, not calibrated on real forged documents."""
    d = _diff(img).astype(np.float32).mean(axis=2)
    h, w = d.shape
    h2, w2 = h - h % BLOCK, w - w % BLOCK
    if h2 == 0 or w2 == 0:
        return {"tamper_score": 0.0, "suspect": False}
    blocks = d[:h2, :w2].reshape(h2 // BLOCK, BLOCK, w2 // BLOCK, BLOCK).mean(axis=(1, 3)).ravel()
    content = blocks[blocks > 0.02]           # ignore blank paper
    if content.size < 12:
        return {"tamper_score": 0.0, "suspect": False}
    ratio = float(np.percentile(blocks, 99.9)) / (float(np.median(content)) + 1e-3)
    score = max(0.0, min(100.0, (ratio - 1.8) / (3.6 - 1.8) * 100.0))
    return {"tamper_score": round(score, 1), "suspect": bool(score >= SUSPECT_AT)}
