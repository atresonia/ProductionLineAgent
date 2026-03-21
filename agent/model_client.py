"""
model_client.py — provider-agnostic LLM client

Wraps Anthropic and OpenAI-compatible APIs behind a single interface.
The investigator calls ModelClient.chat() and receives a normalized
response regardless of which backend is configured.

Configuration (env vars):
  MODEL_PROVIDER   — "anthropic" (default) | "openai"
  MODEL_NAME       — model identifier; defaults per provider shown below
  OPENAI_BASE_URL  — base URL for OpenAI-compatible endpoint (Together, OpenRouter, Ollama…)
  OPENAI_API_KEY   — API key for the OpenAI-compatible endpoint
  ANTHROPIC_API_KEY — required when MODEL_PROVIDER=anthropic
"""

import os
import uuid
from dataclasses import dataclass, field
from typing import Any


# ── Normalized response types ─────────────────────────────────────────────────

@dataclass
class TextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class ToolUseBlock:
    type: str = "tool_use"
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedResponse:
    """
    Provider-agnostic response.

    content     — list of TextBlock and ToolUseBlock (same order as model output)
    stop_reason — "end_turn" | "tool_use"
    """
    content: list[TextBlock | ToolUseBlock]
    stop_reason: str


# ── Schema / message converters ───────────────────────────────────────────────

def _anthropic_tool_to_openai(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert one Anthropic tool schema to OpenAI function-calling format."""
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
        },
    }


def _convert_content_to_openai(content: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """
    Convert Anthropic-style content (possibly containing image blocks) to
    OpenAI message content format.

    Anthropic image block:
      {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "<b64>"}}

    OpenAI image block:
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,<b64>"}}
    """
    if isinstance(content, str):
        return content

    converted: list[dict[str, Any]] = []
    for block in content:
        if block.get("type") == "image":
            src = block["source"]
            if src.get("type") == "base64":
                data_uri = f"data:{src['media_type']};base64,{src['data']}"
                converted.append({
                    "type": "image_url",
                    "image_url": {"url": data_uri},
                })
            # url-based Anthropic image blocks are uncommon but handle gracefully
            elif src.get("type") == "url":
                converted.append({
                    "type": "image_url",
                    "image_url": {"url": src["url"]},
                })
        elif block.get("type") == "text":
            converted.append({"type": "text", "text": block["text"]})
        else:
            # pass through unknown block types unchanged
            converted.append(block)

    return converted


def _convert_messages_to_openai(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Convert the Anthropic message list to OpenAI format.

    Differences handled:
    - Anthropic tool_result blocks  → OpenAI "tool" role messages
    - Anthropic assistant content list → OpenAI assistant message with tool_calls
    - Image content blocks          → OpenAI image_url format
    """
    openai_messages: list[dict[str, Any]] = []

    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if role == "user":
            # content is either a string, a list of content blocks, or a list
            # of tool_result blocks (returned after a tool_use turn).
            if isinstance(content, list):
                tool_results = [b for b in content if b.get("type") == "tool_result"]
                other_blocks = [b for b in content if b.get("type") != "tool_result"]

                # Emit tool results as individual "tool" role messages.
                # content may be a plain string or a list of content blocks
                # (text + image) when a tool returned an image_path.
                for tr in tool_results:
                    tr_content = tr["content"]
                    if isinstance(tr_content, list):
                        # Convert Anthropic image blocks → OpenAI image_url blocks
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": tr["tool_use_id"],
                            "content": _convert_content_to_openai(tr_content),
                        })
                    else:
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": tr["tool_use_id"],
                            "content": tr_content,
                        })

                # Any remaining user content blocks
                if other_blocks:
                    openai_messages.append({
                        "role": "user",
                        "content": _convert_content_to_openai(other_blocks),
                    })
            else:
                openai_messages.append({
                    "role": "user",
                    "content": _convert_content_to_openai(content),
                })

        elif role == "assistant":
            if isinstance(content, list):
                text_parts: list[str] = []
                tool_calls: list[dict[str, Any]] = []

                for block in content:
                    # Anthropic SDK objects have .type; plain dicts have ["type"]
                    btype = getattr(block, "type", None) or block.get("type", "")

                    if btype == "text":
                        text = getattr(block, "text", None) or block.get("text", "")
                        if text:
                            text_parts.append(text)

                    elif btype == "tool_use":
                        bid   = getattr(block, "id",    None) or block.get("id",    str(uuid.uuid4()))
                        bname = getattr(block, "name",  None) or block.get("name",  "")
                        binput = getattr(block, "input", None) or block.get("input", {})
                        import json as _json
                        tool_calls.append({
                            "id": bid,
                            "type": "function",
                            "function": {
                                "name": bname,
                                "arguments": _json.dumps(binput),
                            },
                        })

                assistant_msg: dict[str, Any] = {"role": "assistant"}
                if text_parts:
                    assistant_msg["content"] = "\n".join(text_parts)
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                openai_messages.append(assistant_msg)

            else:
                openai_messages.append({
                    "role": "assistant",
                    "content": content,
                })

    return openai_messages


def _openai_response_to_normalized(response: Any) -> NormalizedResponse:
    """
    Convert an openai.types.chat.ChatCompletion to NormalizedResponse.
    """
    import json as _json

    choice = response.choices[0]
    msg    = choice.message
    finish = choice.finish_reason  # "stop" | "tool_calls"

    content_blocks: list[TextBlock | ToolUseBlock] = []

    if msg.content:
        content_blocks.append(TextBlock(text=msg.content))

    if msg.tool_calls:
        for tc in msg.tool_calls:
            try:
                arguments = _json.loads(tc.function.arguments)
            except _json.JSONDecodeError:
                arguments = {"_raw": tc.function.arguments}
            content_blocks.append(ToolUseBlock(
                id=tc.id,
                name=tc.function.name,
                input=arguments,
            ))

    stop_reason = "tool_use" if finish == "tool_calls" else "end_turn"

    return NormalizedResponse(content=content_blocks, stop_reason=stop_reason)


def _anthropic_response_to_normalized(response: Any) -> NormalizedResponse:
    """
    Convert an anthropic.types.Message to NormalizedResponse.

    Handles both SDK objects (block.type attribute) so the caller never needs
    to import the Anthropic SDK types.
    """
    content_blocks: list[TextBlock | ToolUseBlock] = []

    for block in response.content:
        btype = getattr(block, "type", "")
        if btype == "text":
            content_blocks.append(TextBlock(text=block.text))
        elif btype == "tool_use":
            content_blocks.append(ToolUseBlock(
                id=block.id,
                name=block.name,
                input=block.input,
            ))

    # Map Anthropic stop reasons to our canonical set
    stop_map = {"end_turn": "end_turn", "tool_use": "tool_use"}
    stop_reason = stop_map.get(response.stop_reason, "end_turn")

    return NormalizedResponse(content=content_blocks, stop_reason=stop_reason)


# ── ModelClient ───────────────────────────────────────────────────────────────

class ModelClient:
    """
    Single entry point for LLM calls.

    Usage:
        client = ModelClient()
        response = client.chat(
            system="You are …",
            messages=[{"role": "user", "content": "…"}],
            tools=TOOL_SCHEMAS,   # Anthropic-format tool schemas
        )
        # response.stop_reason  → "end_turn" | "tool_use"
        # response.content      → list of TextBlock / ToolUseBlock
    """

    _DEFAULT_MODELS = {
        "anthropic": "claude-opus-4-6",
        "openai":    "gpt-4o",
    }

    def __init__(self) -> None:
        self._provider  = os.getenv("MODEL_PROVIDER", "anthropic").lower()
        self._model     = os.getenv("MODEL_NAME", self._DEFAULT_MODELS.get(self._provider, "claude-opus-4-6"))
        self._max_tokens = 4096

        if self._provider == "anthropic":
            self._client = self._build_anthropic_client()
        elif self._provider == "openai":
            self._client = self._build_openai_client()
        else:
            raise ValueError(
                f"Unknown MODEL_PROVIDER '{self._provider}'. "
                "Valid values: 'anthropic', 'openai'."
            )

    # ── Provider client builders ──────────────────────────────────────────────

    @staticmethod
    def _build_anthropic_client() -> Any:
        try:
            from anthropic import Anthropic
        except ImportError as e:
            raise ImportError("anthropic package is required. Run: pip install anthropic") from e
        return Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    @staticmethod
    def _build_openai_client() -> Any:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ImportError("openai package is required. Run: pip install openai") from e

        base_url = os.getenv("OPENAI_BASE_URL") or None   # None → default OpenAI endpoint
        api_key  = os.getenv("OPENAI_API_KEY", "")

        return OpenAI(api_key=api_key, base_url=base_url)

    # ── Public interface ──────────────────────────────────────────────────────

    def chat(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> NormalizedResponse:
        """
        Send a chat request and return a NormalizedResponse.

        Args:
            system:   System prompt string.
            messages: Conversation history in Anthropic message format.
            tools:    Tool schemas in Anthropic format (will be converted for OpenAI).

        Returns:
            NormalizedResponse with .content (list of TextBlock/ToolUseBlock)
            and .stop_reason ("end_turn" | "tool_use").
        """
        if self._provider == "anthropic":
            return self._chat_anthropic(system, messages, tools)
        return self._chat_openai(system, messages, tools)

    # ── Provider implementations ──────────────────────────────────────────────

    @staticmethod
    def _serialize_content(content: Any) -> Any:
        """
        Convert a list that may contain TextBlock/ToolUseBlock dataclass instances
        into plain dicts that the Anthropic SDK (and httpx JSON encoder) can serialize.
        """
        if not isinstance(content, list):
            return content
        out: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, TextBlock):
                out.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                out.append({
                    "type":  "tool_use",
                    "id":    block.id,
                    "name":  block.name,
                    "input": block.input,
                })
            else:
                out.append(block)
        return out

    def _chat_anthropic(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> NormalizedResponse:
        # Serialize any TextBlock/ToolUseBlock dataclasses in assistant turns
        serialized: list[dict[str, Any]] = [
            {**msg, "content": self._serialize_content(msg["content"])}
            if isinstance(msg.get("content"), list)
            else msg
            for msg in messages
        ]
        kwargs: dict[str, Any] = {
            "model":      self._model,
            "max_tokens": self._max_tokens,
            "system":     system,
            "messages":   serialized,
        }
        if tools:
            kwargs["tools"] = tools

        response = self._client.messages.create(**kwargs)
        return _anthropic_response_to_normalized(response)

    def _chat_openai(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> NormalizedResponse:
        openai_messages = [{"role": "system", "content": system}]
        openai_messages.extend(_convert_messages_to_openai(messages))

        kwargs: dict[str, Any] = {
            "model":      self._model,
            "max_tokens": self._max_tokens,
            "messages":   openai_messages,
        }
        if tools:
            kwargs["tools"]       = [_anthropic_tool_to_openai(t) for t in tools]
            kwargs["tool_choice"] = "auto"

        response = self._client.chat.completions.create(**kwargs)
        return _openai_response_to_normalized(response)
