"""The orchestrator on Laptop A: an OpenAI-compatible endpoint that Nanobrowser uses as its model provider.

Each request is sorted (turn.classify) and answered:
  probe      -> report_status {"status": "ok"} at once, so the provider passes Nanobrowser's tool-calling test,
  done_only  -> qwen3:14b writes the summary, returned as a `done` call,
  chat       -> text from qwen3:14b, or from the VLM when the request carries an image (the image probe),
  agent      -> the step pipeline (pipeline/step.py).

Run:  uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from . import formatter
from .command import Command
from .config import settings
from .pipeline.step import run_step
from .request_log import RequestLog
from .text_model import TextModel, TextModelError
from .turn import parse
from .vlm_link import VlmLink

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("orchestrator")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.text = TextModel(settings)
    app.state.link = VlmLink(settings)
    app.state.log = RequestLog(settings.log_dir)
    app.state.link.start()
    yield
    await app.state.link.stop()


app = FastAPI(title="privacy-agent orchestrator", lifespan=lifespan)
# The extension's service worker calls from a chrome-extension:// origin.
app.add_middleware(CORSMiddleware, allow_origin_regex=r"chrome-extension://.*", allow_methods=["*"], allow_headers=["*"])


def reply(stream: bool, name: str | None, args: dict | None, text: str = "", usage: dict | None = None):
    if stream:
        return StreamingResponse(formatter.stream(name, args, text, usage), media_type="text/event-stream")
    return JSONResponse(formatter.completion(name, args, text, usage))


@app.get("/health")
async def health(request: Request):
    link: VlmLink = request.app.state.link
    return {"ok": True, "text_model": settings.text_model, "vlm": link.model if link.connected else None}


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": formatter.MODEL_ID, "object": "model", "owned_by": "local"}]}


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    turn = parse(body)
    text: TextModel = request.app.state.text
    link: VlmLink = request.app.state.link
    entry: dict = {
        "kind": turn.kind,
        "redaction": request.headers.get("x-redaction-scheme"),
        "task": turn.task[:300],
        "steps": len(turn.steps),
        "image": turn.image_b64 is not None,
        "tags_seen": sorted(turn.known_tags),
    }
    if settings.log_images and turn.image_b64:
        entry["image_file"] = request.app.state.log.save_image(turn.image_b64)
    try:
        if turn.kind == "probe":
            entry["reply"] = "report_status"
            return reply(turn.stream, "report_status", {"status": "ok"})

        if turn.kind == "chat":
            if turn.image_b64 and link.connected:
                r = await link.run("chat", turn.chat_prompt, turn.image_b64, system=turn.chat_system)
                answer = (r.result or {}).get("text", "") if r else ""
            else:
                answer, _ = await text.chat(
                    [{"role": "system", "content": turn.chat_system}, {"role": "user", "content": turn.chat_prompt}]
                )
            entry["reply"] = answer[:300]
            return reply(turn.stream, None, None, answer)

        if turn.kind == "done_only":
            summary, stats = await text.chat(
                [
                    {"role": "system", "content": "Write the final answer for the user in two or three sentences."},
                    {"role": "user", "content": f"Task: {turn.task}\nActions taken:\n"
                     + "\n".join(f"- {s.name} {s.args} -> {s.result[:200]}" for s in turn.steps[-8:])},
                ]
            )
            args = {"outcome": "partial", "summary": summary}
            entry["reply"] = {"done": args}
            return reply(turn.stream, "done", args, usage=stats)

        result = await run_step(turn, text, link, settings)
        if result.own_call:
            name, args = result.own_call
        else:
            name, args = formatter.to_tool_call(result.command or Command(action="ask_user", message="What next?",
                                                                          reason="no command"))
        entry.update(
            reply={name: args}, attempts=result.attempts, notes=result.notes, perception=result.perception,
            verified=result.verified, advisory=result.advisory, timings=result.timings,
        )
        usage = next((v for k, v in result.timings.items() if k.startswith("decide_")), None)
        return reply(turn.stream, name, args, usage=usage)
    except TextModelError as e:
        entry["error"] = str(e)
        return JSONResponse({"error": {"message": str(e), "type": "server_error"}}, status_code=502)
    finally:
        request.app.state.log.write(entry)
