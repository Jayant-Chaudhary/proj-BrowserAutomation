"""The server's first check of a command, before anything reaches the browser. Each problem becomes a short note that
goes back to qwen3:14b for the retry. It also rates the risk of the action, which decides whether the VLM verifies it.
The extension checks again on its own side; this check only saves a round trip to the browser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from ..command import Command
from ..turn import TAG, Turn

# A changed placeholder: one bracket, lower case, other bracket styles, or a bare KIND_N.
NEAR_MISS = re.compile(
    r"\[\[?\s*[A-Za-z]+_?\d*\s*\]\]?|⟦[^⟧]*⟧|\{\{[^}]*\}\}|\b(?:NAME|EMAIL|PHONE|ADDRESS|CARD|ID|DATE|PERSONAL)_\d+\b"
)
RISKY = re.compile(
    r"submit|send|pay|purchase|buy|order|checkout|delete|remove|confirm|transfer|sign up|register|book|post|publish",
    re.I,
)


@dataclass
class Checked:
    command: Command | None
    problems: list[str] = field(default_factory=list)
    risk: str = "low"
    element: str = ""


def element_line(turn: Turn, ref: str) -> str:
    """The outline line that shows a ref, from the newest result that has it."""
    pattern = re.compile(rf"\[{re.escape(ref)}\][^\n]*")
    for step in reversed(turn.steps):
        m = pattern.search(step.result)
        if m:
            return m.group(0)
    return ""


def check(raw: dict, turn: Turn) -> Checked:
    try:
        cmd = Command.model_validate(raw)
    except ValidationError as e:
        first = e.errors()[0]
        return Checked(None, [f"The JSON did not match the schema: {first['loc']} {first['msg']}."])
    problems: list[str] = []
    if cmd.action in ("click", "type"):
        if cmd.ref:
            cmd.ref = cmd.ref.strip("[] ")
            if cmd.ref not in turn.known_refs:
                problems.append(f'The ref "{cmd.ref}" is not in the page outline. Use a ref that is shown.')
        elif cmd.point:
            if not turn.image_b64:
                problems.append("A point needs a screenshot, and there is none. Use a ref from the outline.")
        elif cmd.action == "click":
            problems.append("click needs a ref from the outline.")
    if cmd.action == "type" and cmd.value:
        # The outline quotes page text as «…»; a model that copies the quotes would type them into the field.
        cmd.value = cmd.value.replace("«", "").replace("»", "")
    if cmd.action == "type":
        if not cmd.value:
            problems.append("type needs a value.")
        else:
            for tag in TAG.findall(cmd.value):
                if tag not in turn.known_tags:
                    problems.append(f"The placeholder {tag} does not exist. Copy one that is shown, or use ask_user.")
            bent = NEAR_MISS.search(TAG.sub("", cmd.value))
            if bent:
                problems.append(f'"{bent.group(0)}" is a changed placeholder. Copy placeholders exactly, like [[NAME_1]].')
    if cmd.action == "ask_user" and not cmd.message:
        problems.append("ask_user needs a message.")
    if cmd.action == "done" and (not cmd.outcome or not cmd.summary):
        problems.append("done needs an outcome and a summary.")
    if cmd.action == "scroll" and not cmd.direction:
        cmd.direction = "down"

    element = element_line(turn, cmd.ref) if cmd.ref else ""
    risky = cmd.action == "click" and (cmd.point is not None or bool(RISKY.search(element)))
    if risky:
        # The task gave these values; a submit before all of them were typed sends an incomplete form.
        missing = untyped_task_tags(turn)
        if missing:
            problems.append(
                f"The task's {', '.join(missing)} {'has' if len(missing) == 1 else 'have'} not been typed yet. "
                "Fill the matching field before you submit."
            )
    checked = Checked(cmd if not problems else None, problems, "high" if risky else "low", element)
    return checked


def untyped_task_tags(turn: Turn) -> list[str]:
    """Placeholders in the task that no earlier set/type call has used, in the order the task gives them."""
    typed = set()
    for step in turn.steps:
        if step.name in ("set", "type", "fill_form"):
            typed.update(TAG.findall(step.args))
    seen: list[str] = []
    for tag in TAG.findall(turn.task):
        if tag not in typed and tag not in seen:
            seen.append(tag)
    return seen
