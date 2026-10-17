# Setup: Laptop A (browser + decider) and Laptop B (vision model)

```
Laptop A                                              Laptop B
Chrome + Nanobrowser fork (client/dist)               Ollama + qwen2.5vl:7b
orchestrator  :8000 (localhost only)  ── wss :8765 ──▶ vlm_worker (TLS + token)
Ollama + qwen3:14b
```

Both laptops must be on the **same local network**. Only masked images and placeholders cross the link. Laptop B
never sees real personal data, and it never talks to anything except Laptop A.

Commands are for Linux (Ubuntu). macOS notes are at the end of each part.

---

## 0. Before you start (both laptops)

1. Find each laptop's LAN address and write them down:
   ```bash
   ip -4 addr show | grep inet        # e.g. Laptop A 192.168.1.10, Laptop B 192.168.1.20
   ```
   These steps use `192.168.1.10` (A) and `192.168.1.20` (B); replace them with yours. A fixed address (a DHCP
   reservation in the router) saves redoing the certificate when it changes.
2. Check that A can reach B: `ping 192.168.1.20`.
3. Install Python 3.12 and Ollama on both:
   ```bash
   sudo apt install -y python3 python3-venv openssl
   curl -fsSL https://ollama.com/install.sh | sh
   ```
4. Get the `server/` folder onto **both** laptops (the same code runs on both; each laptop starts a different part):
   ```bash
   # from Laptop A, which has the project:
   rsync -av --exclude .venv --exclude logs --exclude __pycache__ \
     ~/Code/harness/server/ user@192.168.1.20:~/privacy-agent/server/
   ```
5. Create the Python environment in `server/` on each laptop:
   ```bash
   cd ~/privacy-agent/server        # on Laptop A: ~/Code/harness/server
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```

---

## 1. Laptop B: the vision model

### 1.1 Model

```bash
ollama pull qwen2.5vl:7b
ollama run qwen2.5vl:7b "say ok"          # first load; check it answers
```

With 8 GB of VRAM the 7B fits only with a short context; the worker asks for 8k (`VLM_NUM_CTX`). If it runs slowly,
`nvidia-smi` and `ollama ps` show whether part of it fell back to the CPU. The fallback is `qwen2.5vl:3b`
(set `VLM_MODEL=qwen2.5vl:3b`).

### 1.2 Certificate (TLS for wss)

The certificate must name Laptop B's **IP address**, because Laptop A connects by IP and checks the name.

```bash
mkdir -p ~/privacy-agent/tls && cd ~/privacy-agent/tls
openssl req -x509 -newkey rsa:2048 -nodes -days 90 -subj "/CN=laptop-b" \
  -addext "subjectAltName=IP:192.168.1.20,DNS:laptop-b" \
  -keyout key.pem -out cert.pem
chmod 600 key.pem
```

`key.pem` never leaves Laptop B. Only `cert.pem` is copied to Laptop A (step 2.3).
(`mkcert` works too: `mkcert 192.168.1.20 laptop-b`; then copy the mkcert root CA to A, not the leaf.)

### 1.3 Shared token

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))' > ~/privacy-agent/tls/token
chmod 600 ~/privacy-agent/tls/token
```

Send the token to Laptop A over a private channel (not a group chat).

### 1.4 Firewall: only Laptop A may connect

```bash
sudo ufw allow from 192.168.1.10 to any port 8765 proto tcp
sudo ufw enable
sudo ufw status
```

### 1.5 Start the worker

```bash
cd ~/privacy-agent/server
VLM_TOKEN=$(cat ~/privacy-agent/tls/token) \
  .venv/bin/python -m vlm_worker.worker --host 0.0.0.0 --port 8765 \
  --cert ~/privacy-agent/tls/cert.pem --key ~/privacy-agent/tls/key.pem
```

Expected: `VLM worker on wss://0.0.0.0:8765 with qwen2.5vl:7b`. When A connects, it logs
`orchestrator connected from ('192.168.1.10', …)`.

Optional settings: `VLM_MODEL` (default `qwen2.5vl:7b`), `VLM_NUM_CTX` (8192), and `OLLAMA_URL`
(`http://localhost:11434`).

### 1.6 Keep it running after reboot (optional, systemd)

`/etc/systemd/system/vlm-worker.service`:
```ini
[Unit]
Description=privacy-agent VLM worker
After=network-online.target ollama.service

[Service]
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/privacy-agent/server
ExecStart=/bin/sh -c 'VLM_TOKEN=$(cat /home/YOUR_USER/privacy-agent/tls/token) exec .venv/bin/python -m vlm_worker.worker --host 0.0.0.0 --port 8765 --cert /home/YOUR_USER/privacy-agent/tls/cert.pem --key /home/YOUR_USER/privacy-agent/tls/key.pem'
Restart=always

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload && sudo systemctl enable --now vlm-worker
journalctl -u vlm-worker -f
```

**macOS:** install Ollama from ollama.com. For the firewall, use System Settings → Network → Firewall, which allows
by app. If you want to limit the port to Laptop A's address, use `pf` instead. There's no systemd; run the worker in
a terminal or with `launchd`.

---

## 2. Laptop A: browser, orchestrator and the decider

### 2.1 Model

```bash
ollama pull qwen3:14b
```

On an 8 GB GPU, qwen3:14b at a 16k context is ~12 GB and runs about half on the CPU (measured: 8–13 s per step; see
`phase0-findings.md`). Try `TEXT_NUM_CTX=8192` for a smaller footprint.

### 2.2 Extension (the Nanobrowser fork)

Needs Node 24 and pnpm 10.34.7 (through corepack):
```bash
cd ~/Code/harness/client          # the fork, branch privacy-layer
corepack enable
corepack pnpm install
corepack pnpm build               # output: client/dist
```
In Chrome: open `chrome://extensions`, turn on Developer mode, click **Load unpacked** and choose `client/dist`.
After a rebuild, press **Reload** on the extension's card.

### 2.3 Link to Laptop B

Copy Laptop B's `cert.pem` and the token here:
```bash
mkdir -p ~/privacy-agent/tls
scp user@192.168.1.20:~/privacy-agent/tls/cert.pem ~/privacy-agent/tls/laptop-b.pem
# paste the token B gave you:
nano ~/privacy-agent/tls/token && chmod 600 ~/privacy-agent/tls/token
```

Check the TLS link before starting anything:
```bash
openssl s_client -connect 192.168.1.20:8765 -CAfile ~/privacy-agent/tls/laptop-b.pem </dev/null 2>/dev/null \
  | grep "Verify return code"      # must say: 0 (ok)
```

### 2.4 Start the orchestrator

```bash
cd ~/Code/harness/server
VLM_URL=wss://192.168.1.20:8765 \
VLM_TOKEN=$(cat ~/privacy-agent/tls/token) \
VLM_CA_FILE=~/privacy-agent/tls/laptop-b.pem \
  .venv/bin/uvicorn orchestrator.main:app --host 127.0.0.1 --port 8000
```

Keep `--host 127.0.0.1`: Chrome is on the same laptop, so nothing else needs to reach port 8000.

Expected log: `VLM link up (qwen2.5vl:7b)`. Then check:
```bash
curl localhost:8000/health
# {"ok":true,"text_model":"qwen3:14b","vlm":"qwen2.5vl:7b"}     <- "vlm": null means the link is down
```

Optional settings: `TEXT_MODEL`, `TEXT_NUM_CTX` (16384), `VLM_TIMEOUT_S` (10), `MAX_RETRIES` (2), `SNAPSHOT_CHARS`,
and `LOG_DIR`.

### 2.5 Point Nanobrowser at the orchestrator

In the extension: **Settings → Providers → add provider**
- Kind: **OpenAI-compatible**
- Base URL: `http://localhost:8000/v1`
- API key: anything (for example `local`)
- Model: `privacy-agent`

Press **Test**. The tool-calling test should pass, and the image test is answered by Laptop B. If the image test
fails, the `screenshot` tool is not offered and the agent runs text-only. You can set vision on by hand in the
model's options. Then pick `privacy-agent` as the model in the side panel.

### 2.6 Your "hide this" values (recommended)

Plain names in running text are only hidden once the privacy layer knows them. Until the rules editor is built, add
your own values in the extension's service-worker console (`chrome://extensions` → the extension → "service worker"):
```js
chrome.storage.local.set({ privacyRules: [
  { id: '1', kind: 'NAME', literal: 'Your Full Name' },
  { id: '2', kind: 'ADDRESS', label: 'street' },
]});
```

---

## 3. Check that everything works

1. On A: `curl localhost:8000/health` shows both models.
2. Run a small task in the side panel, for example on a form with fake details.
3. Check that **only placeholders** reached the server:
   ```bash
   grep -c "your-real-email@example.com" ~/Code/harness/server/logs/requests-*.jsonl   # must print 0
   grep -o '\[\[[A-Z]*_[0-9]*\]\]' ~/Code/harness/server/logs/requests-*.jsonl | sort -u
   ```
4. On B, the worker log shows `perceive job … ok in N ms` lines when screenshots are used.
5. The automated end-to-end run (on A, after `corepack pnpm install-chrome` once):
   ```bash
   cd ~/Code/harness/client
   NB_LIVE=1 corepack pnpm autorun eval --args '{"kind":"openai-compatible","model":"privacy-agent",
     "baseUrl":"http://localhost:8000/v1","tasks":["privacy-contact"],"modes":["auto"],"privacy":true,"vision":true}'
   ```

## 4. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| A logs `VLM link down: … CERTIFICATE_VERIFY_FAILED` | the certificate does not name B's IP, or A has the wrong file | redo 1.2 with the right `subjectAltName=IP:…` and copy `cert.pem` again |
| A logs `VLM link down: … 4403 bad token` | the tokens differ | copy the token again; no spaces or newline at the end |
| A logs `… timed out` / `Connection refused` | firewall, wrong IP or worker not running | `sudo ufw status` on B; `ss -ltnp \| grep 8765` on B |
| `/health` shows `"vlm": null` | the link is down; the agent still runs, text-only | see the log lines above |
| Extension: "Privacy check blocked the request" | the leak check found a value left after redaction | the run stops on purpose; report which kind it names |
| Steps are very slow | qwen3:14b partly on CPU, or the VLM swapping | `ollama ps` on each laptop; lower `TEXT_NUM_CTX`; use `qwen2.5vl:3b` on B |
| "Test" fails with a CORS / origin error | the base URL is wrong | it must be `http://localhost:8000/v1` |
