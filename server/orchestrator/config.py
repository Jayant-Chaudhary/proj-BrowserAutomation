"""Settings of the orchestrator, read once from environment variables. Defaults suit one laptop running everything."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).lower() in {"1", "true", "yes"}


@dataclass(frozen=True)
class Settings:
    # The decider: qwen3:14b in Ollama on this laptop.
    ollama_url: str = os.environ.get("OLLAMA_URL", "http://localhost:11434")
    text_model: str = os.environ.get("TEXT_MODEL", "qwen3:14b")
    num_ctx: int = int(os.environ.get("TEXT_NUM_CTX", "16384"))
    text_timeout_s: float = float(os.environ.get("TEXT_TIMEOUT_S", "120"))

    # The VLM worker on Laptop B, over wss. Empty URL: run text-only.
    vlm_url: str = os.environ.get("VLM_URL", "")
    vlm_token: str = os.environ.get("VLM_TOKEN", "")
    # The worker's certificate (or the mkcert root): the only certificate trusted for the link.
    vlm_ca_file: str = os.environ.get("VLM_CA_FILE", "")
    # ws:// without TLS, for a test on one machine only.
    vlm_allow_insecure: bool = _bool("VLM_ALLOW_INSECURE")
    vlm_timeout_s: float = float(os.environ.get("VLM_TIMEOUT_S", "10"))

    # Retries after the first attempt, before the step becomes ask_user.
    max_retries: int = int(os.environ.get("MAX_RETRIES", "2"))
    # Snapshot text kept in the prompt, in characters (about 3k tokens).
    snapshot_chars: int = int(os.environ.get("SNAPSHOT_CHARS", "12000"))

    # Save each image the server receives (already masked by the extension) to logs/images/, for the demo and debugging.
    log_images: bool = _bool("LOG_IMAGES")
    log_dir: Path = Path(os.environ.get("LOG_DIR", Path(__file__).resolve().parent.parent / "logs"))


settings = Settings()
