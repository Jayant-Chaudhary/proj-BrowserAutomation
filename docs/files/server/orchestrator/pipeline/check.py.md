# `server/orchestrator/pipeline/check.py`

**Responsibility.** The server's check of a command: the schema, required fields per action, the ref present on the page, every placeholder one the browser sent, and no bent placeholders. It also strips the outline's «» quote marks from typed values (qwen3:14b copies them) and rates risk: a click on Submit, Pay, Delete, Send… or any point click is high.

**In → out.** Raw dict + Turn → Checked (command or problems, risk, element line).

**Depends on.** command.py, turn.py

**Used by.** pipeline/step.py

**Changed after the E2E runs.** Refuses a risky click while a placeholder from the task has not been typed (`untyped_task_tags`).

**Changed after the first use from Chrome.** `navigate`: a bare host gets https://, and any scheme other than http/https (javascript:, file:, chrome:) is refused.

**Changed after the first use from Chrome.** Repeat guard (`repeated`): refuses navigating to the open address, repeating the last navigate, or the same value into the same field (a Google Forms run navigated to the same page six times).
