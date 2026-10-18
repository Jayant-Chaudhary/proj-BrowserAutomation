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


# The Google Forms loop of 2026-10-18: "Add question" (bo46) refused as covered, again and again.
FORM = (
    "snapshot · tab 3\n[bo23] generic «Question toolbar»\n[bo46] button «Add question»\n"
    "[bo52] button «Add question»\n[bo60] textbox «Untitled question»"
)
COVERED = "error OBSCURED (nothing dispatched)\nbutton «Add question» is covered by generic «Question toolbar» at its click point."
SHOT = "screenshot · tab 3 · 1280×800 px · scale 1.00 · 12 labels"


def form_body(steps):
    # As in a real run: the server asks for one screenshot right after each new outline.
    steps = [x for st in steps for x in ([st, ("screenshot", {}, SHOT)] if st[0] == "snapshot" else [st])]
    tools = TOOLS + [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ("screenshot", "press")]
    body = agent_body(steps, task="make a blank google form with a short answer field called name")
    body["tools"] = tools
    body["messages"].append({"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}]})
    return body


class FakeLink:
    connected = True
    model = "fake-vl"

    def __init__(self, text):
        self.text, self.jobs = text, []

    async def run(self, kind, prompt, image, system=None):
        from vlm_worker.protocol import JobResult

        self.jobs.append((kind, prompt))
        return JobResult(id="1", ok=True, result={"text": self.text} if kind == "chat" else {"ok": True, "reason": "ok"})


def run(body, answers, link):
    import asyncio as aio
    from orchestrator.config import Settings
    from orchestrator.pipeline.step import run_step

    return aio.run(run_step(parse(body), FakeText(answers), link, Settings()))


CLICK_ADD = {"action": "click", "ref": "bo46", "reason": "add a question"}
click_step = ("click", {"target": {"ref": "bo46"}}, COVERED)


def test_ladder_one_failure_screenshot_then_vision_locates_the_visible_copy():
    link = FakeLink('{"found": true, "label": "bo52", "covered": false, "reason": "the visible plus button"}')
    # After one failure, without a fresh screenshot: take one first.
    r = run(form_body([("snapshot", {}, FORM), click_step]), [CLICK_ADD], link)
    assert r.own_call[0] == "screenshot" and "locate" in r.notes[-1]
    # With the fresh labelled screenshot: the VLM names bo52 and the click goes there.
    r = run(form_body([("snapshot", {}, FORM), click_step, ("screenshot", {}, SHOT)]), [CLICK_ADD], link)
    assert r.command.action == "click" and r.command.ref == "bo52"
    assert link.jobs[-1][0] == "chat" and "Add question" in link.jobs[-1][1]


def test_ladder_vision_sees_nothing_better_then_keyboard():
    link = FakeLink('{"found": true, "label": "bo46", "covered": true, "reason": "covered by the toolbar"}')
    r = run(form_body([("snapshot", {}, FORM), click_step, ("screenshot", {}, SHOT)]), [CLICK_ADD], link)
    assert r.command.action == "press" and r.command.ref == "bo46"
    name, args = formatter.to_tool_call(r.command)
    assert name == "press" and args["keys"] == "Enter" and args["target"] == {"ref": "bo46"}


def test_ladder_second_failure_keyboard_third_hands_over():
    link = FakeLink("{}")
    two = [("snapshot", {}, FORM), click_step, ("click", {"target": {"ref": "bo52"}}, COVERED)]
    r = run(form_body(two), [CLICK_ADD], link)
    assert r.command.action == "press"
    three = two + [("press", {"keys": "Enter", "target": {"ref": "bo46"}}, "error NOT_INTERACTABLE (nothing dispatched)")]
    r = run(form_body(three), [CLICK_ADD], link)
    assert r.command.action == "ask_user" and r.command.ask_kind == "takeover"
    assert "Add question" in r.command.message and "3 tries" in r.command.message
    assert formatter.to_tool_call(r.command)[1]["kind"] == "takeover"


def test_ladder_page_budget_and_other_elements_untouched():
    link = FakeLink("{}")
    many = [("snapshot", {}, FORM)] + [("click", {"target": {"ref": "bo23"}}, "error TARGET_CHANGED (nothing dispatched)")] * 6
    r = run(form_body(many), [{"action": "click", "ref": "bo60", "reason": "r"}], link)
    assert r.command.ask_kind == "takeover"
    # No failures on the element: the model's click goes ahead unchanged.
    r = run(form_body([("snapshot", {}, FORM)]), [{"action": "click", "ref": "bo60", "reason": "r"}], link)
    assert r.command.action == "click" and r.command.ref == "bo60" and not r.notes


def test_point_clicks_are_refused():
    turn = parse(form_body([("snapshot", {}, FORM)]))
    assert "not available" in check({"action": "click", "point": {"x": 5, "y": 5}, "reason": "r"}, turn).problems[0]


def test_element_label_prefers_the_line_with_a_name():
    for named in ('[a1] button "Add question"', "[a1] button «Add question»"):
        turn = parse(agent_body([("snapshot", {}, named), ("screenshot", {}, SHOT + "\n[a1] button")]))
        assert turn.element_label("a1") == 'button "Add question"'
    # As the real run printed it, with the label list after the outline.
    turn = parse(agent_body([("snapshot", {}, '[a1] button "Add question"\n[a2] textbox "Name" = "[[NAME_1]]"')]))
    assert check({"action": "type", "ref": "a2", "value": '"[[NAME_1]]"', "reason": "r"}, turn).command.value == "[[NAME_1]]"


def test_done_success_needs_every_task_value_typed():
    # A real run answered done "filled and ready" right after the screenshot, without typing anything.
    turn = parse(agent_body([("snapshot", {}, OUTLINE)]))
    early = check({"action": "done", "outcome": "success", "summary": "filled", "reason": "r"}, turn)
    assert early.command is None and "[[NAME_1]]" in early.problems[0]
    assert check({"action": "done", "outcome": "failed", "summary": "could not", "reason": "r"}, turn).command
