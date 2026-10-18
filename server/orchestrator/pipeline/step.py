"""One step of the task, from a parsed request to one command: the server-side feedback loop.

  1. A refused placeholder in the last result         -> ask_user, no model call.
  2. No fresh page outline                            -> snapshot, no model call.
     A new outline without a screenshot (vision on)  -> screenshot, no model call.
  3. A screenshot is present                          -> VLM "perceive" over wss (text-only if it times out).
  4. qwen3:14b decides -> check -> (VLM verify if risky, on a fresh screenshot) -> on a problem, the note goes back
     to qwen3:14b.
  5. Still failing after the retries                  -> ask_user.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..command import Command
from ..config import Settings
from ..prompt import decide_messages
from ..text_model import TextModel, TextModelError
from ..turn import REF, Turn
from ..vlm_link import VlmLink
from .check import check
from .verify import verify

PERCEIVE_PROMPT = (
    "Describe this web page screenshot for a browser agent. Solid black or grey boxes hide personal data on purpose: "
    "a box over a field means the field HAS a value. Never call a field empty because it is covered. "
    "Red labels such as a5 name elements. "
    'Answer as JSON: {"page_kind": "...", "modal": null or "...", "errors": ["..."], '
    '"visible_fields": ["..."], "notes": "..."}'
)


# `labels` draws each ref (a1, a5, ...) on the image after the masking, so the VLM can find the element a ref names.
SCREENSHOT_CALL = ("screenshot", {"labels": True, "intent": "Show the vision model the page (personal data is masked)"})


@dataclass
class StepResult:
    # Either a command for the formatter, or a tool call the server makes itself (snapshot).
    command: Command | None = None
    own_call: tuple[str, dict] | None = None
    attempts: int = 0
    notes: list[str] = field(default_factory=list)
    perception: dict | None = None
    verified: bool | None = None
    # The VLM's objection to a ref click that went ahead anyway (see above).
    advisory: str | None = None
    timings: dict = field(default_factory=dict)


def wants_perception(turn: Turn, settings: Settings) -> bool:
    """Whether the VLM should describe the fresh screenshot (see Settings.perceive)."""
    if settings.perceive == "never":
        return False
    if settings.perceive == "always":
        return True
    return len(REF.findall(turn.snapshot)) < settings.perceive_min_refs


def ask(message: str, reason: str) -> Command:
    return Command(action="ask_user", message=message, reason=reason)


async def run_step(turn: Turn, text: TextModel, link: VlmLink, settings: Settings) -> StepResult:
    out = StepResult()

    refused = turn.refused_tag()
    if refused:
        out.command = ask(
            f"I need a value for a field, but the placeholder {refused} is not one I know. "
            "Please type the value to use.",
            "The extension refused an unknown placeholder.",
        )
        return out

    if turn.needs_snapshot():
        out.own_call = ("snapshot", {"intent": "Look at the page before acting"})
        return out

    # One screenshot per new outline, for the VLM; only when Nanobrowser offers the tool and Laptop B is there.
    if turn.needs_screenshot() and link.connected:
        out.own_call = SCREENSHOT_CALL
        return out

    # Perceive only a screenshot taken just now: an older one was already described, and each VLM call costs seconds.
    if turn.image_b64 and link.connected and turn.screenshot_at == len(turn.steps) - 1 and wants_perception(turn, settings):
        started = time.monotonic()
        reply = await link.run("perceive", PERCEIVE_PROMPT, turn.image_b64)
        out.timings["perceive_ms"] = int((time.monotonic() - started) * 1000)
        out.perception = reply.result if reply else None

    for attempt in range(settings.max_retries + 1):
        out.attempts = attempt + 1
        messages = decide_messages(turn, out.perception, out.notes, settings.snapshot_chars)
        try:
            raw, stats = await text.decide(messages)
        except TextModelError as e:
            out.notes.append(str(e))
            continue
        out.timings[f"decide_{attempt + 1}"] = stats
        checked = check(raw, turn)
        if checked.command is None:
            out.notes.extend(checked.problems)
            continue
        if checked.risk == "high" and turn.can_screenshot and link.connected and turn.screenshot_is_stale():
            # Verify must look at the page as it is now: take a fresh screenshot first; the next request decides again.
            out.own_call = SCREENSHOT_CALL
            out.notes.append("fresh screenshot requested before a risky click")
            return out
        if checked.risk == "high":
            started = time.monotonic()
            v = await verify(checked.command, checked.element, turn, link)
            out.timings[f"verify_{attempt + 1}_ms"] = int((time.monotonic() - started) * 1000)
            if v.ran:
                out.verified = v.ok
                if not v.ok and checked.command.point is not None:
                    # A click by position has only the image as evidence: the VLM's no blocks it.
                    out.notes.append(f"The vision check rejected this click: {v.reason}")
                    continue
                if not v.ok:
                    # A ref click passed the deterministic checks (the ref is on the page, every task value is typed),
                    # and the extension asks the user before a submit. Qwen2.5-VL 7B said "not visible" for a clearly
                    # visible button in every E2E run, so its no is recorded, not obeyed.
                    out.advisory = v.reason
        out.command = checked.command
        return out

    out.command = ask(
        "I could not find a safe next step on this page. What should I do next?",
        f"Gave up after {out.attempts} attempts: {out.notes[-1] if out.notes else 'no answer'}",
    )
    return out
