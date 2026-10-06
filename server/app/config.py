import os
from pathlib import Path

def _resolve_llm_provider() -> str:
    """Pick the LLM provider, honouring the legacy MUAPI-only environment."""
    explicit = os.getenv("LLM_PROVIDER", "").strip().lower().replace("-", "_")
    if explicit in {"openai_compatible", "muapi"}:
        return explicit
    # Older deployments only set MUAPI_API_KEY. Keep them working unchanged.
    if os.getenv("MUAPI_API_KEY", "").strip():
        return "muapi"
    return "openai_compatible"


class Settings:
    # Any OpenAI-compatible chat completions server (OpenRouter, Ollama,
    # LM Studio, llama.cpp, vLLM, ...) or the legacy MUAPI prediction API.
    LLM_PROVIDER: str = _resolve_llm_provider()
    LLM_API_KEY: str = (
        os.getenv("LLM_API_KEY", "").strip()
        or (os.getenv("MUAPI_API_KEY", "").strip() if LLM_PROVIDER == "muapi" else "")
    )
    LLM_BASE_URL: str = (
        os.getenv("LLM_BASE_URL", "").strip()
        or (os.getenv("MUAPI_BASE_URL", "").strip() if LLM_PROVIDER == "muapi" else "")
    ).rstrip("/")
    LLM_REASONING_EFFORT: str = os.getenv("LLM_REASONING_EFFORT", "").strip().lower()
    # Let the model call the governed tools (workspace, computer, connectors)
    # through OpenAI-style function calling. Approvals still apply.
    LLM_TOOLS_ENABLED: bool = os.getenv("LLM_TOOLS_ENABLED", "1").strip().lower() in {"1", "true", "yes"}
    LLM_MAX_TOOL_ROUNDS: int = int(os.getenv("LLM_MAX_TOOL_ROUNDS", "12"))
    # Screenshots as images for the model: auto (unless the catalog says the
    # model has no vision), always, or never.
    LLM_SCREENSHOTS_TO_MODEL: str = os.getenv("LLM_SCREENSHOTS_TO_MODEL", "auto").strip().lower()
    COMPOSIO_API_KEY: str = os.getenv("COMPOSIO_API_KEY", "")
    DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL", "").strip() or (
        "grok-4-5" if LLM_PROVIDER == "muapi" else "x-ai/grok-4.5"
    )
    DATA_DIR: Path = Path(
        os.getenv("DATA_DIR", str(Path.home() / ".open-grok-bot"))
    ).expanduser().resolve()
    WORKSPACE_ROOT: Path = Path(
        os.getenv("WORKSPACE_ROOT", str(Path(__file__).resolve().parents[2]))
    ).expanduser().resolve()
    WORKSPACE_MAX_FILE_BYTES: int = int(os.getenv("WORKSPACE_MAX_FILE_BYTES", "131072"))
    APPROVAL_TIMEOUT_SECONDS: int = int(os.getenv("APPROVAL_TIMEOUT_SECONDS", "120"))
    AUTH_SESSION_MAX_AGE: int = int(os.getenv("AUTH_SESSION_MAX_AGE", "86400"))
    AUTH_COOKIE_SECURE: bool = os.getenv("AUTH_COOKIE_SECURE", "0").lower() in {"1", "true", "yes"}
    COMPUTER_PROVIDER: str = os.getenv("COMPUTER_PROVIDER", "fake").strip().lower()
    COMPUTER_DOCKER_IMAGE: str = os.getenv(
        "COMPUTER_DOCKER_IMAGE", "open-grok-bot-computer:1.62.1"
    ).strip()
    COMPUTER_DOCKER_BINARY: str = os.getenv("COMPUTER_DOCKER_BINARY", "docker").strip()
    COMPUTER_DOCKER_WORKSPACE_ROOT: Path = Path(
        os.getenv("COMPUTER_DOCKER_WORKSPACE_ROOT", str(DATA_DIR / "computers"))
    ).expanduser().resolve()
    COMPUTER_DOCKER_CPU_LIMIT: str = os.getenv("COMPUTER_DOCKER_CPU_LIMIT", "2.0").strip()
    COMPUTER_DOCKER_MEMORY_LIMIT: str = os.getenv("COMPUTER_DOCKER_MEMORY_LIMIT", "2g").strip()
    COMPUTER_DOCKER_PIDS_LIMIT: int = int(os.getenv("COMPUTER_DOCKER_PIDS_LIMIT", "512"))
    COMPUTER_DOCKER_START_TIMEOUT: float = float(
        os.getenv("COMPUTER_DOCKER_START_TIMEOUT", "20")
    )
    COMPUTER_DOCKER_COMMAND_TIMEOUT: float = float(
        os.getenv("COMPUTER_DOCKER_COMMAND_TIMEOUT", "30")
    )
    COMPUTER_DOCKER_RUNTIME_PORT: int = int(os.getenv("COMPUTER_DOCKER_RUNTIME_PORT", "3000"))
    # When the API itself runs in a container (docker compose), runtime
    # containers join this Docker network and are reached by name instead of
    # through a port published on the host's loopback.
    COMPUTER_DOCKER_NETWORK: str = os.getenv("COMPUTER_DOCKER_NETWORK", "").strip()
    # "bind" mounts a host directory per computer; "volume" uses a named Docker
    # volume per computer, which works wherever the API cannot see host paths.
    COMPUTER_DOCKER_WORKSPACE_MODE: str = os.getenv("COMPUTER_DOCKER_WORKSPACE_MODE", "bind").strip().lower()
    # In bind mode from inside a container: the host path that corresponds to
    # COMPUTER_DOCKER_WORKSPACE_ROOT, because the daemon resolves mount sources
    # on the host.
    COMPUTER_DOCKER_HOST_WORKSPACE_ROOT: str = os.getenv("COMPUTER_DOCKER_HOST_WORKSPACE_ROOT", "").strip()
    COMPUTER_DOCKER_SECCOMP_PROFILE: Path = Path(
        os.getenv(
            "COMPUTER_DOCKER_SECCOMP_PROFILE",
            str(Path(__file__).resolve().parents[2] / "runtime" / "seccomp_profile.json"),
        )
    ).expanduser().resolve()
    CORS_ORIGINS = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://127.0.0.1:3000,http://localhost:3000",
        ).split(",")
        if origin.strip()
    ]
    HOST: str = os.getenv("HOST", "127.0.0.1")
    PORT: int = int(os.getenv("PORT", "8000"))

    def __init__(self):
        self.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.COMPUTER_DOCKER_WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

settings = Settings()
