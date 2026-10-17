"""Reads one Nanobrowser request (OpenAI chat-completions shape, already redacted by the extension) into a Turn: what
the pipeline needs, and nothing of Nanobrowser's own 19 tool schemas or long system prompt.

It also sorts requests into the four kinds the server answers differently:
  probe      - the connection test (only the report_status tool),
  done_only  - Nanobrowser's last call, offering only `done`,
  chat       - no tools: a compaction summary, or the image probe,
  agent      - everything else: one step of the task.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

Kind = Literal["probe", "done_only", "chat", "agent"]

REF = re.compile(r"\[([a-z]\d+)\]")
TAG = re.compile(r"\[\[[A-Z]+_\d+\]\]")
BROWSER_STATE = re.compile(r"<browser-state>.*?</browser-state>", re.S)
SCREENSHOT_LINE = re.compile(r"screenshot · tab \d+ · (\d+)×(\d+) px · scale ([\d.]+)")
# Results after which the page must be observed again before acting.
STALE = re.compile(r"NODE_DETACHED|STALE_REF|UNKNOWN_REF|NOT_SHOWN|observe before|no longer valid|navigated", re.I)
REFUSED_TAG = re.compile(r"error (UNKNOWN_TAG|MALFORMED_TAG)")


@dataclass
class Step:
    """One tool call of the conversation and what it returned."""

    name: str
    args: str
    result: str = ""


@dataclass
class Turn:
    kind: Kind
    task: str = ""
    browser_state: str = ""
    user_notes: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    snapshot: str = ""
    # Index in `steps` of the latest snapshot; -1 when there is none.
    snapshot_at: int = -1
    # Index in `steps` of the latest screenshot; -1 when there is none.
    screenshot_at: int = -1
    known_refs: set[str] = field(default_factory=set)
    known_tags: set[str] = field(default_factory=set)
    image_b64: str | None = None
    image_scale: float = 1.0
    tools: list[str] = field(default_factory=list)
    stream: bool = False
    # For chat requests: the messages as plain text.
    chat_system: str = ""
    chat_prompt: str = ""

    @property
    def last(self) -> Step | None:
        return self.steps[-1] if self.steps else None

    def needs_snapshot(self) -> bool:
        """No observation yet, or the last result says the page moved on from the refs we hold."""
        if self.snapshot_at < 0:
            return True
        last = self.last
        if last is None:
            return False
        if last.name in ("navigate", "ask_user") and self.snapshot_at < len(self.steps) - 1:
            # A new page, or the user may have changed the page while the agent waited.
            return True
        return bool(STALE.search(last.result)) and last.name != "snapshot"

    @property
    def can_screenshot(self) -> bool:
        """Nanobrowser offers `screenshot` only when it treats the model as a vision model."""
        return "screenshot" in self.tools

    def needs_screenshot(self) -> bool:
        """A new page outline has no screenshot after it yet."""
        return self.can_screenshot and self.screenshot_at < self.snapshot_at

    def screenshot_is_stale(self) -> bool:
        """Something was done on the page after the latest screenshot (a verify would look at an old picture)."""
        return self.screenshot_at < len(self.steps) - 1 or self.image_b64 is None

    def filled_since_snapshot(self) -> dict[str, str]:
        """Fields the agent set after the latest outline, with the value it gave, from calls that did not fail."""
        filled: dict[str, str] = {}
        for step in self.steps[self.snapshot_at + 1 :]:
            if step.name not in ("set", "type") or step.result.startswith("error"):
                continue
            try:
                args = json.loads(step.args or "{}")
            except json.JSONDecodeError:
                continue
            ref = (args.get("target") or {}).get("ref") if isinstance(args.get("target"), dict) else None
            value = args.get("value", args.get("text"))
            if ref and isinstance(value, str):
                filled[ref] = value
        return filled

    def refused_tag(self) -> str | None:
        """The placeholder the extension refused in the last call, if it refused one."""
        last = self.last
        if last and REFUSED_TAG.search(last.result):
            m = TAG.search(last.result) or re.search(r'"([^"]+)"', last.result)
            return m.group(0) if m else "a placeholder"
        return None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def _images(content: Any) -> list[str]:
    out = []
    if isinstance(content, list):
        for p in content:
            if isinstance(p, dict) and p.get("type") == "image_url":
                url = (p.get("image_url") or {}).get("url", "")
                if url.startswith("data:image/"):
                    out.append(url.split(",", 1)[1])
    return out


def tool_names(body: dict) -> list[str]:
    return [t.get("function", {}).get("name", "") for t in body.get("tools") or []]


def classify(body: dict) -> Kind:
    names = tool_names(body)
    if names == ["report_status"]:
        return "probe"
    if names == ["done"]:
        return "done_only"
    if not names:
        return "chat"
    return "agent"


def parse(body: dict) -> Turn:
    kind = classify(body)
    messages = body.get("messages") or []
    turn = Turn(kind=kind, tools=tool_names(body), stream=bool(body.get("stream")))
    calls: dict[str, Step] = {}
    first_user = True
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        text = _text(content)
        for state in BROWSER_STATE.findall(text):
            turn.browser_state = state
        plain = BROWSER_STATE.sub("", text).strip()
        if role == "system":
            turn.chat_system = text
        elif role == "user":
            images = _images(content)
            if images:
                turn.image_b64 = images[-1]
            if first_user and plain:
                turn.task = plain
                first_user = False
            elif plain and not plain.startswith("The following image"):
                turn.user_notes.append(plain)
        elif role == "assistant":
            for call in m.get("tool_calls") or []:
                fn = call.get("function") or {}
                step = Step(name=fn.get("name", ""), args=fn.get("arguments", ""))
                calls[call.get("id", "")] = step
                turn.steps.append(step)
        elif role == "tool":
            step = calls.get(m.get("tool_call_id", ""))
            if step is not None:
                step.result = text
    for i, step in enumerate(turn.steps):
        if step.name == "snapshot" and step.result and not step.result.startswith("error"):
            turn.snapshot = step.result
            turn.snapshot_at = i
        shot = SCREENSHOT_LINE.search(step.result)
        if shot:
            turn.image_scale = float(shot.group(3))
        if step.name == "screenshot" and step.result and not step.result.startswith("error"):
            turn.screenshot_at = i
    # Refs the agent may use: those of the latest snapshot and of every result after it (diffs add refs).
    for step in turn.steps[max(turn.snapshot_at, 0) :]:
        turn.known_refs.update(REF.findall(step.result))
    for m in messages:
        turn.known_tags.update(TAG.findall(_text(m.get("content"))))
        for call in m.get("tool_calls") or []:
            turn.known_tags.update(TAG.findall((call.get("function") or {}).get("arguments", "")))
    if kind == "chat":
        turn.chat_prompt = "\n\n".join(_text(m.get("content")) for m in messages if m.get("role") != "system")
    return turn
