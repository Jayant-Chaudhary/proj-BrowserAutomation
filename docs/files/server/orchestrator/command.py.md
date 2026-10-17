# `server/orchestrator/command.py`

**Responsibility.** The command qwen3:14b answers with (action, ref/point, value, direction, message, outcome, summary, reason), plus the JSON schema sent to Ollama as `format`. Running it as a module prints `contracts/command.schema.json`.

**In → out.** —

**Depends on.** pydantic

**Used by.** text_model.py, pipeline/check.py, formatter.py, main.py
