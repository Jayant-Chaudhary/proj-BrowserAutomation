# `server/orchestrator/imaging.py`

**Responsibility.** Draws the verify marker (a ring and a cross) on the masked screenshot at a point given in CSS pixels times the screenshot's scale.

**In → out.** base64 PNG + point + scale → base64 PNG.

**Depends on.** pillow

**Used by.** pipeline/verify.py
