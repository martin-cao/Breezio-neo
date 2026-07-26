import assert from "node:assert/strict";
import test from "node:test";

import {
  API_ROUTES,
  API_PREFIX,
  apiRequest,
  buildQuery,
  buildBackendUrl,
  createControlCommandPayload,
  createDashboardAlertItems,
  createDashboardCommandItems,
  createDashboardKpis,
  createEnvironmentZones,
  createAlertRecords,
  createInferencePredictPayload,
  createPredictionRows,
  createReadingBatchPayload,
  createTopologyNodes,
  createVisionTimelineItems,
  createVisionEventQuery,
  getConfiguredBackendBaseUrl,
  navItems,
  selectPreferredTopologyNode,
} from "../dist/api.js";

test("navigation exposes implemented and placeholder OpenAPI surfaces", () => {
  const ids = navItems.map((item) => item.id);
  assert.deepEqual(ids, [
    "dashboard",
    "devices",
    "history",
    "topology",
    "environment",
    "vision",
    "prediction",
    "alerts",
    "control",
    "models",
    "artifacts",
    "users",
  ]);
});

test("API routes match the consolidated OpenAPI contract", () => {
  assert.equal(API_ROUTES.authLogin, "/auth/login");
  assert.equal(API_ROUTES.authRegister, "/auth/register");
  assert.equal(API_ROUTES.health, "/health");
  assert.equal(API_ROUTES.summary, "/dashboard/summary");
  assert.equal(API_ROUTES.devices, "/devices");
  assert.equal(API_ROUTES.device(7), "/devices/7");
  assert.equal(API_ROUTES.deviceProbe(7), "/devices/7/probe");
  assert.equal(API_ROUTES.devicePreview(7), "/devices/7/preview.mjpg");
  assert.equal(API_ROUTES.deviceDelete(7), "/devices/7/hard");
  assert.equal(API_ROUTES.nodes, "/nodes");
  assert.equal(API_ROUTES.node("n1"), "/nodes/n1");
  assert.equal(API_ROUTES.readings, "/readings");
  assert.equal(API_ROUTES.readingsLatest, "/readings/latest");
  assert.equal(API_ROUTES.alerts, "/alerts");
  assert.equal(API_ROUTES.alertAck(3), "/alerts/3/ack");
  assert.equal(API_ROUTES.alertResolve(3), "/alerts/3/resolve");
  assert.equal(API_ROUTES.controlCommands, "/control/commands");
  assert.equal(API_ROUTES.controlCommand("cmd-1"), "/control/commands/cmd-1");
  assert.equal(API_ROUTES.controlAcks, "/control/acks");
  assert.equal(API_ROUTES.predictionLatest, "/prediction/latest");
  assert.equal(API_ROUTES.predictionMetrics, "/prediction/metrics");
  assert.equal(API_ROUTES.inferenceHealth, "/inference/health");
  assert.equal(API_ROUTES.inferencePredict, "/inference/predict");
  assert.equal(API_ROUTES.visionEvents, "/vision/events");
  assert.equal(API_ROUTES.models, "/models");
  assert.equal(API_ROUTES.modelActivate(2), "/models/2/activate");
  assert.equal(API_ROUTES.artifacts, "/artifacts");
  assert.equal(API_ROUTES.artifactPresign(5), "/artifacts/5/presign");
  assert.equal(API_ROUTES.users, "/users");
  assert.equal(API_ROUTES.user(4), "/users/4");
  assert.equal(API_ROUTES.me, "/me");
  assert.equal(API_ROUTES.mySettings, "/me/settings");
});

test("query builder keeps OpenAPI parameter names and skips empty values", () => {
  assert.equal(
    buildQuery({
      node_id: "n1",
      metric: "temperature",
      start: "",
      end: null,
      limit: 500,
    }),
    "?node_id=n1&metric=temperature&limit=500",
  );
});

test("backend URL builder prefixes the consolidated Flask API", () => {
  assert.equal(API_PREFIX, "/api");
  assert.equal(buildBackendUrl(API_ROUTES.models), "/api/models");
  assert.equal(
    buildBackendUrl(API_ROUTES.visionEvents, {
      baseUrl: "http://127.0.0.1:8000",
      query: { camera_id: "cam-01", task: "scene-classification", limit: 10 },
    }),
    "http://127.0.0.1:8000/api/vision/events?camera_id=cam-01&task=scene-classification&limit=10",
  );
});

test("backend base URL can be supplied for split frontend/backend dev servers", () => {
  assert.equal(getConfiguredBackendBaseUrl({ VITE_API_BASE_URL: "http://127.0.0.1:8000/" }), "http://127.0.0.1:8000");
  assert.equal(buildBackendUrl(API_ROUTES.artifactPresign(7), { baseUrl: "http://127.0.0.1:8000/" }), "http://127.0.0.1:8000/api/artifacts/7/presign");
});

test("control command payload uses OpenAPI field names and enums", () => {
  assert.deepEqual(
    createControlCommandPayload({
      nodeId: "n3",
      targetDevice: "lamp",
      action: "toggle",
      ttlSeconds: 15,
      reason: "manual dashboard test",
    }),
    {
      source: "manual",
      target_node_id: "n3",
      target_device: "lamp",
      action: "toggle",
      reason: "manual dashboard test",
      ttl_seconds: 15,
    },
  );
});

test("manual ingestion payload uses ReadingBatch schema", () => {
  const payload = createReadingBatchPayload({
    gatewayId: "gw-01",
    nodeId: "n1",
    seq: 42,
    temperature: 26.4,
    humidity: 58.2,
    light: 734,
  });
  assert.equal(payload.gateway_id, "gw-01");
  assert.equal(payload.records[0].node_id, "n1");
  assert.equal(payload.records[0].temperature, 26.4);
  assert.equal(payload.records[0].humidity, 58.2);
  assert.equal(payload.records[0].light, 734);
});

test("inference and vision helpers keep frontend behind backend API", () => {
  assert.deepEqual(
    createInferencePredictPayload({
      task: "scene-classification",
      cameraId: "cam-01",
      frameId: "frame-001",
      imageUri: "file:///tmp/frame.jpg",
    }),
    {
      task: "scene-classification",
      input: {
        camera_id: "cam-01",
        frame_id: "frame-001",
        image_uri: "file:///tmp/frame.jpg",
      },
    },
  );
  assert.equal(createVisionEventQuery({ cameraId: "cam-01", task: "scene-classification" }), "?camera_id=cam-01&task=scene-classification");
});

test("apiRequest accepts 204 no-content responses", async () => {
  const result = await apiRequest(
    API_ROUTES.user(4),
    { method: "DELETE" },
    async () => new Response(null, { status: 204 }),
  );

  assert.equal(result, undefined);
});

test("apiRequest attaches stored bearer token", async () => {
  const previousWindow = globalThis.window;
  const storage = new Map([["smart-lab-access-token", "token-123"]]);
  globalThis.window = {
    sessionStorage: { getItem: (key) => storage.get(key) || null },
    localStorage: { getItem: () => null },
  };
  try {
    await apiRequest(
      API_ROUTES.me,
      {},
      async (_url, init) => {
        assert.equal(init.headers.Authorization, "Bearer token-123");
        return new Response(JSON.stringify({ success: true, data: { id: 1 } }), { status: 200 });
      },
    );
  } finally {
    globalThis.window = previousWindow;
  }
});

test("dashboard KPI mapper derives values from backend summary and latest readings", () => {
  const items = createDashboardKpis({
    counts: { gateways: 1, nodes: 2, readings: 6, active_alerts: 1, pending_commands: 3 },
    latest: [
      { node_id: "n1", metric: "temperature", value: 28.0, unit: "C" },
      { node_id: "n2", metric: "temperature", value: 26.0, unit: "C" },
      { node_id: "n1", metric: "humidity", value: 12.0, unit: "%" },
      { node_id: "n2", metric: "light", value: 734, unit: "lux" },
    ],
  });

  assert.equal(items.find((item) => item.label === "Active Nodes")?.value, "2");
  assert.equal(items.find((item) => item.label === "Gateways")?.value, "1");
  assert.equal(items.find((item) => item.label === "Active Alerts")?.value, "1");
  assert.equal(items.find((item) => item.label === "Pending Commands")?.value, "3");
  assert.equal(items.find((item) => item.label === "Live Temp")?.value, "27.0");
  assert.equal(items.find((item) => item.label === "Live Humidity")?.value, "12.0");
  assert.equal(items.find((item) => item.label === "Live Light")?.value, "734");
});

test("dashboard alert mapper derives rows from backend alerts", () => {
  const items = createDashboardAlertItems([
    {
      id: 7,
      alert_type: "temperature",
      severity: "critical",
      status: "active",
      node_id: "n1",
      message: "High temperature",
      created_at: "2026-07-03T04:00:00Z",
      trigger_payload: { value: 34.2, unit: "C" },
    },
  ]);

  assert.deepEqual(items[0], {
    title: "High temperature",
    source: "n1",
    detail: "34.2 C",
    severity: "critical",
    status: "ACTIVE",
    ago: "Jul 3 at 04:00 AM",
  });
});

test("dashboard command mapper derives rows from backend control commands", () => {
  const items = createDashboardCommandItems([
    {
      command_id: "cmd-1",
      target_node_id: "n1",
      target_device: "lamp",
      action: "toggle",
      status: "acked",
      created_at: "2026-07-03T04:00:00Z",
    },
  ]);

  assert.deepEqual(items[0], {
    command_id: "cmd-1",
    node: "n1",
    device: "lamp",
    action: "toggle",
    state: "acked",
    ago: "Jul 3 at 04:00 AM",
  });
});

test("topology mapper derives nodes and readings from backend node state", () => {
  const items = createTopologyNodes(
    [
      { node_id: "router-1", gateway_id: "gw-1", role: "router", area: "Rack", rssi: -45, battery_mv: 3300, status: "online" },
      // Wi-Fi sensors registered with a DHCP-assigned ip_address flow through unchanged.
      { node_id: "n1", gateway_id: "gw-1", parent_node_id: "router-1", role: "sensor", area: "Bench", ip_address: "192.0.2.57", rssi: -60, battery_mv: 3000, status: "online" },
    ],
    [
      { node_id: "n1", metric: "temperature", value: 28.0 },
      { node_id: "n1", metric: "humidity", value: 12.0 },
      { node_id: "n1", metric: "light", value: 734 },
    ],
  );

  assert.equal(items[0].id, "router-1");
  assert.equal(items[0].type, "router");
  assert.equal(items[1].id, "n1");
  assert.equal(items[1].parent, "router-1");
  assert.equal(items[1].temp, 28.0);
  assert.equal(items[1].hum, 12.0);
  assert.equal(items[1].light, 734);
});

test("topology mapper keeps dense backend nodes readable", () => {
  const items = createTopologyNodes(
    Array.from({ length: 12 }, (_, index) => ({
      node_id: `contract-node-1782959719-${index}`,
      gateway_id: "gw-1",
      role: "sensor",
      status: "online",
    })),
  );

  assert.ok(new Set(items.map((item) => item.y)).size > 1);
  assert.ok(items.every((item) => item.label.length <= 14));
});

test("topology selection prefers fresh online backend nodes over stale offline samples", () => {
  const selected = selectPreferredTopologyNode(
    [
      { id: "contract-node-1782959719", label: "contract-node", name: "router", type: "router", zone: "Bench", status: "offline", batt: 0, rssi: 0, loss: 0, hb: 0, temp: 0, hum: 0, light: 0, x: 0, y: 0 },
      { id: "n-api", label: "n-api", name: "sensor", type: "terminal", zone: "-", status: "online", batt: 0, rssi: 0, loss: 0, hb: 90000, temp: 0, hum: 0, light: 0, x: 0, y: 0 },
      { id: "sim-router", label: "sim-router", name: "sensor", type: "terminal", zone: "-", status: "online", batt: 51, rssi: -63, loss: 0, hb: 45, temp: 26, hum: 47, light: 2, x: 0, y: 0 },
    ],
    "N05",
  );

  assert.equal(selected?.id, "sim-router");
});

test("topology selection prefers live hardware nodes over fresh simulated nodes", () => {
  const items = createTopologyNodes(
    [
      { node_id: "sim-lab-01", gateway_id: "gw-demo", role: "sensor", status: "online", last_seen_at: new Date().toISOString() },
      { node_id: "win-01", gateway_id: "gw-smartwin-01", role: "sensor", status: "online", last_seen_at: new Date().toISOString() },
    ],
    [
      { node_id: "sim-lab-01", metric: "temperature", value: 22.7, source: "simulated" },
      { node_id: "win-01", metric: "temperature", value: 28.0, source: "serial_hardware" },
    ],
  );

  const selected = selectPreferredTopologyNode(items);

  assert.equal(selected?.id, "win-01");
  assert.equal(selected?.source, "serial_hardware");
});

test("topology selection prefers serial hardware over older mqtt smoke nodes", () => {
  const selected = selectPreferredTopologyNode([
    { id: "smoke-doc-node", label: "smoke-doc", name: "sensor", type: "terminal", zone: "-", source: "mqtt", status: "online", batt: 0, rssi: -45, loss: 0, hb: 220000, temp: 23.7, hum: 48.5, light: 310, x: 0, y: 0 },
    { id: "win-01", label: "win-01", name: "sensor", type: "terminal", zone: "gw-smartwin-01", source: "serial_hardware", status: "online", batt: 0, rssi: 0, loss: 0, hb: 1200, temp: 28.5, hum: 50.6, light: 38, x: 0, y: 0 },
  ]);

  assert.equal(selected?.id, "win-01");
});

test("environment mapper derives zone stats from backend readings", () => {
  const zones = createEnvironmentZones(
    [
      { node_id: "n1", area: "Bench" },
      { node_id: "n2", area: "Window" },
    ],
    [
      { node_id: "n1", metric: "temperature", value: 28.0 },
      { node_id: "n1", metric: "temperature", value: 26.0 },
      { node_id: "n2", metric: "temperature", value: 20.0 },
    ],
    "temperature",
  );

  assert.deepEqual(
    zones.map((zone) => ({ zone: zone.zone, current: zone.current, min: zone.min, max: zone.max, avg: zone.avg })),
    [
      { zone: "Bench", current: 28, min: 26, max: 28, avg: 27 },
      { zone: "Window", current: 20, min: 20, max: 20, avg: 20 },
    ],
  );
});

test("alert center mapper derives rows from backend alerts", () => {
  const records = createAlertRecords([
    {
      id: 9,
      alert_type: "temperature",
      severity: "medium",
      status: "active",
      node_id: "n1",
      message: "Temperature high",
      created_at: "2026-07-03T04:00:00Z",
      trigger_payload: { rule: "temperature > 32", value: 34.2, unit: "C", note: "check fan" },
    },
  ]);

  assert.deepEqual(records[0], {
    id: "AL-9",
    sev: "warning",
    title: "Temperature high",
    source: "n1",
    rule: "temperature > 32",
    value: "34.2 C",
    status: "active",
    created: "Jul 3 at 04:00 AM",
    age: "Jul 3 at 04:00 AM",
    note: "check fan",
  });
});

test("vision mapper derives timeline items from persisted backend events", () => {
  const items = createVisionTimelineItems([
    {
      event_id: "vision-1",
      camera_id: "cam-01",
      task: "scene-classification",
      model_id: "scene-demo-v1",
      runtime: "dry-run",
      scene_label: "crowded",
      people_count: 3,
      confidence: 0.91,
      latency_ms: 18.5,
      captured_at: "2026-07-03T04:00:00Z",
      outputs: {
        detections: [{ label: "person", confidence: 0.87, box: [10, 20, 110, 220] }],
      },
    },
  ]);

  assert.deepEqual(items[0], {
    id: "vision-1",
    cameraId: "cam-01",
    frameId: "",
    task: "scene-classification",
    modelId: "scene-demo-v1",
    runtime: "dry-run",
    title: "scene-classification · crowded",
    detail: "people=3 · conf=0.91 · 18.5ms · dry-run",
    captured: "Jul 3 at 04:00 AM",
    peopleCount: 3,
    detections: [{ label: "person", confidence: 0.87, box: [10, 20, 110, 220] }],
  });
});

test("prediction mapper derives model rows from backend metrics", () => {
  const rows = createPredictionRows([
    {
      node_id: "n1",
      metric: "temperature",
      model_name: "Random Forest",
      mae: 0.91,
      rmse: 1.34,
      r2: 0.93,
      inference_latency_ms: 12.4,
      is_active: true,
    },
  ]);

  assert.deepEqual(rows[0], {
    id: "n1-temperature-Random Forest",
    name: "Random Forest",
    type: "temperature · n1",
    nodeId: "n1",
    metricKey: "temperature",
    modelName: "Random Forest",
    mae: "0.91",
    rmse: "1.34",
    r2: "0.93",
    lat: "12.4ms",
    color: "#129d6b",
    active: true,
  });
});
