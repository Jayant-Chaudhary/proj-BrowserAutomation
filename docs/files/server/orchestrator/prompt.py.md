# `server/orchestrator/prompt.py`

**Responsibility.** Builds the compact decide prompt: the task, user notes, browser state, the VLM's screen description, the snapshot cut to SNAPSHOT_CHARS, the last 4 results, and rejection notes from earlier attempts.

**In → out.** Turn + perception + notes → chat messages.

**Depends on.** turn.py, prompts/decide.txt

**Used by.** pipeline/step.py

**Changed after the E2E runs.** Adds "FIELDS YOU ALREADY FILLED AFTER THE OUTLINE".
