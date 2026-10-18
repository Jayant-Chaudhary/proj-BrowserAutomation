# `server/orchestrator/config.py`

**Responsibility.** All settings, read from environment variables: Ollama URL, text model (qwen3:14b), num_ctx (16384), VLM URL/token/CA file, timeouts, retries, snapshot size, log folder.

**In → out.** —

**Depends on.** nothing

**Used by.** almost every orchestrator file

**Changed for the two-laptop setup.** VLM_TOKEN_FILE: the token can be read from the file copied from Laptop B.

**Changed after a slow real run.** TEXT_NUM_CTX defaults to 8192 (16k spilled qwen3:14b further off the 8 GB GPU: 8-114 s per step; 8k: ~8 s). PERCEIVE=auto|always|never and PERCEIVE_MIN_REFS: the VLM describes a screenshot only when the outline is thin.
