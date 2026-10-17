# `server/orchestrator/config.py`

**Responsibility.** All settings, read from environment variables: Ollama URL, text model (qwen3:14b), num_ctx (16384), VLM URL/token/CA file, timeouts, retries, snapshot size, log folder.

**In → out.** —

**Depends on.** nothing

**Used by.** almost every orchestrator file

**Changed for the two-laptop setup.** VLM_TOKEN_FILE: the token can be read from the file copied from Laptop B.
