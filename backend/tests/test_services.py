import sqlite3
import unittest

from breezio_backend.db import init_db
from breezio_backend import services
from breezio_backend.services import BackendStore


class BackendServicesTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def test_ingest_batch_expands_metrics_and_updates_node(self):
        result = services.ingest_telemetry_batch(
            self.conn,
            {
                "message_id": "msg-1",
                "gateway_id": "gw-01",
                "batch_id": "1042",
                "captured_at": services.now_iso(),
                "records": [
                    {
                        "node_id": "n1",
                        "seq": 1,
                        "temperature": 26.4,
                        "humidity": 58.2,
                        "light": 734,
                        "battery_mv": 3120,
                        "rssi": -67,
                        "flags": 0,
                    }
                ],
            },
        )
        self.assertEqual(result["inserted_metrics"], 3)
        latest = services.latest_readings(self.conn)
        self.assertEqual({item["metric"] for item in latest}, {"temperature", "humidity", "light"})
        nodes = services.list_nodes(self.conn)
        self.assertEqual(nodes[0]["node_id"], "n1")
        self.assertEqual(nodes[0]["status"], "online")

    def test_ingest_batch_preserves_router_parent_relationship(self):
        services.ingest_telemetry_batch(
            self.conn,
            {
                "message_id": "msg-router",
                "gateway_id": "gw-01",
                "batch_id": "router-1",
                "records": [
                    {"node_id": "n1", "seq": 1, "temperature": 26.4, "role": "router"},
                    {
                        "node_id": "n2",
                        "seq": 2,
                        "light": 734,
                        "role": "sensor",
                        "parent_node_id": "n1",
                        "source": "serial_router",
                    },
                ],
            },
        )

        nodes = {item["node_id"]: item for item in services.list_nodes(self.conn)}
        self.assertEqual(nodes["n1"]["role"], "router")
        self.assertIsNone(nodes["n1"]["parent_node_id"])
        self.assertEqual(nodes["n2"]["role"], "sensor")
        self.assertEqual(nodes["n2"]["parent_node_id"], "n1")

    def test_stale_telemetry_node_is_reported_offline(self):
        services.ingest_telemetry_batch(
            self.conn,
            {
                "message_id": "msg-stale",
                "gateway_id": "gw-01",
                "batch_id": "stale-1",
                "captured_at": "2000-01-01T00:00:00+00:00",
                "records": [{"node_id": "n-stale", "seq": 1, "temperature": 26.4}],
            },
        )

        nodes = services.list_nodes(self.conn)
        self.assertEqual(nodes[0]["node_id"], "n-stale")
        self.assertEqual(nodes[0]["status"], "offline")
        self.assertEqual(services.list_nodes(self.conn, status="online"), [])

    def test_store_wraps_service_operations(self):
        store = BackendStore(self.conn)
        store.ingest_telemetry_batch(
            {
                "message_id": "msg-store",
                "gateway_id": "gw-01",
                "batch_id": "store",
                "records": [{"node_id": "n1", "seq": 2, "temperature": 27.1}],
            }
        )
        self.assertEqual(store.latest_readings()[0]["metric"], "temperature")

    def test_command_outbox_and_ack(self):
        command = services.create_control_command(
            self.conn,
            {
                "source": "manual",
                "target_node_id": "n1",
                "target_device": "fan",
                "action": "on",
                "reason": "test",
            },
        )
        self.assertEqual(command["status"], "pending")
        acked = services.ingest_command_ack(
            self.conn,
            {
                "command_id": command["command_id"],
                "node_id": "n1",
                "result": "OK",
                "state": "ON",
            },
        )
        self.assertEqual(acked["status"], "acked")
        self.assertEqual(len(acked["acks"]), 1)


class AlertRulesTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def _ingest(self, temperature: float) -> dict:
        return services.ingest_telemetry_batch(
            self.conn,
            {
                "gateway_id": "gw1",
                "batch_id": "b1",
                "records": [{"node_id": "n1", "seq": 1, "temperature": temperature}],
            },
        )

    def test_rule_crud(self):
        rule = services.create_alert_rule(
            self.conn,
            {"name": "hot", "metric": "temperature", "condition": "gt", "threshold": 30},
        )
        self.assertEqual(rule["severity"], "medium")
        self.assertEqual(rule["enabled"], 1)
        rules = services.list_alert_rules(self.conn)
        self.assertEqual(len(rules), 1)
        updated = services.update_alert_rule(self.conn, rule["id"], {"threshold": 35, "severity": "high"})
        self.assertEqual(updated["threshold"], 35)
        self.assertEqual(updated["severity"], "high")
        self.assertTrue(services.delete_alert_rule(self.conn, rule["id"]))
        self.assertFalse(services.delete_alert_rule(self.conn, rule["id"]))

    def test_rule_validation(self):
        with self.assertRaises(ValueError):
            services.create_alert_rule(self.conn, {"name": "x", "metric": "bogus", "condition": "gt", "threshold": 1})
        with self.assertRaises(ValueError):
            services.create_alert_rule(self.conn, {"name": "x", "metric": "temperature", "condition": "between", "threshold": 1})
        with self.assertRaises(ValueError):
            services.create_alert_rule(self.conn, {"name": "x", "metric": "temperature", "condition": "gt"})

    def test_rule_fires_alert_on_ingest_with_cooldown(self):
        services.create_alert_rule(
            self.conn,
            {"name": "hot", "metric": "temperature", "condition": "gt", "threshold": 30, "cooldown_seconds": 600},
        )
        result = self._ingest(31.5)
        self.assertEqual(result["alerts_fired"], 1)
        alerts = services.list_alerts(self.conn, "active")
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["alert_type"], "rule:temperature")
        # within cooldown: same breach does not fire again
        result = self._ingest(32.0)
        self.assertEqual(result["alerts_fired"], 0)
        self.assertEqual(len(services.list_alerts(self.conn, "active")), 1)
        # below threshold never fires
        result = self._ingest(29.0)
        self.assertEqual(result["alerts_fired"], 0)

    def test_rule_scoping_and_disable(self):
        services.create_alert_rule(
            self.conn,
            {"name": "other-node", "metric": "temperature", "condition": "gt", "threshold": 30, "node_id": "n2"},
        )
        self.assertEqual(self._ingest(35.0)["alerts_fired"], 0)
        rule = services.create_alert_rule(
            self.conn,
            {"name": "disabled", "metric": "temperature", "condition": "gt", "threshold": 30, "enabled": False},
        )
        self.assertEqual(self._ingest(36.0)["alerts_fired"], 0)
        services.update_alert_rule(self.conn, rule["id"], {"enabled": True})
        self.assertEqual(self._ingest(37.0)["alerts_fired"], 1)


if __name__ == "__main__":
    unittest.main()
