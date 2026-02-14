"""
Runtime Credential Store — Nexus-Zero
======================================
Thread-safe in-memory credential store.
Agents call set_credentials() via MCP, and all other tools
read from here with env-var fallback.

Credentials live ONLY in memory — they vanish when the
container scales to zero. Zero trust, no persistence.
"""

import threading

_lock = threading.Lock()
_credentials: dict[str, str] = {}


def set_credential(key: str, value: str) -> None:
    """Store a credential in memory."""
    with _lock:
        _credentials[key] = value


def get_credential(key: str, fallback_env: str | None = None) -> str:
    """
    Get a credential.  Priority order:
      1. Runtime value (set via MCP tool)
      2. Environment variable (deploy-time)
      3. Empty string
    """
    import os
    with _lock:
        val = _credentials.get(key)
    if val:
        return val
    if fallback_env:
        return os.environ.get(fallback_env, "")
    return os.environ.get(key, "")


def get_all_credentials() -> dict[str, str]:
    """Return a REDACTED view of stored credentials (for status checks)."""
    with _lock:
        return {
            k: f"{v[:4]}...{v[-4:]}" if len(v) > 8 else "****"
            for k, v in _credentials.items()
        }


def clear_credentials() -> None:
    """Wipe all runtime credentials."""
    with _lock:
        _credentials.clear()
