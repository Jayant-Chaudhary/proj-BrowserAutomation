# `server/orchestrator/turn.py`

**Responsibility.** Reads one Nanobrowser request into a Turn (task, browser state, user notes, tool steps with results, latest snapshot, known refs and tags, last image and its scale) and sorts it into probe / done_only / chat / agent. Decides when a fresh snapshot is needed and spots a refused placeholder.

**In → out.** OpenAI chat request JSON → Turn.

**Depends on.** nothing

**Used by.** main.py, prompt.py, pipeline/*

**Changed after the E2E runs.** Tracks the latest screenshot (`screenshot_at`), asks for a snapshot after `ask_user`, and lists fields filled since the outline (`filled_since_snapshot`).

**Changed after the first use from Chrome.** Never asks for two snapshots in a row: a page that cannot be observed (a new tab) goes to the model, which can navigate away.

**Changed after the first use from Chrome.** Splits the chat into the user's requests (`requests`); the last one is the current task, the earlier ones are shown as already handled. Reads the active tab's address (`active_url`).

**Fixed (2026-10-18).** `REF` accepts multi-letter epoch prefixes (`bl90`) and the 12-uppercase fallback. With the old one-letter pattern, every ref on a page looked invalid once the prefix grew past `z` (after extension reloads), so correct clicks were rejected and the VLM description ran on every page.

**Changed (2026-10-18, real runs).** Per-element failure tracking for the ladder (`failures_on`, `failures_on_page`, `steps_on_page`) and `element_label`, which reads the snapshot's real JSON-style quotes (`[a1] button "Add question"`) as well as «…», preferring the line that carries the name.
