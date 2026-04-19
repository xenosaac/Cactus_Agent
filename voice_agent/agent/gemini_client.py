"""Hybrid-mode cloud fallback via the google-genai SDK."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, TypeVar

from pydantic import BaseModel

if TYPE_CHECKING:
    from voice_agent.config import Settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


_GEMINI_SCHEMA_KEYS = {
    "type", "properties", "required", "items", "enum",
    "description", "nullable", "format", "minimum", "maximum",
    "minItems", "maxItems", "minLength", "maxLength",
}


def _sanitize_schema(node: Any, *, _in_properties: bool = False) -> Any:
    """Recursively strip JSON-Schema keys Gemini's function declaration rejects.

    MCP tool schemas come with fields like `$schema`, `additionalProperties`,
    `definitions`, `examples`, `oneOf`/`anyOf`, etc. Google's schema validator
    only accepts a narrow subset, so we whitelist and then fix up `required`
    to match what survived in `properties`.
    """
    if isinstance(node, dict):
        if _in_properties:
            return {str(k): _sanitize_schema(v) for k, v in node.items()}
        out: dict[str, Any] = {}
        for k, v in node.items():
            if k == "properties":
                out[k] = _sanitize_schema(v, _in_properties=True)
            elif k in _GEMINI_SCHEMA_KEYS:
                out[k] = _sanitize_schema(v)
        # Gemini requires `type` on every schema node; default to object.
        if "type" not in out and "properties" in out:
            out["type"] = "object"
        # Drop entries from `required` whose property didn't survive the whitelist
        # (e.g. a field defined only via oneOf/anyOf). Otherwise Gemini rejects:
        # "required[N]: property is not defined".
        if "required" in out and isinstance(out.get("properties"), dict):
            props = out["properties"]
            out["required"] = [r for r in out["required"] if r in props]
            if not out["required"]:
                del out["required"]
        return out
    if isinstance(node, list):
        return [_sanitize_schema(x) for x in node]
    return node


def _resolve_json_schema_refs(schema: dict[str, Any]) -> dict[str, Any]:
    defs = schema.pop("$defs", None) or schema.pop("definitions", None) or {}

    def _resolve(obj: Any) -> Any:
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref = str(obj.pop("$ref"))
                ref_name = ref.split("/")[-1]
                if ref_name in defs:
                    resolved = dict(defs[ref_name])
                    for key, value in obj.items():
                        resolved[key] = value
                    return _resolve(resolved)
            return {k: _resolve(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_resolve(v) for v in obj]
        return obj

    return _resolve(schema)


def _response_schema_for_model(model_class: type[BaseModel]) -> dict[str, Any]:
    """Convert a Pydantic model schema to the subset Gemini accepts."""
    schema = _resolve_json_schema_refs(model_class.model_json_schema())

    def _clean(obj: Any) -> Any:
        if isinstance(obj, dict):
            cleaned: dict[str, Any] = {}
            for key, value in obj.items():
                if key in {
                    "$schema",
                    "additionalProperties",
                    "additional_properties",
                    "title",
                    "default",
                }:
                    continue
                cleaned[key] = _clean(value)
            if (
                str(cleaned.get("type", "")).lower() == "object"
                and "properties" in cleaned
                and isinstance(cleaned["properties"], dict)
                and not cleaned["properties"]
            ):
                cleaned["properties"] = {"_placeholder": {"type": "string"}}
            return cleaned
        if isinstance(obj, list):
            return [_clean(v) for v in obj]
        return obj

    return _clean(schema)


class GeminiClient:
    def __init__(self, settings: Settings) -> None:
        from google import genai
        self._genai = genai
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model = settings.gemini_model
        self._vision_model = settings.gemini_vision_model or settings.gemini_model
        self._timeout = settings.gemini_timeout_s
        self._vision_timeout = settings.gemini_vision_timeout_s
        self._vision_thinking_level = settings.gemini_vision_thinking_level

    @property
    def provider(self) -> str:
        """Browser Use BaseChatModel compatibility."""
        return "google"

    @property
    def name(self) -> str:
        """Browser Use BaseChatModel compatibility."""
        return self._model

    @property
    def model(self) -> str:
        """Browser Use token/cost compatibility."""
        return self._model

    @property
    def model_name(self) -> str:
        """Browser Use cloud-event compatibility."""
        return self._model

    async def ainvoke(
        self,
        messages: list[Any],
        output_format: type[T] | None = None,
        tools_json: str | None = None,
    ) -> Any:
        import time as _time
        _t0 = _time.perf_counter()
        from browser_use.llm.views import ChatInvokeCompletion
        from google.genai import types as gtypes

        system_instruction, contents = self._split_system_and_contents(messages)
        log.info(
            "gemini.ainvoke START model=%s msgs=%d contents=%d has_tools=%s has_schema=%s",
            self._model, len(messages), len(contents),
            bool(tools_json), bool(output_format),
        )
        config_kwargs: dict[str, Any] = {}

        if system_instruction:
            # Gemini has no system role; the canonical path is the dedicated
            # `system_instruction` field on GenerateContentConfig.
            config_kwargs["system_instruction"] = system_instruction

        if tools_json:
            tools_py = json.loads(tools_json)
            raw_count = len(tools_py)
            # MCP tools often include JSON-Schema metadata ($schema, additionalProperties,
            # etc.) that Gemini's function_declarations validator rejects. Strip down
            # to the subset Google accepts.
            tools_py = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": _sanitize_schema(t.get("parameters") or {}),
                }
                for t in tools_py
            ]
            log.debug(
                "gemini tool manifest sanitized: %d tools, names=%s",
                raw_count, [t["name"] for t in tools_py][:10],
            )
            config_kwargs["tools"] = [
                {"function_declarations": tools_py}
            ]

        if output_format is not None:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = _response_schema_for_model(output_format)

        config = (
            gtypes.GenerateContentConfig(**config_kwargs) if config_kwargs else None
        )

        def _run() -> Any:
            # google-genai accepts list[ContentDict] at runtime; the type
            # hint is narrower than reality.
            return self._client.models.generate_content(
                model=self._model,
                contents=contents,  # type: ignore[arg-type]
                config=config,
            )

        try:
            resp = await asyncio.wait_for(
                asyncio.to_thread(_run), timeout=self._timeout
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"Gemini timed out after {self._timeout}s"
            ) from exc

        # Default usage — Gemini may not surface exact counts, and Browser Use's
        # ChatInvokeCompletion requires the field to be present.
        usage = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "prompt_cached_tokens": 0, "prompt_cache_creation_tokens": 0,
            "prompt_image_tokens": 0,
        }

        def _build(**kwargs: Any) -> Any:
            try:
                return ChatInvokeCompletion(**kwargs, usage=usage)
            except TypeError:
                return ChatInvokeCompletion(**kwargs)

        if output_format is not None:
            parsed = getattr(resp, "parsed", None)
            if parsed is not None:
                if not isinstance(parsed, output_format):
                    parsed = output_format.model_validate(parsed)
                return _build(completion=parsed)
            try:
                text = getattr(resp, "text", "") or ""
            except Exception:  # noqa: BLE001
                text = ""
            if text:
                parsed_data = json.loads(text)
                return _build(completion=output_format.model_validate(parsed_data))
            raise RuntimeError("Gemini returned no parseable structured response")

        # Extract a function call if present.
        tool_calls: list[Any] = []
        for cand in getattr(resp, "candidates", None) or []:
            parts = getattr(cand.content, "parts", None) or []
            for part in parts:
                fn = getattr(part, "function_call", None)
                if fn is not None:
                    tool_calls.append(self._translate_fc(fn))

        # Gemini raises ValueError on .text when the response has no text parts
        # (only function_call). Swallow it — text is optional.
        try:
            text = getattr(resp, "text", "") or ""
        except Exception:  # noqa: BLE001
            text = ""
        log.info(
            "gemini.ainvoke END duration_ms=%.1f text_len=%d tool_calls=%d",
            (_time.perf_counter() - _t0) * 1000, len(text), len(tool_calls),
        )
        if tool_calls:
            # ChatInvokeCompletion in this Browser Use version has no tool_calls
            # field and Pydantic blocks dynamic attrs. Return a SimpleNamespace
            # that exposes the two attributes the orchestrator looks at.
            from types import SimpleNamespace
            return SimpleNamespace(completion=text, tool_calls=tool_calls)
        return _build(completion=text)

    async def describe_image(
        self,
        *,
        prompt: str,
        image_bytes: bytes,
        mime_type: str = "image/png",
        system_instruction: str | None = None,
        max_tokens: int = 512,
        temperature: float = 0.1,
        top_p: float = 0.9,
        timeout_s: float | None = None,
        model: str | None = None,
        response_mime_type: str | None = None,
        thinking_level: str | None = None,
    ) -> str:
        """Return text for a one-shot image understanding request."""
        import time as _time

        from google.genai import types as gtypes

        selected_model = model or self._vision_model
        _t0 = _time.perf_counter()
        log.info(
            "gemini.describe_image START model=%s bytes=%d",
            selected_model, len(image_bytes),
        )
        config_kwargs: dict[str, Any] = {
            "temperature": temperature,
            "top_p": top_p,
            "max_output_tokens": max_tokens,
        }
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if response_mime_type:
            config_kwargs["response_mime_type"] = response_mime_type
        thinking = thinking_level or self._vision_thinking_level
        if thinking:
            config_kwargs["thinking_config"] = gtypes.ThinkingConfig(
                thinking_level=thinking
            )
        config = gtypes.GenerateContentConfig(**config_kwargs)
        contents = [
            gtypes.Content(
                role="user",
                parts=[
                    gtypes.Part.from_text(text=prompt),
                    gtypes.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                ],
            )
        ]

        def _run() -> Any:
            return self._client.models.generate_content(
                model=selected_model,
                contents=contents,
                config=config,
            )

        timeout = timeout_s or self._vision_timeout
        try:
            resp = await asyncio.wait_for(
                asyncio.to_thread(_run), timeout=timeout
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"Gemini image request timed out after {timeout}s"
            ) from exc

        try:
            text = getattr(resp, "text", "") or ""
        except Exception:  # noqa: BLE001
            text = ""
        log.info(
            "gemini.describe_image END duration_ms=%.1f text_len=%d",
            (_time.perf_counter() - _t0) * 1000, len(text),
        )
        return text.strip()

    def _split_system_and_contents(
        self, messages: list[Any]
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Split messages into (system_instruction, non-system contents).

        Multiple system messages are concatenated with newlines. Non-system
        messages are converted to Gemini's `{role, parts}` shape.
        """
        system_parts: list[str] = []
        out: list[dict[str, Any]] = []
        for m in messages:
            role = getattr(m, "role", None)
            content = getattr(m, "content", None)
            if role is None and isinstance(m, dict):
                role = m.get("role", "user")
                content = m.get("content", "")
            if content is None:
                content = str(m)

            role_str = str(role or "user").lower()
            if "system" in role_str:
                system_parts.append(str(content))
                continue
            gemini_role = (
                "model" if ("assistant" in role_str or "model" in role_str) else "user"
            )
            out.append({"role": gemini_role, "parts": [{"text": str(content)}]})

        system_instruction = "\n\n".join(s for s in system_parts if s.strip()) or None
        return system_instruction, out

    def _translate_fc(self, fn: Any) -> Any:
        name = getattr(fn, "name", "")
        args = getattr(fn, "args", None) or {}
        try:
            from browser_use.llm.views import ToolCall
            return ToolCall(name=name, arguments=dict(args))
        except Exception:  # noqa: BLE001
            from types import SimpleNamespace
            return SimpleNamespace(name=name, arguments=dict(args))
