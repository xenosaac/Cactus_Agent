from __future__ import annotations

from voice_agent.agent.gemini_client import (
    GeminiClient,
    _response_schema_for_model,
    _sanitize_schema,
)
from voice_agent.config import Mode, Settings


def test_gemini_client_exposes_browser_use_metadata() -> None:
    client = GeminiClient(
        Settings(
            mode=Mode.HYBRID,
            gemini_api_key="test",
            gemini_model="gemini-2.5-flash",
        )
    )

    assert client.provider == "google"
    assert client.name == "gemini-2.5-flash"
    assert client.model == "gemini-2.5-flash"
    assert client.model_name == "gemini-2.5-flash"


def test_tool_schema_sanitization_preserves_property_names() -> None:
    schema = {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "Task to run",
                "additionalProperties": False,
            }
        },
        "required": ["task"],
        "additionalProperties": False,
    }

    sanitized = _sanitize_schema(schema)

    assert sanitized["properties"]["task"]["type"] == "string"
    assert sanitized["required"] == ["task"]
    assert "additionalProperties" not in sanitized


def test_browser_use_response_schema_removes_unsupported_fields() -> None:
    from browser_use.agent.views import AgentOutput

    schema = _response_schema_for_model(AgentOutput)
    rendered = str(schema)

    assert "additionalProperties" not in rendered
    assert "additional_properties" not in rendered
    assert schema["properties"]
    assert "action" in schema["properties"]
