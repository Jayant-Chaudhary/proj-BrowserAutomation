"""Draws the verify marker on the (already masked) screenshot: a ring and a cross where the agent wants to click, so
the VLM can say whether that spot is the right element. The image stays on this side only long enough to go to the
VLM worker; nothing is saved.
"""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageDraw


def draw_marker(image_b64: str, x_css: float, y_css: float, scale: float) -> str:
    """The screenshot with a marker at a point given in CSS pixels; `scale` is image pixels per CSS pixel."""
    img = Image.open(io.BytesIO(base64.b64decode(image_b64))).convert("RGB")
    x, y = x_css * scale, y_css * scale
    r = max(10, int(14 * scale))
    d = ImageDraw.Draw(img)
    color = (255, 0, 160)
    width = max(2, int(3 * scale))
    d.ellipse([x - r, y - r, x + r, y + r], outline=color, width=width)
    d.line([x - 2 * r, y, x + 2 * r, y], fill=color, width=width)
    d.line([x, y - 2 * r, x, y + 2 * r], fill=color, width=width)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()
