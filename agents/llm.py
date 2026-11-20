"""One small interface over the model providers, with schema-constrained JSON output, a response cache,
token accounting and a hard budget. Agents never talk to a provider SDK directly.

Providers (LLM_PROVIDER):
  anthropic      Anthropic Messages API, structured outputs (output_config.format = json_schema)
  openai_compat  any OpenAI-compatible chat completions endpoint (OpenAI, Azure-style gateways, Ollama, vLLM,
                 llama.cpp), using response_format = json_schema
  replay         answers only from recorded responses in LLM_REPLAY_DIR; makes no network call
  none           agents are disabled; the pipeline itself never needs a model
"""
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import jsonschema
import psycopg
from psycopg.types.json import Jsonb

from pipeline.config import Settings

log = logging.getLogger("agents")

# List prices in USD per million tokens (input, output), read from the provider's pricing page on 2026-11-17.
# The Haiku price is the one for prompts up to 100,000 tokens, which every prompt built here is.
# Used only for the cost estimate in reports. Override with LLM_PRICE_IN / LLM_PRICE_OUT.
LIST_PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}
DEFAULT_MODELS = {"anthropic": {"small": "claude-haiku-5-5", "strong": "claude-sonnet-5-5"}}
# Room for the answer plus any thinking the model does. Kept under the size at which the Anthropic SDK
# insists on streaming (about 21,000 tokens for a non-streaming request).
MAX_TOKENS = 16000


class LLMDisabled(RuntimeError):
    """No provider is configured. Callers skip agent work; the pipeline is unaffected."""


class BudgetExceeded(RuntimeError):
    """The token budget for this command is spent. Remaining values stay unmapped and flagged."""


class LLMOutputError(RuntimeError):
    """The model's answer did not satisfy the schema, even after one retry."""


@dataclass
class LLMResult:
    data: dict
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cached: bool
    cache_key: str


# transport(model, system, user, schema, max_tokens) -> (json_text, input_tokens, output_tokens)
Transport = Callable[[str, str, str, dict, int], tuple[str, int, int]]


class LLM:
    def __init__(self, settings: Settings, conn: psycopg.Connection | None = None, transport: Transport | None = None):
        self.settings = settings
        self.conn = conn
        self.provider = settings.llm_provider if transport is None else "stub"
        self._transport = transport or self._pick_transport()
        self.tokens_used = 0
        self.calls = 0
        self.cache_hits = 0

    # ------------------------------------------------------------------ public
    def model_for(self, tier: str) -> str:
        configured = self.settings.llm_model_small if tier == "small" else self.settings.llm_model_strong
        model = configured or DEFAULT_MODELS.get(self.provider, {}).get(tier, "")
        if not model and self.provider in ("stub", "replay"):
            model = f"{self.provider}-{tier}"
        if not model:
            raise LLMDisabled(f"set LLM_MODEL_{tier.upper()} for provider {self.provider}")
        return model

    def complete_json(self, *, task: str, prompt_version: str, system: str, user: str, schema: dict,
                      tier: str = "small", max_tokens: int = MAX_TOKENS, cache_salt: str = "",
                      validate: Callable[[dict], None] | None = None) -> LLMResult:
        """Return JSON that validates against `schema` and passes `validate` (which raises LLMOutputError).
        A rejected answer is retried once with the reason. Only accepted answers are cached, keyed by provider,
        model, prompt version and input, so a bad answer can never be replayed."""
        if self.provider == "none":
            raise LLMDisabled("LLM_PROVIDER is 'none'")
        model = self.model_for(tier)
        key = cache_key(self.provider, model, prompt_version, system, user, schema, cache_salt)
        cached = self._cache_get(key)
        if cached is not None:
            self.cache_hits += 1
            return LLMResult(cached, self.provider, model, 0, 0, 0, True, key)
        if self.tokens_used >= self.settings.llm_token_budget:
            raise BudgetExceeded(f"token budget of {self.settings.llm_token_budget} reached after {self.calls} calls")

        started = time.monotonic()
        tokens_in = tokens_out = 0
        attempt_user, error = user, None
        for attempt in (1, 2):
            text, used_in, used_out = self._transport(model, system, attempt_user, schema, max_tokens)
            tokens_in += used_in
            tokens_out += used_out
            self.calls += 1
            try:
                data = json.loads(text)
                jsonschema.validate(data, schema)
                if validate is not None:
                    validate(data)
                break
            except (json.JSONDecodeError, jsonschema.ValidationError, LLMOutputError) as exc:
                error = str(exc).splitlines()[0][:300]
                log.warning("task=%s attempt=%s invalid output: %s", task, attempt, error)
                attempt_user = f"{user}\n\nYour previous answer was rejected: {error}\nReturn JSON that satisfies the schema exactly."
        else:
            self.tokens_used += tokens_in + tokens_out
            raise LLMOutputError(f"{task}: output failed validation twice: {error}")

        self.tokens_used += tokens_in + tokens_out
        latency = int((time.monotonic() - started) * 1000)
        self._cache_put(key, task, model, prompt_version, {"system": system, "user": user, "schema": schema}, data, tokens_in, tokens_out, latency)
        log.info("task=%s model=%s tokens_in=%s tokens_out=%s latency_ms=%s", task, model, tokens_in, tokens_out, latency)
        return LLMResult(data, self.provider, model, tokens_in, tokens_out, latency, False, key)

    # ------------------------------------------------------------------ cache
    def _cache_get(self, key: str) -> dict | None:
        if self.provider == "replay":
            path = Path(self.settings.replay_dir) / f"{key}.json"
            if not path.exists():
                raise LLMDisabled(f"replay: no recording for this prompt ({path.name}); record it with a live provider first")
            return json.loads(path.read_text(encoding="utf-8"))["response"]
        if self.conn is None:
            return None
        row = self.conn.execute("SELECT response FROM ops.agent_call WHERE cache_key = %s", (key,)).fetchone()
        return row[0] if row else None

    def _cache_put(self, key, task, model, prompt_version, request, response, tokens_in, tokens_out, latency) -> None:
        if self.conn is None:
            return
        self.conn.execute(
            "INSERT INTO ops.agent_call (cache_key, task, provider, model, prompt_version, request, response, input_tokens, output_tokens, latency_ms) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (cache_key) DO NOTHING",
            (key, task, self.provider, model, prompt_version, Jsonb(request), Jsonb(response), tokens_in, tokens_out, latency))
        self.conn.commit()

    # ------------------------------------------------------------------ providers
    def _pick_transport(self) -> Transport:
        return {"anthropic": self._anthropic, "openai_compat": self._openai_compat}.get(self.provider, self._unavailable)

    def _unavailable(self, *_args):
        raise LLMDisabled(f"provider '{self.provider}' makes no live calls")

    def _anthropic(self, model: str, system: str, user: str, schema: dict, max_tokens: int) -> tuple[str, int, int]:
        import anthropic

        client = anthropic.Anthropic(api_key=self.settings.llm_api_key or None, base_url=self.settings.llm_base_url or None)
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            # the instruction block is identical across calls of a task, so it is marked cacheable
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if response.stop_reason == "max_tokens":
            raise LLMOutputError(f"answer cut off at max_tokens={max_tokens}; lower CLASSIFY_BATCH_SIZE or raise the limit")
        text = next((block.text for block in response.content if block.type == "text"), "")
        usage = response.usage
        tokens_in = usage.input_tokens + (getattr(usage, "cache_creation_input_tokens", 0) or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        return text, tokens_in, usage.output_tokens

    def _openai_compat(self, model: str, system: str, user: str, schema: dict, max_tokens: int) -> tuple[str, int, int]:
        import openai

        client = openai.OpenAI(api_key=self.settings.llm_api_key or ("not-needed" if self.settings.llm_base_url else None),
                               base_url=self.settings.llm_base_url or None)
        request = dict(
            model=model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_schema", "json_schema": {"name": "result", "schema": schema, "strict": True}},
        )
        if self.settings.llm_temperature != "":
            request["temperature"] = float(self.settings.llm_temperature)
        try:
            response = client.chat.completions.create(**request)
        except openai.BadRequestError as exc:
            if "temperature" not in request or "temperature" not in str(exc).lower():
                raise
            request.pop("temperature")          # some models accept only their default sampling settings
            response = client.chat.completions.create(**request)
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise LLMOutputError("answer cut off by the provider's length limit; lower CLASSIFY_BATCH_SIZE")
        usage = response.usage
        return choice.message.content or "", getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0


def cache_key(provider: str, model: str, prompt_version: str, system: str, user: str, schema: dict, salt: str = "") -> str:
    payload = json.dumps([provider, model, prompt_version, system, user, schema, salt], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def estimate_cost(model: str, tokens_in: int, tokens_out: int, settings: Settings | None = None) -> float | None:
    """USD estimate from list prices, or None when the model's price is unknown."""
    import os

    price_in, price_out = os.getenv("LLM_PRICE_IN"), os.getenv("LLM_PRICE_OUT")
    if price_in and price_out:
        prices = (float(price_in), float(price_out))
    elif model in LIST_PRICES:
        prices = LIST_PRICES[model]
    else:
        return None
    return round(tokens_in / 1e6 * prices[0] + tokens_out / 1e6 * prices[1], 6)


def load_prompt(name: str) -> tuple[str, str]:
    """Prompts are versioned files. The first line is 'version: <id>'; the rest is the instruction text."""
    text = (Path(__file__).parent / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    first, _, body = text.partition("\n")
    if not first.startswith("version:"):
        raise ValueError(f"prompt {name} must start with a 'version:' line")
    return first.split(":", 1)[1].strip(), body.strip()
