# `server/orchestrator/pipeline/step.py`

**Responsibility.** The server-side feedback loop for one step: a refused tag → ask_user; no fresh outline → snapshot; perceive over wss; then decide → check → verify, sending each problem back as a note, at most MAX_RETRIES retries, then ask_user.

**In → out.** Turn → StepResult (command or own snapshot call, attempts, notes, perception, timings).

**Depends on.** check.py, verify.py, prompt.py, text_model.py, vlm_link.py, config.py

**Used by.** main.py

**Changed after the E2E runs.** Requests a labelled `screenshot` after each new outline and before a risky click; perceives only a fresh screenshot; the VLM's no blocks only point clicks (ref clicks record it as `advisory`).

**Changed after a slow real run.** `wants_perception`: the VLM's screen description runs only on thin outlines (default under 8 refs); the click check before a risky click is unchanged.
