# `server/orchestrator/turn.py`

**Responsibility.** Reads one Nanobrowser request into a Turn (task, browser state, user notes, tool steps with results, latest snapshot, known refs and tags, last image and its scale) and sorts it into probe / done_only / chat / agent. Decides when a fresh snapshot is needed and spots a refused placeholder.

**In → out.** OpenAI chat request JSON → Turn.

**Depends on.** nothing

**Used by.** main.py, prompt.py, pipeline/*

**Changed after the E2E runs.** Tracks the latest screenshot (`screenshot_at`), asks for a snapshot after `ask_user`, and lists fields filled since the outline (`filled_since_snapshot`).
