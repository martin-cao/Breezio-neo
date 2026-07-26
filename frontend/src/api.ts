export type RouteFactory = (id: string | number) => string;

export type ApiRoutes = {
  authLogin: string;
  authRegister: string;
  health: string;
  summary: string;
  devices: string;
  device: RouteFactory;
  deviceProbe: RouteFactory;
  devicePreview: RouteFactory;
  deviceCaption: RouteFactory;
  deviceDelete: RouteFactory;
  gateways: string;
  nodes: string;
  node: RouteFactory;
  readings: string;
  readingsLatest: string;
  readingsStream: string;
  alerts: string;
  alertAck: RouteFactory;
  alertResolve: RouteFactory;
  alertRules: string;
  alertRule: RouteFactory;
  controlCommands: string;
  controlCommand: RouteFactory;
  controlAcks: string;
  predictionRuns: string;
  predictionRunActivate: RouteFactory;
  predictionLatest: string;
  predictionMetrics: string;
  predictionFeatures: string;
  inferenceHealth: string;
  inferencePredict: string;
  visionEvents: string;
  models: string;
  modelActivate: RouteFactory;
  artifacts: string;
  artifactPresign: RouteFactory;
  users: string;
  user: RouteFactory;
  me: string;
  mySettings: string;
  chatSessions: string;
  chatMessages: RouteFactory;
};

export type NavItem = {
  id: string;
  label: string;
  badge?: string;
};

export type QueryValue = string | number | boolean | null | undefined;

export const API_PREFIX = "/api";

export type BackendUrlOptions = {
  baseUrl?: string;
  query?: Record<string, QueryValue>;
};

type RuntimeConfig = {
  VITE_API_BASE_URL?: string;
  BREEZIO_API_BASE_URL?: string;
};

export type InferenceTask = "scene-classification" | "person-detection" | "sensor-forecast" | "face-identification";

export type InferencePredictPayloadInput = {
  task: InferenceTask;
  cameraId?: string;
  frameId?: string;
  imageUri?: string;
  horizon?: number;
  metric?: string;
};

export type VisionEventQueryInput = {
  cameraId?: string;
  task?: InferenceTask;
  limit?: number;
};

export type ControlCommandPayloadInput = {
  nodeId: string;
  targetDevice: "fan" | "lamp" | "buzzer" | "window";
  action: "on" | "off" | "open" | "close" | "toggle";
  ttlSeconds?: number;
  reason?: string;
};

export type ReadingBatchPayloadInput = {
  gatewayId: string;
  nodeId: string;
  seq?: number;
  temperature: number;
  humidity: number;
  light: number;
};

export type DashboardReading = {
  node_id?: string;
  metric?: "temperature" | "humidity" | "light" | string;
  value?: number | string | null;
  unit?: string;
  source?: string;
  captured_at?: string;
};

export type DashboardSummary = {
  counts?: {
    gateways?: number | string | null;
    nodes?: number | string | null;
    readings?: number | string | null;
    active_alerts?: number | string | null;
    pending_commands?: number | string | null;
  };
  latest?: DashboardReading[];
};

export type DashboardKpi = {
  label: string;
  value: string;
  unit: string;
  delta: string;
  deltaColor: string;
  dot: string;
  data: number[];
  color: string;
  fill: string;
};

export type DashboardTrendFallback = Partial<Record<"temperature" | "humidity" | "light", number[]>>;

export type DashboardAlertBackend = {
  id?: number | string;
  alert_type?: string;
  severity?: string;
  status?: string;
  node_id?: string | null;
  camera_id?: string | null;
  message?: string;
  created_at?: string;
  trigger_payload?: Record<string, unknown> | string | null;
};

export type DashboardAlertItem = {
  title: string;
  source: string;
  detail: string;
  severity: "critical" | "warning" | "info";
  status: string;
  ago: string;
};

export type DashboardCommandBackend = {
  command_id?: string;
  target_node_id?: string;
  target_device?: string;
  action?: string;
  status?: string;
  created_at?: string;
};

export type DashboardCommandItem = {
  command_id?: string;
  node: string;
  action: string;
  device: string;
  state: "acked" | "sent" | "pending" | "failed" | "timeout";
  ago: string;
};

export type BackendNodeStatus = "online" | "offline" | "fault";

export type BackendNodeForUi = {
  node_id?: string;
  gateway_id?: string | null;
  parent_node_id?: string | null;
  role?: "sink" | "router" | "sensor" | "actuator";
  area?: string | null;
  ip_address?: string | null;
  battery_mv?: number | string | null;
  rssi?: number | string | null;
  packet_loss?: number | string | null;
  last_seen_at?: string | null;
  status?: BackendNodeStatus;
};

export type TopologyNodeItem = {
  id: string;
  label: string;
  name: string;
  type: "sink" | "router" | "terminal";
  zone: string;
  source?: string;
  parent?: string;
  status: BackendNodeStatus;
  batt: number;
  rssi: number;
  loss: number;
  hb: number;
  temp: number;
  hum: number;
  light: number;
  x: number;
  y: number;
};

export type EnvironmentZoneItem = {
  zone: string;
  status: BackendNodeStatus;
  current: number;
  min: number;
  max: number;
  avg: number;
  values: number[];
};

export type AlertRecordItem = {
  id: string;
  sev: "critical" | "warning" | "info";
  title: string;
  source: string;
  rule: string;
  value: string;
  status: "active" | "ack" | "closed";
  created: string;
  age: string;
  note: string;
};

export type VisionEventBackend = {
  event_id?: string;
  camera_id?: string | null;
  frame_id?: string | null;
  task?: string;
  model_id?: string | null;
  runtime?: string | null;
  scene_label?: string | null;
  people_count?: number | string | null;
  confidence?: number | string | null;
  latency_ms?: number | string | null;
  captured_at?: string;
  outputs?: {
    detections?: VisionDetectionBackend[];
    [key: string]: unknown;
  } | null;
};

export type VisionDetectionBackend = {
  label?: string;
  confidence?: number | string | null;
  box?: Array<number | string | null>;
};

export type VisionDetectionBox = {
  label: string;
  confidence: number;
  box: [number, number, number, number];
};

export type VisionTimelineItem = {
  id: string;
  cameraId: string;
  frameId: string;
  task: string;
  modelId: string;
  runtime: string;
  title: string;
  detail: string;
  captured: string;
  peopleCount: number;
  detections: VisionDetectionBox[];
};

export type PredictionMetricBackend = {
  node_id?: string;
  metric?: string;
  model_name?: string;
  mae?: number | string | null;
  rmse?: number | string | null;
  r2?: number | string | null;
  inference_latency_ms?: number | string | null;
  is_active?: boolean;
};

export type PredictionRowItem = {
  id: string;
  name: string;
  type: string;
  nodeId?: string;
  metricKey?: string;
  modelName?: string;
  mae: string;
  rmse: string;
  r2: string;
  lat: string;
  color: string;
  active?: boolean;
};

export const API_ROUTES: ApiRoutes = {
  authLogin: "/auth/login",
  authRegister: "/auth/register",
  health: "/health",
  summary: "/dashboard/summary",
  devices: "/devices",
  device: (deviceId) => `/devices/${encodeURIComponent(deviceId)}`,
  deviceProbe: (deviceId) => `/devices/${encodeURIComponent(deviceId)}/probe`,
  devicePreview: (deviceId) => `/devices/${encodeURIComponent(deviceId)}/preview.mjpg`,
  deviceCaption: (deviceId) => `/devices/${encodeURIComponent(deviceId)}/caption`,
  deviceDelete: (deviceId) => `/devices/${encodeURIComponent(deviceId)}/hard`,
  gateways: "/gateways",
  nodes: "/nodes",
  node: (nodeId) => `/nodes/${encodeURIComponent(nodeId)}`,
  readings: "/readings",
  readingsLatest: "/readings/latest",
  readingsStream: "/stream/readings",
  alerts: "/alerts",
  alertAck: (alertId) => `/alerts/${encodeURIComponent(alertId)}/ack`,
  alertResolve: (alertId) => `/alerts/${encodeURIComponent(alertId)}/resolve`,
  alertRules: "/alert-rules",
  alertRule: (ruleId) => `/alert-rules/${encodeURIComponent(ruleId)}`,
  controlCommands: "/control/commands",
  controlCommand: (commandId) => `/control/commands/${encodeURIComponent(commandId)}`,
  controlAcks: "/control/acks",
  predictionRuns: "/prediction/runs",
  predictionRunActivate: (runId) => `/prediction/runs/${encodeURIComponent(runId)}/activate`,
  predictionLatest: "/prediction/latest",
  predictionMetrics: "/prediction/metrics",
  predictionFeatures: "/prediction/features",
  inferenceHealth: "/inference/health",
  inferencePredict: "/inference/predict",
  visionEvents: "/vision/events",
  models: "/models",
  modelActivate: (modelId) => `/models/${encodeURIComponent(modelId)}/activate`,
  artifacts: "/artifacts",
  artifactPresign: (artifactId) => `/artifacts/${encodeURIComponent(artifactId)}/presign`,
  users: "/users",
  user: (userId) => `/users/${encodeURIComponent(userId)}`,
  me: "/me",
  mySettings: "/me/settings",
  chatSessions: "/chat/sessions",
  chatMessages: (sessionId) => `/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
};

export const navItems: NavItem[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "devices", label: "Devices", badge: "empty" },
  { id: "history", label: "History", badge: "empty" },
  { id: "topology", label: "Sensor Network" },
  { id: "environment", label: "Environment" },
  { id: "vision", label: "Vision Monitoring" },
  { id: "prediction", label: "Prediction Center" },
  { id: "alerts", label: "Alert Center" },
  { id: "control", label: "Remote Control" },
  { id: "models", label: "Model Selection" },
  { id: "artifacts", label: "Artifacts", badge: "empty" },
  { id: "users", label: "User Management" },
];

export function buildQuery(params: Record<string, QueryValue>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    query.set(key, String(value));
  }
  const text = query.toString();
  return text ? `?${text}` : "";
}

export function getConfiguredBackendBaseUrl(env?: RuntimeConfig): string {
  const explicit = env?.VITE_API_BASE_URL || env?.BREEZIO_API_BASE_URL;
  if (explicit) return explicit.replace(/\/$/, "");

  const runtime = globalThis as typeof globalThis & {
    BREEZIO_API_BASE_URL?: string;
    window?: Window & typeof globalThis;
    document?: Document;
  };
  if (runtime.BREEZIO_API_BASE_URL) return runtime.BREEZIO_API_BASE_URL.replace(/\/$/, "");

  try {
    const stored = runtime.window?.localStorage?.getItem("BREEZIO_API_BASE_URL");
    if (stored) return stored.replace(/\/$/, "");
  } catch {
    // localStorage may be unavailable in restricted browser contexts.
  }

  const meta = runtime.document?.querySelector<HTMLMetaElement>("meta[name='breezio-api-base-url']")?.content;
  return meta ? meta.replace(/\/$/, "") : "";
}

export function buildBackendUrl(route: string, options: BackendUrlOptions = {}): string {
  const prefix = `${API_PREFIX}${route.startsWith("/") ? route : `/${route}`}`;
  const base = options.baseUrl ? options.baseUrl.replace(/\/$/, "") : getConfiguredBackendBaseUrl();
  return `${base}${prefix}${buildQuery(options.query || {})}`;
}

export async function apiRequest<T>(
  route: string,
  options: RequestInit & BackendUrlOptions = {},
  fetcher: typeof fetch = fetch,
): Promise<T> {
  const { baseUrl, query, headers, ...requestOptions } = options;
  let authHeader: Record<string, string> = {};
  try {
    const token = globalThis.window?.sessionStorage?.getItem("smart-lab-access-token") || globalThis.window?.localStorage?.getItem("smart-lab-access-token");
    if (token) authHeader = { Authorization: `Bearer ${token}` };
  } catch {
    // localStorage may be unavailable in restricted browser contexts.
  }
  const response = await fetcher(buildBackendUrl(route, { baseUrl, query }), {
    ...requestOptions,
    headers: {
      Accept: "application/json",
      ...(requestOptions.body ? { "Content-Type": "application/json" } : {}),
      ...authHeader,
      ...(headers || {}),
    },
  });
  if (response.status === 204) return undefined as T;
  const payload = await response.json();
  if (!response.ok || payload.success === false) {
    throw new Error(payload.message || payload.error || `request failed: ${response.status}`);
  }
  return payload.data as T;
}

export function createControlCommandPayload(input: ControlCommandPayloadInput) {
  return {
    source: "manual",
    target_node_id: input.nodeId,
    target_device: input.targetDevice,
    action: input.action,
    reason: input.reason,
    ttl_seconds: Number(input.ttlSeconds || 10),
  };
}

export function createReadingBatchPayload(input: ReadingBatchPayloadInput) {
  return {
    gateway_id: input.gatewayId,
    captured_at: new Date().toISOString(),
    records: [
      {
        node_id: input.nodeId,
        seq: Number(input.seq || 1),
        temperature: Number(input.temperature),
        humidity: Number(input.humidity),
        light: Number(input.light),
      },
    ],
  };
}

export function createDashboardKpis(summary: DashboardSummary, fallback: DashboardTrendFallback = {}): DashboardKpi[] {
  const counts = summary.counts || {};
  // Only average currently-reporting nodes: a node that stopped reporting
  // (e.g. a leftover demo node) must not drag the live numbers around.
  const latest = freshReadings(summary.latest || []);
  const activeNodes = toFiniteNumber(counts.nodes);
  const readings = toFiniteNumber(counts.readings);
  const gateways = toFiniteNumber(counts.gateways);
  const alerts = toFiniteNumber(counts.active_alerts);
  const pendingCommands = toFiniteNumber(counts.pending_commands);
  const tempAvg = averageMetric(latest, "temperature");
  const humidityAvg = averageMetric(latest, "humidity");
  const lightAvg = averageMetric(latest, "light");

  return [
    {
      label: "Active Nodes",
      value: String(activeNodes),
      unit: "nodes",
      delta: `${readings} readings`,
      deltaColor: "#129d6b",
      dot: "#129d6b",
      data: trendFor(latest, "temperature", fallback.temperature, activeNodes),
      color: "#129d6b",
      fill: "rgba(18,157,107,.08)",
    },
    {
      label: "Gateways",
      value: String(gateways),
      unit: "online",
      delta: "hardware bridge",
      deltaColor: "#129d6b",
      dot: "#129d6b",
      data: [gateways || 0, gateways || 0, gateways || 0],
      color: "#129d6b",
      fill: "rgba(18,157,107,.08)",
    },
    {
      label: "Active Alerts",
      value: String(alerts),
      unit: "",
      delta: alerts > 0 ? "requires attention" : "clear",
      deltaColor: alerts > 0 ? "#dc4444" : "#129d6b",
      dot: alerts > 0 ? "#dc4444" : "#129d6b",
      data: [0, alerts],
      color: alerts > 0 ? "#dc4444" : "#129d6b",
      fill: alerts > 0 ? "rgba(220,68,68,.08)" : "rgba(18,157,107,.08)",
    },
    {
      label: "Pending Commands",
      value: String(pendingCommands),
      unit: "",
      delta: pendingCommands > 0 ? "awaiting ACK" : "no pending outbox",
      deltaColor: pendingCommands > 0 ? "#e08206" : "#129d6b",
      dot: pendingCommands > 0 ? "#e08206" : "#129d6b",
      data: [0, pendingCommands],
      color: "#e08206",
      fill: "rgba(224,130,6,.08)",
    },
    {
      label: "Live Temp",
      value: formatMetricValue(tempAvg, 1),
      unit: "C",
      delta: latestDelta(latest, "temperature"),
      deltaColor: "#8c95a1",
      dot: "#e08206",
      data: trendFor(latest, "temperature", fallback.temperature, tempAvg),
      color: "#e08206",
      fill: "rgba(224,130,6,.08)",
    },
    {
      label: "Live Humidity",
      value: formatMetricValue(humidityAvg, 1),
      unit: "%",
      delta: latestDelta(latest, "humidity"),
      deltaColor: "#8c95a1",
      dot: "#3b5bdb",
      data: trendFor(latest, "humidity", fallback.humidity, humidityAvg),
      color: "#3b5bdb",
      fill: "rgba(59,91,219,.08)",
    },
    {
      label: "Live Light",
      value: formatMetricValue(lightAvg, 0),
      // Raw photoresistor index (0-100, lower = brighter) — not calibrated lux.
      unit: "",
      delta: latestDelta(latest, "light"),
      deltaColor: "#8c95a1",
      dot: "#0ea5b7",
      data: trendFor(latest, "light", fallback.light, lightAvg),
      color: "#0ea5b7",
      fill: "rgba(14,165,183,.08)",
    },
  ];
}

export function createDashboardAlertItems(alerts: DashboardAlertBackend[]): DashboardAlertItem[] {
  return alerts.map((alert) => {
    const trigger = parseTriggerPayload(alert.trigger_payload);
    return {
      title: alert.message || alert.alert_type || "Alert",
      source: alert.node_id || alert.camera_id || "-",
      detail: triggerDetail(trigger),
      severity: normalizeAlertSeverity(alert.severity),
      status: String(alert.status || "active").toUpperCase(),
      ago: formatApiDate(alert.created_at),
    };
  });
}

export function createDashboardCommandItems(commands: DashboardCommandBackend[]): DashboardCommandItem[] {
  return commands.map((command) => ({
    command_id: command.command_id,
    node: command.target_node_id || "-",
    device: command.target_device || "-",
    action: command.action || "-",
    state: normalizeCommandState(command.status),
    ago: formatApiDate(command.created_at),
  }));
}

export function createTopologyNodes(nodes: BackendNodeForUi[], readings: DashboardReading[] = []): TopologyNodeItem[] {
  const sorted = [...nodes].sort((a, b) => roleRank(a.role) - roleRank(b.role) || String(a.node_id || "").localeCompare(String(b.node_id || "")));
  const counts = {
    sink: Math.max(1, sorted.filter((node) => node.role === "sink").length),
    router: Math.max(1, sorted.filter((node) => node.role === "router").length),
    terminal: Math.max(1, sorted.filter((node) => node.role !== "sink" && node.role !== "router").length),
  };
  const seen = { sink: 0, router: 0, terminal: 0 };
  return sorted.map((node) => {
    const type = topologyType(node.role);
    const laneIndex = seen[type]++;
    const point = topologyPoint(type, laneIndex, counts[type]);
    const nodeId = node.node_id || "-";
    return {
      id: nodeId,
      label: compactTopologyLabel(nodeId),
      name: node.role || "node",
      type,
      zone: node.area || node.gateway_id || "-",
      source: latestSourceForNode(readings, nodeId),
      parent: node.parent_node_id || undefined,
      status: node.status || "offline",
      batt: batteryPercent(node.battery_mv, type),
      rssi: toFiniteNumber(node.rssi),
      loss: toFiniteNumber(node.packet_loss),
      hb: heartbeatSeconds(node.last_seen_at),
      temp: latestMetricForNode(readings, nodeId, "temperature"),
      hum: latestMetricForNode(readings, nodeId, "humidity"),
      light: latestMetricForNode(readings, nodeId, "light"),
      x: point.x,
      y: point.y,
    };
  });
}

export function selectPreferredTopologyNode(nodes: TopologyNodeItem[], currentId?: string): TopologyNodeItem | undefined {
  if (currentId) {
    const current = nodes.find((node) => node.id === currentId);
    if (current) return current;
  }
  const freshOnline = nodes.filter((node) => node.status === "online" && node.hb <= 300);
  const online = nodes.filter((node) => node.status === "online");
  return (
    freshOnline.find(isSerialHardwareTopologyNode) ||
    online.find(isSerialHardwareTopologyNode) ||
    freshOnline.find(isMqttTopologyNode) ||
    freshOnline[0] ||
    online.find(isMqttTopologyNode) ||
    online[0] ||
    nodes[0]
  );
}

export function createEnvironmentZones(nodes: BackendNodeForUi[], readings: DashboardReading[], metric: "temperature" | "humidity" | "light"): EnvironmentZoneItem[] {
  const areaByNode = new Map(nodes.map((node) => [node.node_id || "-", node.area || node.node_id || "-"]));
  const groups = new Map<string, number[]>();
  for (const reading of readings) {
    if (reading.metric !== metric || !reading.node_id) continue;
    const zone = areaByNode.get(reading.node_id) || reading.node_id;
    const values = groups.get(zone) || [];
    values.push(toFiniteNumber(reading.value));
    groups.set(zone, values);
  }
  return [...groups.entries()].map(([zone, values]) => {
    const min = Math.min(...values);
    const max = Math.max(...values);
    const avg = values.reduce((total, value) => total + value, 0) / values.length;
    const current = values[0] ?? 0;
    return {
      zone,
      status: metric === "temperature" && current >= 32 ? "fault" : "online",
      current,
      min,
      max,
      avg,
      values,
    };
  });
}

export function createAlertRecords(alerts: DashboardAlertBackend[]): AlertRecordItem[] {
  return alerts.map((alert) => {
    const trigger = parseTriggerPayload(alert.trigger_payload);
    const created = formatApiDate(alert.created_at);
    return {
      id: `AL-${alert.id ?? "-"}`,
      sev: normalizeAlertSeverity(alert.severity),
      title: alert.message || alert.alert_type || "Alert",
      source: alert.node_id || alert.camera_id || "-",
      rule: String(trigger.rule || alert.alert_type || "-"),
      value: triggerDetail(trigger),
      status: normalizeAlertStatus(alert.status),
      created,
      age: created,
      note: String(trigger.note || trigger.message || alert.message || "-"),
    };
  });
}

export function createVisionTimelineItems(events: VisionEventBackend[]): VisionTimelineItem[] {
  return events.map((event, index) => {
    const people = toFiniteNumber(event.people_count);
    const confidence = toFiniteNumber(event.confidence);
    const latency = toFiniteNumber(event.latency_ms);
    const detections = Array.isArray(event.outputs?.detections)
      ? event.outputs.detections.map(normalizeVisionDetection).filter((item): item is VisionDetectionBox => item !== null)
      : [];
    const task = event.task || "vision-event";
    // Text-producing tasks carry their signal in scene_summary; the
    // people/conf line only makes sense for detection-shaped events.
    const isTextEvent = task === "scene-caption" || task === "anomaly-detection";
    const summary = typeof event.outputs?.scene_summary === "string" ? event.outputs.scene_summary : "";
    const anomalyLabel = typeof event.outputs?.anomaly_label === "string" ? event.outputs.anomaly_label : "";
    const reason = typeof event.outputs?.reason === "string" ? event.outputs.reason : "";
    const detail = isTextEvent
      ? `${summary || reason || "no summary"} · ${latency ? `${latency}ms` : "-"} · ${event.runtime || "runtime?"}`
      : `people=${people} · conf=${confidence.toFixed(2)} · ${latency ? `${latency}ms` : "-"} · ${event.runtime || "runtime?"}`;
    return {
      id: event.event_id || `vision-${index}`,
      cameraId: event.camera_id || "",
      frameId: event.frame_id || "",
      task,
      modelId: event.model_id || "",
      runtime: event.runtime || "",
      title: `${task} · ${event.scene_label || anomalyLabel || event.camera_id || "-"}`,
      detail,
      captured: formatApiDate(event.captured_at),
      peopleCount: people,
      detections,
    };
  });
}

function normalizeVisionDetection(item: VisionDetectionBackend): VisionDetectionBox | null {
  if (!Array.isArray(item.box) || item.box.length < 4) return null;
  const box = item.box.slice(0, 4).map((value) => toFiniteNumber(value));
  if (box.some((value) => !Number.isFinite(value))) return null;
  return {
    label: item.label || "person",
    confidence: toFiniteNumber(item.confidence),
    box: [box[0], box[1], box[2], box[3]],
  };
}

export function createPredictionRows(metrics: PredictionMetricBackend[]): PredictionRowItem[] {
  return metrics.map((metric, index) => {
    const name = metric.model_name || "Model";
    return {
      id: `${metric.node_id || "node"}-${metric.metric || "metric"}-${name}`,
      name,
      type: `${metric.metric || "-"} · ${metric.node_id || "-"}`,
      nodeId: metric.node_id,
      metricKey: metric.metric,
      modelName: name,
      mae: formatNumber(metric.mae, 2),
      rmse: formatNumber(metric.rmse, 2),
      r2: formatNumber(metric.r2, 2),
      lat: `${formatNumber(metric.inference_latency_ms, 1)}ms`,
      color: metric.is_active ? "#129d6b" : predictionColor(index),
      active: Boolean(metric.is_active),
    };
  });
}

export function createInferencePredictPayload(input: InferencePredictPayloadInput) {
  return {
    task: input.task,
    input: {
      ...(input.cameraId ? { camera_id: input.cameraId } : {}),
      ...(input.frameId ? { frame_id: input.frameId } : {}),
      ...(input.imageUri ? { image_uri: input.imageUri } : {}),
      ...(input.horizon !== undefined ? { horizon: input.horizon } : {}),
      ...(input.metric ? { metric: input.metric } : {}),
    },
  };
}

export function createVisionEventQuery(input: VisionEventQueryInput): string {
  return buildQuery({
    camera_id: input.cameraId,
    task: input.task,
    limit: input.limit,
  });
}

function toFiniteNumber(value: number | string | null | undefined, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function metricValues(readings: DashboardReading[], metric: string): number[] {
  return readings.filter((reading) => reading.metric === metric).map((reading) => toFiniteNumber(reading.value)).filter(Number.isFinite);
}

const FRESH_READING_WINDOW_MS = 10 * 60 * 1000;

function freshReadings(readings: DashboardReading[]): DashboardReading[] {
  const times = readings.map((reading) => new Date(reading.captured_at || "").getTime()).filter((time) => !Number.isNaN(time));
  if (times.length === 0) return readings;
  const newest = Math.max(...times);
  return readings.filter((reading) => {
    const time = new Date(reading.captured_at || "").getTime();
    return Number.isNaN(time) || newest - time <= FRESH_READING_WINDOW_MS;
  });
}

function averageMetric(readings: DashboardReading[], metric: string): number {
  const values = metricValues(readings, metric);
  if (values.length === 0) return 0;
  return values.reduce((total, value) => total + value, 0) / values.length;
}

function trendFor(readings: DashboardReading[], metric: string, fallback: number[] | undefined, scalarFallback: number): number[] {
  const values = metricValues(readings, metric);
  if (values.length > 1) return values;
  if (values.length === 1) return [values[0], values[0]];
  if (fallback && fallback.length > 0) return fallback;
  return [scalarFallback || 0, scalarFallback || 0];
}

function latestDelta(readings: DashboardReading[], metric: string): string {
  const values = metricValues(readings, metric);
  if (values.length === 0) return "no backend reading";
  return `${values.length} latest ${metric} reading${values.length === 1 ? "" : "s"}`;
}

function formatMetricValue(value: number, digits: number): string {
  if (!Number.isFinite(value)) return "0";
  return digits === 0 ? String(Math.round(value)) : value.toFixed(digits);
}

function parseTriggerPayload(value: DashboardAlertBackend["trigger_payload"]): Record<string, unknown> {
  if (!value) return {};
  if (typeof value === "string") {
    try {
      return JSON.parse(value);
    } catch {
      return {};
    }
  }
  return value;
}

function triggerDetail(trigger: Record<string, unknown>): string {
  const value = trigger.value;
  if (value === undefined || value === null || value === "") return "-";
  const unit = trigger.unit;
  return `${value}${unit ? ` ${unit}` : ""}`;
}

function normalizeAlertSeverity(value?: string): DashboardAlertItem["severity"] {
  if (value === "critical") return "critical";
  if (value === "warning" || value === "medium") return "warning";
  return "info";
}

function normalizeAlertStatus(value?: string): AlertRecordItem["status"] {
  if (value === "acked" || value === "ack") return "ack";
  if (value === "resolved" || value === "closed") return "closed";
  return "active";
}

function normalizeCommandState(value?: string): DashboardCommandItem["state"] {
  if (value === "acked" || value === "sent" || value === "failed" || value === "timeout") return value;
  return "pending";
}

function formatApiDate(value?: string): string {
  if (!value) return "-";
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "-";
  const parts = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
  }).formatToParts(timestamp);
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value || "";
  return `${part("month")} ${part("day")} at ${part("hour")}:${part("minute")} ${part("dayPeriod")}`;
}

function roleRank(role?: string): number {
  if (role === "sink") return 0;
  if (role === "router") return 1;
  return 2;
}

function topologyType(role?: string): TopologyNodeItem["type"] {
  if (role === "sink") return "sink";
  if (role === "router") return "router";
  return "terminal";
}

function topologyPoint(type: TopologyNodeItem["type"], index: number, count: number): { x: number; y: number } {
  if (type === "terminal" && count > 8) {
    const columns = Math.min(8, Math.ceil(count / 2));
    const row = Math.floor(index / columns);
    const column = index % columns;
    const x = columns <= 1 ? 380 : 80 + (column / (columns - 1)) * 600;
    return { x, y: 305 + row * 78 };
  }
  const y = type === "sink" ? 70 : type === "router" ? 185 : 330;
  const usableWidth = type === "sink" ? 180 : 610;
  const start = type === "sink" ? 290 : 75;
  const x = count <= 1 ? 380 : start + (index / (count - 1)) * usableWidth;
  return { x, y };
}

function compactTopologyLabel(value: string): string {
  if (value.length <= 14) return value;
  const suffix = value.slice(-4);
  return `${value.slice(0, 7)}...${suffix}`;
}

function batteryPercent(value: number | string | null | undefined, type: TopologyNodeItem["type"]): number {
  if (type === "sink" && (value === null || value === undefined || value === "")) return 100;
  const mv = toFiniteNumber(value);
  if (mv <= 0) return 0;
  return Math.max(0, Math.min(100, Math.round(((mv - 2400) / 1200) * 100)));
}

function heartbeatSeconds(value?: string | null): number {
  if (!value) return 0;
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return 0;
  return Math.max(0, Math.round((Date.now() - timestamp.getTime()) / 1000));
}

function latestMetricForNode(readings: DashboardReading[], nodeId: string, metric: string): number {
  const found = readings.find((reading) => reading.node_id === nodeId && reading.metric === metric);
  return found ? toFiniteNumber(found.value) : 0;
}

function latestSourceForNode(readings: DashboardReading[], nodeId: string): string | undefined {
  return readings.find((reading) => reading.node_id === nodeId)?.source;
}

function isSerialHardwareTopologyNode(node: TopologyNodeItem): boolean {
  return node.source === "serial_hardware" || node.source === "serial";
}

function isMqttTopologyNode(node: TopologyNodeItem): boolean {
  return node.source === "mqtt";
}

function formatNumber(value: number | string | null | undefined, digits: number): string {
  const parsed = toFiniteNumber(value);
  return parsed.toFixed(digits);
}

function predictionColor(index: number): string {
  return ["#3b5bdb", "#e08206", "#0ea5b7", "#8c95a1"][index % 4];
}
