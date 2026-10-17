# `server/orchestrator/pipeline/verify.py`

**Responsibility.** Gets the VLM's second opinion on a high-risk click over wss. It marks the point on the image, or asks whether the page is ready for the risky element. Skipped without a screenshot or a link.

**In → out.** Command + Turn → VerifyOutcome (ran, ok, reason).

**Depends on.** imaging.py, vlm_link.py, command.py, turn.py

**Used by.** pipeline/step.py
