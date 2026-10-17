# How the privacy agent works, in plain English

_Keep this file up to date whenever the behaviour changes. It is written for someone who has never seen the code._

## The idea in one paragraph

Nanobrowser is a Chrome extension that lets an AI model use your browser. Normally, everything on the page
(your name, your email, the screenshot) is sent to that model. We change two things. First, **the extension hides
personal data before anything leaves the browser**: real values become placeholders such as `[[EMAIL_1]]`, and
screenshots get the same values painted over. Second, **the model is our own server**, running on our own laptops: a
text model (qwen3:14b) that decides what to do, helped by a vision model (Gemma 4) on a second, Windows laptop that looks
at the masked screenshot. The real values come back only at the last moment, when the extension types them into the
page.

## The machines

| Where | What runs there | Talks to |
|---|---|---|
| Laptop A | Chrome with our Nanobrowser fork | the orchestrator, over `http://…:8000/v1` |
| Laptop A | **orchestrator** (FastAPI) + **qwen3:14b** in Ollama | the VLM worker over **wss** |
| Laptop B (Windows) | **VLM worker** + **Gemma 4** (vision) in Ollama | nothing else |

Nanobrowser is set up with an "OpenAI-compatible" provider whose address is the orchestrator. Nanobrowser doesn't
know that the "model" is a pipeline.

## One step, from the page to the click

1. **Nanobrowser looks at the page.** It asks the browser layer for a `snapshot`, a text outline of the page such as
   `[e3] textbox «Email» = «jane@x.org»`. With vision on, it can also take a `screenshot`.
2. **The screenshot is masked when it is taken.** The layer already paints over passwords and card numbers. We added a
   hook so it also paints over every element whose text contains a personal value: anything the vault knows, or
   anything the detectors find. Only after that is the image recorded as "allowed to leave".
3. **The request is cleaned on its way out.** Every request to the model goes through our `fetch` wrapper:
   - Values already in the vault are replaced by their placeholder first, including their URL-encoded and
     form-encoded forms (`jane.doe%40example.com`, `Jane+Doe`), because pages often echo submitted data like that. This catches a value we typed earlier
     when it shows up again on the page.
   - Then the detectors run (also on a decoded copy of the text): a name you state ("my name is …"), email, phone, card, ID numbers, the value of any field whose label says "name",
     "address", "date of birth" and so on, and your own "hide this" rules. Each new value gets a new placeholder.
   - Any image that was not masked by the layer is replaced by the text "screenshot withheld".
   - Redaction repeats until no new placeholder appears, so a value found late is also replaced earlier in the request.
   - A final **leak check** searches the cleaned request again. If any real value or detector hit remains, the
     request is **not sent**, and the run stops with a notice.
4. **The orchestrator receives only placeholders and masked images**, and works out what kind of request it is:
   - the connection test: answered at once;
   - Nanobrowser's final "wrap up" call: qwen3:14b writes the summary;
   - a request with no tools: plain text, or the vision model for the image test;
   - a normal step of the task: the step pipeline below.
5. **The step pipeline (the server-side feedback loop):**
   - If the last action was refused because of an unknown placeholder, ask the user. No model call.
   - If there is no fresh page outline (or the user was just asked something), ask Nanobrowser for a `snapshot`. No
     model call.
   - With vision on, ask for one `screenshot` per new outline, with the ref labels (`a1`, `a5`, …) drawn on top of
     the masked image.
   - If there is a screenshot, send it over **wss** to Laptop B. The vision model describes the screen: the page
     type, any popup, visible errors, the fields it can see. If Laptop B doesn't answer within 10 seconds, continue
     without the description.
   - The vision model only runs on a screenshot taken just now. Each call costs about 10 seconds.
   - Build a **short** prompt for qwen3:14b, including a list of the fields it has already filled: the task, the screen description, the outline, the last few results,
     and any reasons earlier answers were rejected. Nanobrowser's 19 tool definitions (about 9k tokens) are not
     included.
   - qwen3:14b answers with one small JSON command, for example `{"action":"type","ref":"e3","value":"[[EMAIL_1]]"}`.
     Ollama forces the answer into that shape, so we don't rely on the model's own tool-calling.
   - **Check** the command: the ref must exist on the page, and every placeholder must be one the browser actually
     sent. A changed placeholder such as `[EMAIL_1]` is rejected.
   - A risky click (Submit, Pay, Delete, Send…) is refused while any placeholder from the task has not been typed.
   - Before a risky click, take a fresh screenshot and ask the vision model to **verify** the click. For a click by
     position, its "no" blocks the click. For a click on a ref, its "no" is only recorded, because the 7B vision model
     wrongly said "not visible" in every test. The ref check and Nanobrowser's own confirmation still apply.
   - Any problem goes back to qwen3:14b as a note, and it tries again, at most twice. After that, ask the user.
   - The command is then converted into the tool call Nanobrowser expects (for example `set`, with the target ref
     and the value).
6. **Back in the browser, the real value goes in.** Just before the layer runs the tool, the extension swaps each
   placeholder for its real value. If a placeholder is unknown or slightly changed, the call is refused and nothing
   is typed. The conversation history keeps the placeholders, so the real values never go back to the model.
7. **Nanobrowser's own safety rules still apply.** Submit, send and delete ask you first (in Ask mode), and payments
   and passwords are always handed to you.
8. **The result goes into the next request.** The receipt (what changed on the page) is part of the next request,
   so a failed action shows up in the next step's prompt.

## The user's side of the loop

- You confirm risky actions (Nanobrowser's own approval cards).
- When the agent asks you something (`ask_user`), your typed answer is put into the vault straight away. Next time it
  is sent as a placeholder. Short replies such as "yes" stay as they are.
- "Hide this" rules are kept in `chrome.storage.local` under `privacyRules`. They can be a literal value, a pattern, or
  a field label. The rules editor in the side panel is still to be built.

## What we promise, and what we don't

- **Only cleaned data leaves the browser.** Text is cleaned and leak-checked, and images must be ones the layer
  masked or files you attached yourself.
- **A name the detectors can't recognise is not hidden** until it is in the vault: you typed it into a field, it is in
  a field labelled "name", it is one of your rules, or it was an `ask_user` answer. Add your own name as a rule.
- **Text drawn inside pictures, canvas or video is not detected**, so it is not masked.
- The vault lives in the service worker's memory. After Chrome restarts the worker, earlier placeholders are unknown.
  The extension then refuses them and the agent asks you again.
- The vault is shared by all conversations in the browser. A placeholder from one chat would resolve in another.

## How to run it

See [`../server/README.md`](../server/README.md) for the servers. For the extension, open `client/` and run
`pnpm install` and `pnpm build`, load `client/dist` unpacked in Chrome, and add a provider of kind
"OpenAI-compatible" with the base URL `http://<laptop A>:8000/v1` and model `privacy-agent`.
