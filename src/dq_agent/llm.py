from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict

from .config import LLMConfig


ResponseT = TypeVar("ResponseT", bound=BaseModel)


SYSTEM_POLICY = """You are a governed data-quality reasoning component.
Return only JSON matching the supplied schema. Use only supplied metadata, trusted
context, and evidence. Cite supporting context/evidence IDs in your response. Never
invent tables, columns, relationships, filters, values, credentials, tools, or SQL.
If evidence is insufficient, require review or mark the conclusion inconclusive.
"""


class LLMCapabilities(BaseModel):
    structured_output: bool = True
    native_tools: bool = False
    provider_independent_actions: bool = True


class LLMHealth(BaseModel):
    backend: str
    api_family: str
    model: str
    ok: bool
    detail: str


class _HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ok: bool


def _request_text(task: str, context: dict[str, Any], schema: dict[str, Any]) -> str:
    return json.dumps(
        {"task": task, "context": context, "required_response_schema": schema},
        default=str, ensure_ascii=True,
    )


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return text


class LLMClient(ABC):
    def __init__(self, config: LLMConfig):
        self.config = config

    def capabilities(self) -> LLMCapabilities:
        return LLMCapabilities()

    def health_check(self) -> LLMHealth:
        try:
            response = self.generate_structured(
                "Return {\"ok\": true} to confirm schema-constrained output.", {}, _HealthResponse
            )
            if not response.ok:
                raise RuntimeError("LLM health response did not confirm readiness")
            return LLMHealth(
                backend=self.config.backend, api_family=self.config.api_family,
                model=self.config.model, ok=True,
                detail="configuration, connection, model, and structured output succeeded",
            )
        except Exception as exc:
            raise RuntimeError(
                f"Mandatory LLM preflight failed for backend={self.config.backend} "
                f"api_family={self.config.api_family} model={self.config.model}: {exc}"
            ) from exc

    def healthcheck(self) -> LLMHealth:
        """Compatibility alias for older notebook calls."""
        return self.health_check()

    def generate_structured(
        self, task: str, context: dict[str, Any], response_model: type[ResponseT]
    ) -> ResponseT:
        last_error: Exception | None = None
        schema = response_model.model_json_schema()
        for attempt in range(self.config.max_retries + 1):
            try:
                payload = _strip_fences(self._generate(task, context, schema))
                return response_model.model_validate_json(payload)
            except Exception as exc:
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                time.sleep(min(2**attempt, 8))
        raise RuntimeError(
            f"LLM structured request failed after {self.config.max_retries + 1} attempts: {last_error}"
        ) from last_error

    def complete_structured(
        self, task: str, context: dict[str, Any], response_model: type[ResponseT]
    ) -> ResponseT:
        """Compatibility name retained for existing workflow calls."""
        return self.generate_structured(task, context, response_model)

    @abstractmethod
    def _generate(self, task: str, context: dict[str, Any], schema: dict[str, Any]) -> str:
        raise NotImplementedError


class OpenAICompatibleClient(LLMClient):
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install openai to use an OpenAI-compatible LLM") from exc
        key_env = config.api_key_env or "OPENAI_API_KEY"
        api_key = os.getenv(key_env)
        if config.backend == "ollama":
            api_key = api_key or "ollama"
            self._verify_ollama()
        elif config.backend == "vertex" and not api_key:
            try:
                import google.auth
                from google.auth.transport.requests import Request
            except ImportError as exc:
                raise RuntimeError("Install google-auth for Vertex OpenAI-compatible models") from exc
            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            credentials.refresh(Request())
            api_key = credentials.token
        elif not api_key:
            raise RuntimeError(f"Set {key_env} for backend={config.backend}")
        self._client = OpenAI(
            api_key=api_key, base_url=config.endpoint or None, timeout=config.timeout_seconds
        )

    def _verify_ollama(self) -> None:
        root = str(self.config.endpoint).rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        try:
            with urllib.request.urlopen(f"{root}/api/tags", timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(
                f"Ollama is not available at {root}. Start it with 'ollama serve'. Original error: {exc}"
            ) from exc
        available = {str(item.get("name")) for item in payload.get("models", [])}
        model = self.config.model
        if model not in available and f"{model}:latest" not in available:
            raise RuntimeError(
                f"Ollama model {model!r} is not installed. Run 'ollama pull {model}'. "
                f"Available models: {sorted(available)}"
            )

    def _generate(self, task: str, context: dict[str, Any], schema: dict[str, Any]) -> str:
        response = self._client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "system", "content": SYSTEM_POLICY},
                {"role": "user", "content": _request_text(task, context, schema)},
            ],
            temperature=self.config.temperature,
            max_tokens=self.config.max_output_tokens,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "dq_response", "strict": False, "schema": schema},
            },
        )
        text = response.choices[0].message.content or ""
        if not text:
            raise RuntimeError("OpenAI-compatible endpoint returned no content")
        return text


class VertexGeminiClient(LLMClient):
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("Install google-genai to use Vertex Gemini") from exc
        project = config.resolved_project()
        if not project:
            raise RuntimeError(f"Set {config.project_env} or llm.project for Vertex")
        self._genai = genai
        self._client = genai.Client(vertexai=True, project=project, location=config.location)

    def _generate(self, task: str, context: dict[str, Any], schema: dict[str, Any]) -> str:
        response = self._client.models.generate_content(
            model=self.config.model,
            contents=_request_text(task, context, schema),
            config=self._genai.types.GenerateContentConfig(
                system_instruction=SYSTEM_POLICY,
                temperature=self.config.temperature,
                max_output_tokens=self.config.max_output_tokens,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        if not response.text:
            raise RuntimeError("Vertex Gemini returned no content")
        return response.text


class AnthropicClient(LLMClient):
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("Install anthropic to use the Anthropic API family") from exc
        if config.backend == "vertex":
            project = config.resolved_project()
            if not project:
                raise RuntimeError(f"Set {config.project_env} or llm.project for Vertex")
            self._client = anthropic.AnthropicVertex(project_id=project, region=config.location)
        else:
            key_env = config.api_key_env or "ANTHROPIC_API_KEY"
            if not os.getenv(key_env):
                raise RuntimeError(f"Set {key_env} for Anthropic")
            self._client = anthropic.Anthropic(
                api_key=os.environ[key_env], timeout=config.timeout_seconds
            )

    def _generate(self, task: str, context: dict[str, Any], schema: dict[str, Any]) -> str:
        message = self._client.messages.create(
            model=self.config.model,
            system=SYSTEM_POLICY,
            max_tokens=self.config.max_output_tokens,
            temperature=self.config.temperature,
            messages=[{"role": "user", "content": _request_text(task, context, schema)}],
        )
        text = "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )
        if not text:
            raise RuntimeError("Anthropic returned no content")
        return text


class _WarmupPayload(BaseModel):
    ok: bool = True


def _ollama_root(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url.rstrip("/")


def ensure_ollama_running(base_url: str, timeout_seconds: float = 30.0) -> None:
    root = _ollama_root(base_url)
    health_url = f"{root}/"

    def _ping() -> bool:
        try:
            with urllib.request.urlopen(health_url, timeout=3):
                return True
        except Exception:
            return False

    if _ping():
        return

    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "Ollama is not running and the 'ollama' binary was not found on PATH. "
            "Start Ollama manually or install it from https://ollama.ai"
        )

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        time.sleep(1.0)
        if _ping():
            return

    raise RuntimeError(
        f"Ollama did not respond at {health_url} within {timeout_seconds:.0f}s "
        "after attempting to start it. Check that 'ollama serve' launched correctly."
    )


def ensure_model_available(base_url: str, model: str, timeout_seconds: float = 120.0) -> None:
    root = _ollama_root(base_url)
    tags_url = f"{root}/api/tags"

    try:
        with urllib.request.urlopen(tags_url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as exc:
        raise RuntimeError(f"Could not reach Ollama tags endpoint {tags_url}: {exc}") from exc

    available = {m.get("name", "") for m in data.get("models", [])}
    base_name = model.split(":")[0]
    if model in available or f"{base_name}:latest" in available:
        return

    result = subprocess.run(["ollama", "pull", model], timeout=timeout_seconds)
    if result.returncode != 0:
        raise RuntimeError(f"'ollama pull {model}' failed with return code {result.returncode}")


def warmup_model(adapter: "LLMClient") -> None:
    try:
        adapter.complete_structured("ping", {}, _WarmupPayload)
    except Exception:
        pass


def make_llm_adapter(config: LLMConfig) -> LLMClient:
    if config.api_family == "openai_compatible":
        return OpenAICompatibleClient(config)
    if config.backend == "vertex" and config.api_family == "google_genai":
        return VertexGeminiClient(config)
    if config.api_family == "anthropic":
        return AnthropicClient(config)
    raise RuntimeError(
        f"Unsupported LLM transport backend={config.backend} api_family={config.api_family}"
    )


# Compatibility alias for existing imports.
LLMAdapter = LLMClient
