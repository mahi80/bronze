"""The two provider adapters, driven through the real SDKs against a local mock HTTP server.

This checks the request each SDK sends and that a documented response shape is read correctly.
It is not a live call and says nothing about model behaviour."""
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from agents.llm import LLM, LLMOutputError, estimate_cost

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["mappings"],
          "properties": {"mappings": {"type": "array", "items": {"type": "string"}}}}
ANSWER = json.dumps({"mappings": ["ok"]})


class Mock(BaseHTTPRequestHandler):
    requests: list = []
    replies: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Mock.requests.append({"path": self.path, "body": body, "headers": {k.lower(): v for k, v in self.headers.items()}})
        status, payload = Mock.replies.pop(0)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture()
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    Mock.requests, Mock.replies = [], []
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def anthropic_message(text=ANSWER, stop_reason="end_turn"):
    return 200, {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                 "content": [{"type": "thinking", "thinking": "...", "signature": "s"}, {"type": "text", "text": text}],
                 "stop_reason": stop_reason, "stop_sequence": None,
                 "usage": {"input_tokens": 120, "output_tokens": 30, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 400}}


def openai_completion(text=ANSWER, finish_reason="stop"):
    return 200, {"id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "local-model",
                 "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish_reason}],
                 "usage": {"prompt_tokens": 90, "completion_tokens": 20, "total_tokens": 110}}


def test_anthropic_adapter_sends_structured_output_request(settings, server):
    live = replace(settings, llm_provider="anthropic", llm_base_url=server, llm_api_key="test-key", llm_model_small="")
    Mock.replies = [anthropic_message()]
    llm = LLM(live)
    result = llm.complete_json(task="t", prompt_version="v1", system="You label things.", user="<values>\n</values>", schema=SCHEMA)
    assert result.data == {"mappings": ["ok"]} and result.model == "claude-haiku-5-5"          # default small model
    assert (result.input_tokens, result.output_tokens) == (520, 30)                             # cached input tokens are counted
    request = Mock.requests[0]
    assert request["path"] == "/v1/messages"
    assert "test-key" in request["headers"].get("x-api-key", "") + request["headers"].get("authorization", "")
    body = request["body"]
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
    assert body["system"] == [{"type": "text", "text": "You label things.", "cache_control": {"type": "ephemeral"}}]
    assert body["messages"] == [{"role": "user", "content": "<values>\n</values>"}]
    assert body["max_tokens"] == 16000 and "temperature" not in body and "tools" not in body
    assert estimate_cost("claude-haiku-5-5", 1_000_000, 1_000_000) == 0.6

    Mock.replies = [anthropic_message(stop_reason="max_tokens")]
    with pytest.raises(LLMOutputError, match="cut off"):
        llm.complete_json(task="t", prompt_version="v1", system="s", user="another", schema=SCHEMA)
    assert llm.model_for("strong") == "claude-sonnet-5-5"


def test_openai_compatible_adapter_and_temperature_fallback(settings, server):
    live = replace(settings, llm_provider="openai_compat", llm_base_url=f"{server}/v1", llm_api_key="", llm_model_small="local-model", llm_temperature="0")
    Mock.replies = [openai_completion()]
    llm = LLM(live)
    result = llm.complete_json(task="t", prompt_version="v1", system="sys", user="usr", schema=SCHEMA)
    assert result.data == {"mappings": ["ok"]} and (result.input_tokens, result.output_tokens) == (90, 20)
    body = Mock.requests[0]["body"]
    assert Mock.requests[0]["path"] == "/v1/chat/completions"
    assert body["response_format"] == {"type": "json_schema", "json_schema": {"name": "result", "schema": SCHEMA, "strict": True}}
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]
    assert body["model"] == "local-model" and body["temperature"] == 0
    assert estimate_cost("local-model", 1000, 1000) is None          # no list price known: the report says n/a

    # a model that refuses the temperature parameter gets one retry without it
    Mock.replies = [(400, {"error": {"message": "Unsupported value: 'temperature' does not support 0", "type": "invalid_request_error"}}),
                    openai_completion()]
    llm.complete_json(task="t", prompt_version="v1", system="sys", user="second", schema=SCHEMA)
    assert "temperature" in Mock.requests[1]["body"] and "temperature" not in Mock.requests[2]["body"]


def test_invalid_model_output_is_retried_with_the_reason(settings, server):
    live = replace(settings, llm_provider="openai_compat", llm_base_url=f"{server}/v1", llm_model_small="local-model", llm_temperature="")
    Mock.replies = [openai_completion(text='{"mappings": "nope"}'), openai_completion()]
    result = LLM(live).complete_json(task="t", prompt_version="v1", system="sys", user="usr", schema=SCHEMA)
    assert result.data == {"mappings": ["ok"]} and result.input_tokens == 180           # both attempts are paid for and counted
    assert "previous answer was rejected" in Mock.requests[1]["body"]["messages"][1]["content"]
    assert "temperature" not in Mock.requests[0]["body"]
