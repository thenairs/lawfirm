"""Shared configuration, client setup, and the structured-call helper every
AI stage uses. Centralizing the retry/validation logic here is what makes
the per-stage error handling in the design spec (schema-invalid output ->
one repair retry -> flag for review) consistent across all six AI stages.

Supports two providers, selected via MODEL_PROVIDER:
  - "huggingface" (default): HF's OpenAI-compatible router
    (router.huggingface.co/v1) in front of an open vision-capable model.
    Not every backend behind the router honors strict JSON-schema response
    formatting, so this path uses prompt-embedded schema + parse + repair
    retry instead of relying on a guarantee.
  - "openai": native OpenAI Structured Outputs (schema-guaranteed JSON).
"""
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import openai

ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = ROOT / "logs"
OUTPUT_DIR = ROOT / "output"
LEGAL_CORPUS_DIR = ROOT / "legal_corpus"

PROVIDER = os.environ.get("MODEL_PROVIDER", "huggingface").lower()
OCR_CONFIDENCE_THRESHOLD = float(os.environ.get("OCR_CONFIDENCE_THRESHOLD", "0.75"))
CLASSIFICATION_CONFIDENCE_THRESHOLD = float(
    os.environ.get("CLASSIFICATION_CONFIDENCE_THRESHOLD", "0.6")
)

DOCUMENT_CATEGORIES = [
    "FIR",
    "Charge Sheet",
    "Witness Statement",
    "Previous Hearing Notes",
    "Court Order",
    "Medical Report",
    "Evidence Document",
    "Case Law",
    "Other",
]

if PROVIDER == "huggingface":
    _api_key = os.environ.get("HF_API_TOKEN") or os.environ.get("HF_TOKEN")
    if not _api_key:
        raise RuntimeError(
            "HF_API_TOKEN is not set. Copy .env.example to .env and add your "
            "Hugging Face access token, or `export HF_API_TOKEN=hf_...` before running."
        )
    # Together's Cloudflare WAF blocks the openai SDK's default User-Agent on
    # multimodal (image) requests specifically -- a normal browser UA passes.
    client = openai.OpenAI(
        api_key=_api_key,
        base_url="https://router.huggingface.co/v1",
        default_headers={"User-Agent": "Mozilla/5.0"},
    )
    # The only $0-priced model on the router with both text and image input
    # as of this build; verified working for chat + vision below. It's a
    # reasoning model that spends a large token budget on chain-of-thought
    # before its answer -- see the max_tokens floor in call_structured.
    MODEL = os.environ.get("TRIAL_PREP_MODEL", "prism-ml/Ternary-Bonsai-27B-AWQ-4bit")
    SUPPORTS_STRICT_SCHEMA = False
    # Observed reasoning overhead scales with input size -- a few hundred
    # tokens for a one-line prompt, several thousand once synthesizing
    # across all 6 case documents (timeline/evidence/report stages).
    REASONING_TOKEN_FLOOR = 6000
elif PROVIDER == "openai":
    _api_key = os.environ.get("OPENAI_API_KEY")
    if not _api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and add your "
            "key, or `export OPENAI_API_KEY=sk-...` before running."
        )
    client = openai.OpenAI(api_key=_api_key)
    MODEL = os.environ.get("TRIAL_PREP_MODEL", "gpt-4o")
    SUPPORTS_STRICT_SCHEMA = True
    REASONING_TOKEN_FLOOR = 0
else:
    raise RuntimeError(f"Unknown MODEL_PROVIDER: {PROVIDER!r}. Use 'huggingface' or 'openai'.")


class StageError(Exception):
    """Raised when an AI stage cannot produce schema-valid output after retry."""


def _strictify(schema):
    """OpenAI's Structured Outputs 'strict' mode requires every object node
    to list ALL of its properties as required and set
    additionalProperties=False, recursively. The stage modules write plain
    JSON Schema without worrying about that -- this converts it at call
    time so every schema in pipeline/*.py stays readable.
    """
    if not isinstance(schema, dict):
        return schema
    schema = {k: v for k, v in schema.items() if k not in ("minimum", "maximum")}
    if schema.get("type") == "object" and "properties" in schema:
        schema["properties"] = {k: _strictify(v) for k, v in schema["properties"].items()}
        schema["required"] = list(schema["properties"].keys())
        schema["additionalProperties"] = False
    elif schema.get("type") == "array" and "items" in schema:
        schema["items"] = _strictify(schema["items"])
    return schema


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(raw: str):
    """Open models frequently wrap JSON in markdown fences or add stray
    text despite instructions not to. Strip fences and grab the outermost
    {...} object before parsing.
    """
    text = _FENCE_RE.sub("", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1:
        text = text[start:end + 1]
    return json.loads(text)


def call_structured(
    *,
    system: str,
    user_content,
    tool_name: str,
    tool_description: str,
    input_schema: dict,
    max_tokens: int = 4096,
):
    """Call the model with a JSON-schema-constrained response so the result
    is guaranteed-shape JSON. On malformed/unparseable output, retries once
    with the validation error appended (the "repair prompt" from the error
    handling spec) before raising StageError for the caller to flag the
    document/stage as needs_review instead of silently continuing.
    """
    if SUPPORTS_STRICT_SCHEMA:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": tool_name,
                "description": tool_description,
                "schema": _strictify(input_schema),
                "strict": True,
            },
        }
        effective_system = system
    else:
        response_format = None
        effective_system = (
            f"{system}\n\nRespond with ONLY a single JSON object matching this "
            f"JSON Schema -- no markdown code fences, no commentary, no text "
            f"before or after the JSON:\n{json.dumps(input_schema)}"
        )

    messages = [
        {"role": "system", "content": effective_system},
        {"role": "user", "content": user_content},
    ]
    effective_max_tokens = max_tokens + REASONING_TOKEN_FLOOR

    last_error = None
    for attempt in range(2):
        kwargs = dict(model=MODEL, max_completion_tokens=effective_max_tokens, messages=messages)
        if response_format is not None:
            kwargs["response_format"] = response_format
        try:
            response = client.chat.completions.create(**kwargs)
        except openai.APIError as exc:
            last_error = f"API error: {exc}"
            messages.append(
                {
                    "role": "user",
                    "content": f"The previous request failed: {last_error}. Please retry.",
                }
            )
            continue

        choice = response.choices[0]
        raw = choice.message.content if choice.message else None

        if choice.finish_reason == "length":
            last_error = "Response was truncated (max_tokens too low)."
        elif not raw:
            last_error = "Model returned no content."
        else:
            try:
                return json.loads(raw) if SUPPORTS_STRICT_SCHEMA else _extract_json(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                last_error = f"Invalid JSON: {exc}"

        messages.append(
            {
                "role": "user",
                "content": (
                    f"Your previous response was invalid: {last_error}. "
                    f"Return valid JSON matching the required schema, with no markdown fences."
                ),
            }
        )

    raise StageError(
        f"Stage using schema '{tool_name}' failed validation after retry: {last_error}"
    )
