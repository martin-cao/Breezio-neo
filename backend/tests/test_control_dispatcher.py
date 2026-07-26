import json
import unittest

from workers.control_dispatcher import dispatch_once


class FakeStore:
    def __init__(self):
        self.messages = [
            {
                "id": 1,
                "topic": "breezio/nodes/n1/command",
                "payload_json": {
                    "command_id": "cmd-1",
                    "target_node_id": "n1",
                    "target_device": "fan",
                    "action": "on",
                },
            }
        ]
        self.sent = []
        self.failed = []

    def list_pending_outbox_messages(self, limit=50):
        return self.messages[:limit]

    def mark_outbox_message_sent(self, message_id):
        self.sent.append(message_id)
        return {"id": message_id, "status": "sent"}

    def mark_outbox_message_failed(self, message_id, retry_delay_seconds=5):
        self.failed.append((message_id, retry_delay_seconds))
        return {"id": message_id, "status": "pending"}


class FakePublisher:
    def __init__(self, fail=False):
        self.fail = fail
        self.published = []

    def publish(self, topic, payload):
        if self.fail:
            raise RuntimeError("broker down")
        self.published.append((topic, json.loads(payload)))


class ControlDispatcherTest(unittest.TestCase):
    def test_dispatch_once_publishes_pending_outbox_and_marks_sent(self):
        store = FakeStore()
        publisher = FakePublisher()

        result = dispatch_once(store, publisher, retry_delay_seconds=9)

        self.assertEqual(result, {"sent": 1, "failed": 0})
        self.assertEqual(store.sent, [1])
        self.assertEqual(store.failed, [])
        self.assertEqual(publisher.published[0][0], "breezio/nodes/n1/command")
        self.assertEqual(publisher.published[0][1]["command_id"], "cmd-1")

    def test_dispatch_once_marks_failed_publish_for_retry(self):
        store = FakeStore()

        result = dispatch_once(store, FakePublisher(fail=True), retry_delay_seconds=9)

        self.assertEqual(result, {"sent": 0, "failed": 1})
        self.assertEqual(store.sent, [])
        self.assertEqual(store.failed, [(1, 9)])


if __name__ == "__main__":
    unittest.main()
