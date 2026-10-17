# `server/orchestrator/main.py`

**Responsibility.** The FastAPI app on Laptop A: `/v1/chat/completions` (answers probe, done_only, chat and agent requests), `/v1/models`, `/health`. CORS allows chrome-extension origins. It starts and stops the VLM link.

**In → out.** HTTP from the extension → tool-call replies.

**Depends on.** turn.py, pipeline/step.py, text_model.py, formatter.py, vlm_link.py, request_log.py, config.py

**Used by.** uvicorn

**Changed after the first use from Chrome.** `/v1/models` declares `input_modalities: [text, image]` and `context_length`, so Nanobrowser offers the screenshot tool without a separate Test; the log records the tools offered and whether `screenshot` is one.
