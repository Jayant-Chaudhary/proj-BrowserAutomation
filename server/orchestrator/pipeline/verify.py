"""The VLM's second opinion on a risky or point-based click, over the wss link. For a point, the masked screenshot gets a
marker at the spot; for a risky ref, the VLM is asked whether the page is ready for it (no visible errors, nothing left
empty). No screenshot or no link means no verify: the extension still asks the user before any submit, pay or delete.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..command import Command
from ..imaging import draw_marker
from ..turn import Turn
from ..vlm_link import VlmLink


@dataclass
class VerifyOutcome:
    ran: bool
    ok: bool = True
    reason: str = ""


def verify_prompt(cmd: Command, element: str, task: str) -> str:
    target = (
        "the spot marked with the pink ring and cross"
        if cmd.point
        else f"the element with the red label {cmd.ref} ({element or 'no description'})"
    )
    return (
        f"The user's task: {task}\n"
        f"The agent wants to click {target}. Its reason: {cmd.reason}\n"
        "A field filled with a solid grey box hides personal data on purpose: it is filled. A white field with only a "
        "thin border is empty.\n"
        "Is this click correct now? Say no if the target is wrong, if a dialog covers it, or if the form shows errors "
        'or required fields that are still empty. Answer as JSON: {"ok": true|false, "reason": "..."}'
    )


async def verify(cmd: Command, element: str, turn: Turn, link: VlmLink) -> VerifyOutcome:
    if not turn.image_b64 or not link.connected:
        return VerifyOutcome(ran=False)
    image = turn.image_b64
    if cmd.point:
        image = draw_marker(image, cmd.point.x, cmd.point.y, turn.image_scale)
    reply = await link.run("verify", verify_prompt(cmd, element, turn.task), image)
    if reply is None or not reply.result:
        return VerifyOutcome(ran=False)
    return VerifyOutcome(ran=True, ok=bool(reply.result.get("ok")), reason=str(reply.result.get("reason", "")))
