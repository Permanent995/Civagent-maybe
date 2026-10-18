"""Test collection policy.

Legacy civsim/JVM/Redis tests (the frozen fuxi stack, EXECUTION.md W7) are skipped by
default because they require JPype + a live Redis and are being reworked separately.
Set CIVAGENT_LEGACY_TESTS=1 to include them.
"""
import os
import sys
from pathlib import Path

import pytest

# 仓库根目录进 sys.path：arena 现在是包，测试用 `from arena import ...` 而不改 sys.path 魔法。
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# LLM-related env keys that code paths may write directly (monkeypatch cannot undo those).
_ENV_KEYS = (
    "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_BASE_URL",
    "LLM_DEFAULT_PROVIDER", "OLLAMA_BASE_URL",
    "CIVAGENT_MODEL", "CIVAGENT_STREAMING", "CIVAGENT_SAMPLING",
)


@pytest.fixture(autouse=True)
def _isolate_machine_secrets(monkeypatch, tmp_path):
    """Keep the machine's real config out of the tests.

    - The app writes `~/.civagent/secrets.json` when the user configures a provider in-game
      (Options -> CivAgent); civagent.config loads it, so a 'no key configured' test would
      otherwise see the developer's real key and the suite would be machine-dependent.
    - `secrets_store.apply_secrets_to_environ()` writes os.environ directly, which monkeypatch
      cannot undo, so snapshot/restore those keys around every test.
    """
    monkeypatch.setenv("CIVAGENT_SECRETS_PATH", str(tmp_path / "absent-secrets.json"))
    # Never inherit the developer's real .env (config._load_dotenv writes os.environ directly, so
    # monkeypatch cannot undo it and every config reload would re-apply the relay endpoint).
    monkeypatch.setenv("CIVAGENT_NO_DOTENV", "1")
    # Start every test from a clean LLM env: importing the app modules (collection time) can
    # apply the machine's real sidecar to os.environ, and monkeypatch cannot undo a foreign write.
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


collect_ignore_glob = []
if not os.environ.get("CIVAGENT_LEGACY_TESTS"):
    collect_ignore_glob += [
        "test_bargain.py", "test_civagent.py", "test_intention.py",
        "test_prologue.py", "test_reflection.py", "test_simulator.py", "test_skill.py",
    ]
