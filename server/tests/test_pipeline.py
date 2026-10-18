"""Request parsing, the command check, the formatter and the whole endpoint with a fake qwen3:14b."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestrator import formatter
from orchestrator.command import Command
from orchestrator.pipeline.check import check
from orchestrator.turn import classify, parse

SPIKE = Path(__file__).resolve().parents[1] / "logging_proxy/logs/session-20261017-233833.jsonl"

TOOLS = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ["snapshot", "click", "set", "done"]]
OUTLINE = (
    "snapshot · tab 7\n[e1] heading «Contact form»\n[e2] textbox «Your name» = «[[NAME_1]]»\n"
    "[e3] textbox «Email»\n[e4] button «Submit»"
)


def agent_body(steps=(), task="Fill the form with my name [[NAME_1]] and email [[EMAIL_1]]", stream=False):
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": [{"type": "text", "text": task}]}]
    for i, (name, args, result) in enumerate(steps):
        cid = f"c{i}"
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]})
        messages.append({"role": "tool", "tool_call_id": cid, "content": result})
    return {"model": "privacy-agent", "messages": messages, "tools": TOOLS, "stream": stream}


@pytest.mark.skipif(not SPIKE.exists(), reason="spike log not present")
def test_classifies_real_nanobrowser_requests():
    rows = [json.loads(l) for l in SPIKE.open()]
    kinds = [classify(r["client_request"]) for r in rows]
    assert kinds[0] == "probe"
    assert kinds[4] == "agent"
    assert kinds[-1] == "done_only"
    turn = parse(rows[4]["client_request"])
    assert turn.task == "HLO" and "<browser-state>" in turn.browser_state and len(turn.tools) == 19


def test_turn_needs_snapshot_then_knows_refs_and_tags():
    assert parse(agent_body()).needs_snapshot()
    turn = parse(agent_body([("snapshot", {}, OUTLINE)]))
    assert not turn.needs_snapshot()
    assert turn.known_refs == {"e1", "e2", "e3", "e4"}
    assert turn.known_tags == {"[[NAME_1]]", "[[EMAIL_1]]"}


def test_check_rejects_unknown_refs_and_bent_tags_and_rates_submit_high():
    turn = parse(agent_body([("snapshot", {}, OUTLINE)]))
    assert check({"action": "type", "ref": "e9", "value": "x", "reason": "r"}, turn).problems
    assert check({"action": "type", "ref": "e3", "value": "[[EMAIL_7]]", "reason": "r"}, turn).problems
    assert check({"action": "type", "ref": "e3", "value": "[EMAIL_1]", "reason": "r"}, turn).problems
    ok = check({"action": "type", "ref": "e3", "value": "[[EMAIL_1]]", "reason": "r"}, turn)
    assert ok.command and not ok.problems and ok.risk == "low"
    # Submit is refused while a task value is untyped (the E2E run on 2026-10-18 skipped the phone).
    early = check({"action": "click", "ref": "e4", "reason": "r"}, turn)
    assert early.risk == "high" and "[[EMAIL_1]]" in early.problems[0]
    filled = parse(agent_body([("snapshot", {}, OUTLINE), ("set", {"value": "[[EMAIL_1]]"}, "ok"),
                               ("set", {"value": "[[NAME_1]]"}, "ok")]))
    filled.known_refs.add("e4")
    assert check({"action": "click", "ref": "e4", "reason": "r"}, filled).command is not None
    # qwen3:14b copied the outline's quote marks in the Phase 0 run; they must not reach the field.
    quoted = check({"action": "type", "ref": "e3", "value": "«[[EMAIL_1]]»", "reason": "r"}, turn)
    assert quoted.command.value == "[[EMAIL_1]]"


def test_formatter_maps_commands_to_nanobrowser_tools():
    assert formatter.to_tool_call(Command(action="type", ref="e3", value="[[EMAIL_1]]", reason="r"))[0] == "set"
    name, args = formatter.to_tool_call(Command(action="scroll", direction="up", reason="r"))
    assert (name, args["by"]) == ("scroll", {"pages": -1})
    chunks = list(formatter.stream("click", {"target": {"ref": "e4"}}))
    first = json.loads(chunks[0][6:])
    assert first["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "click"
    assert chunks[-1] == "data: [DONE]\n\n"


class FakeText:
    """qwen3:14b stand-in: answers from a list, one per decide call."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    async def decide(self, messages):
        self.calls.append(messages)
        return self.answers.pop(0), {"ms": 1, "prompt_tokens": 100, "completion_tokens": 10}

    async def chat(self, messages):
        return "summary", {"prompt_tokens": 1, "completion_tokens": 1}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    from orchestrator import main

    with TestClient(main.app) as c:
        main.app.state.log.directory = tmp_path
        yield c, main.app


def post(c, body):
    r = c.post("/v1/chat/completions", json=body, headers={"X-Redaction-Scheme": "typed-tags-v1"})
    assert r.status_code == 200, r.text
    return r.json()["choices"][0]["message"]


def test_endpoint_probe_snapshot_and_retry_loop(client):
    c, app = client
    probe = {"messages": [{"role": "user", "content": "Call report_status now."}],
             "tools": [{"type": "function", "function": {"name": "report_status"}}]}
    assert post(c, probe)["tool_calls"][0]["function"]["name"] == "report_status"

    assert post(c, agent_body())["tool_calls"][0]["function"]["name"] == "snapshot"

    app.state.text = FakeText([
        {"action": "type", "ref": "e99", "value": "[[EMAIL_1]]", "reason": "wrong ref"},
        {"action": "type", "ref": "e3", "value": "[[EMAIL_1]]", "reason": "email field"},
    ])
    call = post(c, agent_body([("snapshot", {}, OUTLINE)]))["tool_calls"][0]["function"]
    assert call["name"] == "set"
    assert json.loads(call["arguments"])["value"] == "[[EMAIL_1]]"
    # The rejected first answer went back to the model as a note.
    assert "e99" in app.state.text.calls[1][1]["content"]


def test_endpoint_gives_up_with_ask_user_and_handles_refused_tag(client):
    c, app = client
    app.state.text = FakeText([{"action": "click", "ref": "e99", "reason": "bad"}] * 3)
    call = post(c, agent_body([("snapshot", {}, OUTLINE)]))["tool_calls"][0]["function"]
    assert call["name"] == "ask_user"

    refused = "error UNKNOWN_TAG (nothing dispatched)\nThe placeholder [[PHONE_1]] is not a known value."
    call = post(c, agent_body([("snapshot", {}, OUTLINE), ("set", {"value": "[[PHONE_1]]"}, refused)]))
    assert call["tool_calls"][0]["function"]["name"] == "ask_user"
    assert "[[PHONE_1]]" in json.loads(call["tool_calls"][0]["function"]["arguments"])["message"]


def test_screenshot_rules():
    tools = TOOLS + [{"type": "function", "function": {"name": "screenshot", "parameters": {}}}]
    shot = "screenshot · tab 7 · 1280×900 px · scale 1.00"
    body = agent_body([("snapshot", {}, OUTLINE)])
    body["tools"] = tools
    assert parse(body).needs_screenshot()
    body = agent_body([("snapshot", {}, OUTLINE), ("screenshot", {}, shot), ("set", {"value": "[[EMAIL_1]]"}, "ok")])
    body["tools"] = tools
    turn = parse(body)
    assert not turn.needs_screenshot() and turn.screenshot_is_stale()


def test_filled_fields_and_snapshot_after_ask_user():
    from orchestrator.prompt import decide_messages

    turn = parse(agent_body([("snapshot", {}, OUTLINE), ("set", {"target": {"ref": "e3"}, "value": "[[EMAIL_1]]"}, "ok"),
                             ("set", {"target": {"ref": "e2"}, "value": "x"}, "error NODE_DETACHED")]))
    assert turn.filled_since_snapshot() == {"e3": "[[EMAIL_1]]"}
    assert "- e3 = [[EMAIL_1]]" in decide_messages(turn, None, [], 5000)[1]["content"]
    assert parse(agent_body([("snapshot", {}, OUTLINE), ("ask_user", {}, "answer: yes")])).needs_snapshot()


def test_navigate_and_no_snapshot_loop():
    blank = parse(agent_body([("snapshot", {}, "error NOT_OBSERVABLE: the tab is not a regular web page")],
                             task="open youtube.com"))
    assert not blank.needs_snapshot()
    c = check({"action": "navigate", "url": "youtube.com", "reason": "r"}, blank)
    assert c.command and c.command.url == "https://youtube.com"
    assert formatter.to_tool_call(c.command) == ("navigate", {"url": "https://youtube.com", "intent": "r"})
    assert check({"action": "navigate", "url": "javascript:alert(1)", "reason": "r"}, blank).problems
    after = parse(agent_body([("snapshot", {}, "error"), ("navigate", {"url": "https://youtube.com"}, "ok")]))
    assert after.needs_snapshot()


def test_several_requests_in_one_chat_and_repeat_guard():
    body = agent_body([("navigate", {"url": "https://docs.google.com/forms/u/0/"}, "ok"),
                       ("snapshot", {}, OUTLINE)], task="open youtube.com")
    body["messages"][1]["content"] += [
        {"type": "text", "text": "open google form website"},
        {"type": "text", "text": "snapshot · tab 1 · rev 1 · 69 refs\n<<<page-content>>>"},
        {"type": "text", "text": "<browser-state>\nactive tab: [1] «Forms» «https://docs.google.com/forms/u/0/»\n</browser-state>"},
    ]
    turn = parse(body)
    assert turn.task == "open google form website" and turn.earlier_requests == ["open youtube.com"]
    assert turn.active_url == "https://docs.google.com/forms/u/0/"
    again = check({"action": "navigate", "url": "https://docs.google.com/forms/u/0/", "reason": "r"}, turn)
    assert again.command is None and "already the active tab" in again.problems[0]
    from orchestrator.prompt import decide_messages
    text = decide_messages(turn, None, [], 5000)[1]["content"]
    assert "CURRENT REQUEST (work only on this):\nopen google form website" in text and "EARLIER REQUESTS" in text


def test_models_endpoint_declares_vision(client):
    c, _ = client
    m = c.get("/v1/models").json()["data"][0]
    assert m["id"] == "privacy-agent" and "image" in m["input_modalities"] and m["context_length"] > 0


def test_stream_sends_heartbeats_while_the_step_works(client, monkeypatch):
    import asyncio as aio
    from orchestrator import main

    c, app = client
    monkeypatch.setattr(main, "HEARTBEAT_S", 0.05)

    class Slow(FakeText):
        async def decide(self, messages):
            await aio.sleep(0.3)
            return await super().decide(messages)

    app.state.text = Slow([{"action": "type", "ref": "e3", "value": "[[EMAIL_1]]", "reason": "r"}])
    with c.stream("POST", "/v1/chat/completions", json=agent_body([("snapshot", {}, OUTLINE)], stream=True)) as r:
        lines = [l for l in r.iter_lines() if l]
    assert any(l.startswith(": working") for l in lines)
    data = [json.loads(l[6:]) for l in lines if l.startswith("data: {")]
    assert data[0]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "set"
    assert lines[-1] == "data: [DONE]"


def test_perceive_only_on_thin_outlines():
    from orchestrator.config import Settings
    from orchestrator.pipeline.step import wants_perception

    rich = parse(agent_body([("snapshot", {}, OUTLINE * 3)]))
    thin = parse(agent_body([("snapshot", {}, "[e1] heading «Canvas app»")]))
    s = Settings()
    assert not wants_perception(rich, s) and wants_perception(thin, s)


def test_refs_with_longer_epoch_prefixes():
    # A real Google Forms run: the model chose "bl90" (the Blank form button) and the old pattern ([a-z]\d+) rejected it.
    outline = "[bl88] heading «Start a new form»\n[bl90] button «Blank form»\n[ABCDEFGHIJKL7] link «Help»"
    turn = parse(agent_body([("snapshot", {}, outline)]))
    assert turn.known_refs == {"bl88", "bl90", "ABCDEFGHIJKL7"}
    c = check({"action": "click", "ref": "bl90", "reason": "r"}, turn)
    assert c.command is not None and "Blank form" in c.element
