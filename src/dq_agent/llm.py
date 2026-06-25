from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel

from .config import LLMConfig


ResponseT = TypeVar("ResponseT", bound=BaseModel)


SYSTEM_POLICY = """You are a data-quality reasoning component.
Return only JSON matching the supplied schema. Use only the provided metadata and
aggregate evidence. Never invent evidence, credentials, table names, or SQL. When
the evidence is insufficient, explicitly mark the conclusion inconclusive.
"""


def _request_text(task: str, context: dict[str, Any], schema: dict[str, Any]) -> str:
    return json.dumps(
        {"task": task, "context": context, "required_response_schema": schema},
        default=str,
        ensure_ascii=True,
    )


class LLMAdapter(ABC):
    def __init__(self, config: LLMConfig):
        self.config = config

    def complete_structured(
        self,
        task: str,
        context: dict[str, Any],
        response_model: type[ResponseT],
    ) -> ResponseT:
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                payload = self._complete(task, context, response_model.model_json_schema())
                return response_model.model_validate_json(payload)
            except Exception as exc:  # provider SDK exceptions do not share a base type
                last_error = exc
                if attempt >= self.config.max_retries:
                    break
                time.sleep(min(2**attempt, 8))
        raise RuntimeError(
            f"LLM request failed after {self.config.max_retries + 1} attempts"
        ) from last_error

    @abstractmethod
    def _complete(self, task: str, context: dict[str, Any], schema: dict[str, Any]) -> str:
        raise NotImplementedError


class VertexGeminiAdapter(LLMAdapter):
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("Install google-genai to use Vertex Gemini") from exc
        project = config.resolved_project()
        if not project:
            raise RuntimeError(f"Set {config.project_env} or llm.project for Vertex Gemini")
        self._genai = genai
        self._client = genai.Client(vertexai=True, project=project, location=config.location)

    def _complete(self, task: str, context: dict[str, Any], schema: dict[str, Any]) -> str:
        types = self._genai.types
        response = self._client.models.generate_content(
            model=self.config.model,
            contents=_request_text(task, context, schema),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_POLICY,
                temperature=self.config.temperature,
                max_output_tokens=self.config.max_output_tokens,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        if not response.text:
            raise RuntimeError("Vertex Gemini returned no text")
        return response.text


class OpenAIAdapter(LLMAdapter):
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("Install openai to use the OpenAI adapter") from exc
        key_env = config.api_key_env or "OPENAI_API_KEY"
        api_key = os.getenv(key_env) or ("ollama" if config.base_url else None)
        if not api_key:
            raise RuntimeError(f"Set {key_env} for the OpenAI adapter")
        self._client = OpenAI(
            api_key=api_key,
            base_url=config.base_url or None,
            timeout=config.timeout_seconds,
        )

    def _complete(self, task: str, context: dict[str, Any], schema: dict[str, Any]) -> str:
        # Use chat.completions — compatible with Ollama and standard OpenAI
        response = self._client.chat.completions.create(
            model=self.config.model,
            messages=[
                {"role": "system", "content": SYSTEM_POLICY},
                {"role": "user", "content": _request_text(task, context, schema)},
            ],
            temperature=self.config.temperature,
            max_tokens=self.config.max_output_tokens,
            response_format={"type": "json_object"},
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise RuntimeError("OpenAI/Ollama returned no text")
        # Strip markdown code fences if the model wrapped the JSON
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        return text


class AnthropicAdapter(LLMAdapter):
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        try:
            from anthropic import Anthropic
        except ImportError as exc:
            raise RuntimeError("Install anthropic to use the Anthropic adapter") from exc
        key_env = config.api_key_env or "ANTHROPIC_API_KEY"
        if not os.getenv(key_env):
            raise RuntimeError(f"Set {key_env} for the Anthropic adapter")
        self._client = Anthropic(api_key=os.environ[key_env], timeout=config.timeout_seconds)

    def _complete(self, task: str, context: dict[str, Any], schema: dict[str, Any]) -> str:
        message = self._client.messages.create(
            model=self.config.model,
            system=SYSTEM_POLICY,
            max_tokens=self.config.max_output_tokens,
            temperature=self.config.temperature,
            messages=[{"role": "user", "content": _request_text(task, context, schema)}],
        )
        text = "".join(block.text for block in message.content if getattr(block, "type", "") == "text")
        if not text:
            raise RuntimeError("Anthropic returned no text")
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        return text


def make_llm_adapter(config: LLMConfig) -> LLMAdapter | None:
    if not config.enabled or config.provider == "disabled":
        return None
    adapters: dict[str, type[LLMAdapter]] = {
        "vertex_gemini": VertexGeminiAdapter,
        "openai": OpenAIAdapter,
        "anthropic": AnthropicAdapter,
    }
    return adapters[config.provider](config)
