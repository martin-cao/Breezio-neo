import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from breezio_backend.chat import ChatToolbox, run_agent_turn
from breezio_backend.llm_client import LLMClient, LLMClientError


class ScriptedLLMHandler(BaseHTTPRequestHandler):
    """Serves a queued list of chat-completions responses, recording requests."""

    responses: list = []
    requests: list = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        type(self).requests.append(body)
        if not type(self).responses:
            self.send_response(500)
            self.end_headers()
            return
        status, payload = type(self).responses.pop(0)
        raw = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # silence test output
        pass


def message_response(message):
    return (200, {"choices": [{"message": message}]})


class LLMClientTest(unittest.TestCase):
    def setUp(self):
        ScriptedLLMHandler.responses = []
        ScriptedLLMHandler.requests = []
        self.server = HTTPServer(("127.0.0.1", 0), ScriptedLLMHandler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        self.client = LLMClient(self.base_url, "test-model", api_key="")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_plain_text_response(self):
        ScriptedLLMHandler.responses = [message_response({"role": "assistant", "content": "hello"})]
        message = self.client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(message["content"], "hello")
        self.assertEqual(ScriptedLLMHandler.requests[0]["model"], "test-model")

    def test_tool_call_response_passes_through(self):
        tool_call = {"id": "call_1", "type": "function", "function": {"name": "get_active_alerts", "arguments": "{}"}}
        ScriptedLLMHandler.responses = [message_response({"role": "assistant", "content": None, "tool_calls": [tool_call]})]
        message = self.client.chat([{"role": "user", "content": "alerts?"}], tools=[{"type": "function", "function": {"name": "get_active_alerts"}}])
        self.assertEqual(message["tool_calls"][0]["id"], "call_1")
        self.assertEqual(ScriptedLLMHandler.requests[0]["tool_choice"], "auto")

    def test_unreachable_endpoint_raises_typed_error(self):
        client = LLMClient("http://127.0.0.1:9", "m", api_key="")
        with self.assertRaises(LLMClientError):
            client.chat([{"role": "user", "content": "hi"}])

    def test_invalid_json_raises_typed_error(self):
        ScriptedLLMHandler.responses = [(200, b"not-json")]
        with self.assertRaises(LLMClientError):
            self.client.chat([{"role": "user", "content": "hi"}])

    def test_4xx_with_tools_falls_back_to_plain_chat(self):
        ScriptedLLMHandler.responses = [
            (400, {"error": "tools not supported"}),
            message_response({"role": "assistant", "content": "plain answer"}),
        ]
        message = self.client.chat([{"role": "user", "content": "hi"}], tools=[{"type": "function", "function": {"name": "x"}}])
        self.assertEqual(message["content"], "plain answer")
        self.assertNotIn("tools", ScriptedLLMHandler.requests[1])


class FakeStore:
    def latest_readings(self):
        return [{"node_id": "win-01", "metric": "temperature", "value": 28.4, "unit": "C"}]

    def list_alerts(self, status=None):
        return []

    def list_nodes(self, gateway_id=None, status=None):
        return [{"node_id": "win-01", "status": "online"}]

    def list_devices(self, status=None):
        return []

    def get_latest_prediction(self, node_id, metric, horizon_minutes=None):
        return {"node_id": node_id, "metric": metric, "horizon_minutes": horizon_minutes, "yhat": 28.1}


class ScriptedLLM:
    """Fake LLMClient: pops canned messages, records call transcripts."""

    def __init__(self, messages):
        self.messages = list(messages)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append([dict(m) for m in messages])
        if not self.messages:
            # Model that never terminates: keep emitting the same tool call.
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "loop", "type": "function", "function": {"name": "get_active_alerts", "arguments": "{}"}}]}
        return self.messages.pop(0)


class AgentLoopTest(unittest.TestCase):
    def setUp(self):
        self.toolbox = ChatToolbox(store=FakeStore(), vision_sink=None, inference=None)

    def test_tool_round_then_final_answer(self):
        llm = ScriptedLLM([
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_a", "type": "function", "function": {"name": "get_latest_readings", "arguments": "{}"}},
                {"id": "call_b", "type": "function", "function": {"name": "get_node_status", "arguments": "{}"}},
            ]},
            {"role": "assistant", "content": "It is 28.4C right now."},
        ])
        result = run_agent_turn(llm, self.toolbox, [], "what is the temperature?")
        self.assertEqual(result["content"], "It is 28.4C right now.")
        self.assertEqual(result["tool_calls_made"], ["get_latest_readings", "get_node_status"])
        # Second call must include one role:"tool" result per call, keyed by id.
        second = llm.calls[1]
        tool_messages = [m for m in second if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_messages], ["call_a", "call_b"])
        self.assertIn("28.4", tool_messages[0]["content"])

    def test_malformed_arguments_become_tool_error_not_crash(self):
        llm = ScriptedLLM([
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_bad", "type": "function", "function": {"name": "get_latest_readings", "arguments": "{not json"}},
            ]},
            {"role": "assistant", "content": "sorry, retried"},
        ])
        result = run_agent_turn(llm, self.toolbox, [], "hi")
        self.assertEqual(result["content"], "sorry, retried")
        tool_messages = [m for m in llm.calls[1] if m["role"] == "tool"]
        self.assertIn("invalid_arguments", tool_messages[0]["content"])

    def test_loop_cap_stops_non_terminating_model(self):
        llm = ScriptedLLM([])  # always emits tool_calls
        result = run_agent_turn(llm, self.toolbox, [], "hi")
        self.assertEqual(result["error"], "tool_round_limit")
        self.assertLessEqual(len(llm.calls), 9)

    def test_unknown_tool_returns_error_result(self):
        llm = ScriptedLLM([
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "not_a_tool", "arguments": "{}"}},
            ]},
            {"role": "assistant", "content": "done"},
        ])
        result = run_agent_turn(llm, self.toolbox, [], "hi")
        self.assertEqual(result["content"], "done")
        tool_messages = [m for m in llm.calls[1] if m["role"] == "tool"]
        self.assertIn("unknown_tool", tool_messages[0]["content"])


if __name__ == "__main__":
    unittest.main()
