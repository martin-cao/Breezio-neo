import React, { type ChangeEvent, type FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  API_ROUTES,
  apiRequest,
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
  selectPreferredTopologyNode,
  type AlertRecordItem,
  type DashboardAlertBackend,
  type DashboardAlertItem,
  type DashboardCommandBackend,
  type DashboardCommandItem,
  type DashboardKpi,
  type DashboardSummary,
  type EnvironmentZoneItem,
  type PredictionMetricBackend,
  type PredictionRowItem,
  type TopologyNodeItem,
  type VisionDetectionBox,
  type VisionEventBackend,
  type VisionTimelineItem,
} from "./api";

const h = React.createElement;

type ViewId =
  | "dashboard"
  | "devices"
  | "history"
  | "topology"
  | "environment"
  | "vision"
  | "prediction"
  | "alerts"
  | "control"
  | "assistant"
  | "models"
  | "artifacts"
  | "users";

type NavItem = {
  id: ViewId;
  label: string;
  badge?: string;
  icon: ReactNode;
  adminOnly?: boolean;
};

const VIEW_IDS: ViewId[] = ["dashboard", "devices", "history", "topology", "environment", "vision", "prediction", "alerts", "control", "assistant", "models", "artifacts", "users"];

// Hash routing (`#/environment`) so a browser refresh restores the same page.
// `#register` / `#login` are reserved for the unauthenticated auth screen.
const viewFromHash = (): ViewId | null => {
  if (typeof window === "undefined") return null;
  const candidate = window.location.hash.replace(/^#\/?/, "");
  return (VIEW_IDS as string[]).includes(candidate) ? (candidate as ViewId) : null;
};

type Kpi = DashboardKpi;

type AlertItem = DashboardAlertItem;

type CommandItem = DashboardCommandItem;

type DeviceStatus = "online" | "offline";
type DeviceTab = "devices" | "nodes";
type StatusFilter = "all" | DeviceStatus;
type NodeStatusFilter = "all" | "online" | "offline" | "fault";
type ReadingMetric = "temperature" | "humidity" | "light" | "air_quality";
type EnvironmentMetric = Exclude<ReadingMetric, "air_quality">;
type TimeRangeId = "30m" | "2h" | "6h" | "12h" | "24h" | "48h" | "72h" | "7d" | "30d";
type PredictionHorizonId = "30m" | "2h" | "6h";
type SourceKind = string;
type TrendPoint = { value: number; label?: string };
type ChartViewport = { scrollLeft: number; clientWidth: number; scrollWidth: number };
type PredictionSamplePoint = { captured_at?: string; actual?: number | string | null; predicted?: number | string | null };
type BackendPredictionRun = {
  run_id?: string;
  node_id?: string;
  metric?: string;
  model_name?: string;
  status?: string;
  horizon_minutes?: number | string | null;
  sample_predictions?: PredictionSamplePoint[];
  is_active?: boolean;
};
type BackendLatestPrediction = {
  node_id?: string;
  metric?: string;
  model_name?: string;
  horizon_minutes?: number | string | null;
  target_time?: string;
  yhat?: number | string | null;
  series?: { target_time?: string; yhat?: number | string | null }[];
  historical_series?: { target_time?: string; yhat?: number | string | null }[];
  error?: string;
};

type LogicalDevice = {
  id: number;
  name: string;
  device_type: string;
  location: string;
  status: DeviceStatus;
  client_id: string;
  protocol?: string;
  ip_address?: string;
  port?: number | string;
  source_uri?: string;
  created_at: string;
};

type SensorNodeRow = {
  node_id: string;
  gateway_id: string;
  parent_node_id: string;
  role: "sink" | "router" | "sensor" | "actuator";
  area: string;
  ip_address: string;
  battery_mv: number | null;
  rssi: number | null;
  packet_loss: number | null;
  last_seen_at: string;
  status: "online" | "offline" | "fault";
};

type SensorReading = {
  message_id: string;
  gateway_id: string;
  batch_id: string;
  node_id: string;
  seq: number;
  metric: ReadingMetric;
  value: number;
  unit: string;
  rssi: number;
  battery_mv: number;
  flags: number;
  source: SourceKind;
  captured_at: string;
};

type DeviceModalState = LogicalDevice & { _isNew?: boolean };
type NodeModalState = Pick<SensorNodeRow, "node_id" | "gateway_id" | "parent_node_id" | "role" | "area" | "ip_address"> & { _isNew?: boolean };
type AlertFilter = "all" | "active" | "critical" | "warning" | "ack" | "closed";
type ArtifactType = "snapshot" | "dataset" | "model" | "onnx" | "benchmark" | "report";
type UserRole = "admin" | "operator" | "viewer";
type UserStatus = "active" | "invited" | "disabled";
type ThemeMode = "system" | "light" | "dark";
type SettingsTab = "profile" | "preferences" | "vision" | "assistant" | "security" | "demo";
type AuthMode = "login" | "register";

type UserAccount = {
  id: number;
  name: string;
  email: string;
  role: UserRole;
  status: UserStatus;
  last_login: string;
};

type UserModalState = Pick<UserAccount, "name" | "email" | "role" | "status"> & { id?: number; _isNew?: boolean };

type UserSettingsState = {
  notifications: {
    critical_alerts: boolean;
    warning_alerts: boolean;
    vacant_energy_waste: boolean;
  };
  vision: {
    preview_fps: number;
    preview_width: number;
    inference_hz: number;
    face_inference_hz: number;
    person_confidence: number;
    anomaly_inference_hz: number;
    caption_periodic_seconds: number;
    caption_on_anomaly: boolean;
  };
  chatbot: {
    base_url: string;
    model: string;
    provider_label: string;
  };
};

const DEFAULT_CHATBOT_SETTINGS: UserSettingsState["chatbot"] = {
  base_url: "http://127.0.0.1:11434/v1",
  model: "qwen2.5:7b",
  provider_label: "ollama-local",
};

// Deliberately gentle: keeps whatever the user typed (so editing isn't
// fought keystroke-by-keystroke); the backend applies defaults/trimming at
// request time when a field is left empty.
const normalizeChatbotSettings = (value?: Partial<UserSettingsState["chatbot"]>): UserSettingsState["chatbot"] => ({
  base_url: String(value?.base_url ?? DEFAULT_CHATBOT_SETTINGS.base_url),
  model: String(value?.model ?? DEFAULT_CHATBOT_SETTINGS.model),
  provider_label: String(value?.provider_label ?? DEFAULT_CHATBOT_SETTINGS.provider_label),
});

const DEFAULT_VISION_SETTINGS: UserSettingsState["vision"] = {
  preview_fps: 30,
  preview_width: 960,
  inference_hz: 5,
  face_inference_hz: 1,
  person_confidence: 0.25,
  anomaly_inference_hz: 0.1,
  caption_periodic_seconds: 300,
  caption_on_anomaly: true,
};

type AuthFormState = {
  name: string;
  email: string;
  password: string;
  confirmPassword: string;
  role: UserRole;
  remember: boolean;
};

type LabNode = TopologyNodeItem;

type ModelItem = {
  id?: string;
  name: string;
  version: string;
  task: string;
  metric: string;
  latency: string;
  hash: string;
  status: "active" | "available" | "deprecated" | "failed" | "registered";
  recommended?: boolean;
};

type ArtifactItem = {
  id: number;
  bucket: string;
  object_key: string;
  artifact_type: ArtifactType;
  content_type: string;
  size: string;
  sha256: string;
  etag: string;
  meta: string;
  url: string;
};

type BackendArtifact = {
  id: number;
  bucket: string;
  object_key: string;
  artifact_type: ArtifactType;
  content_type?: string;
  size_bytes?: number;
  sha256?: string;
  etag?: string;
  meta?: Record<string, unknown>;
  presigned_url?: string;
};

type BackendReading = {
  message_id?: string;
  gateway_id?: string;
  batch_id?: string;
  node_id?: string;
  seq?: number | string | null;
  metric?: ReadingMetric;
  value?: number | string | null;
  unit?: string;
  rssi?: number | string | null;
  battery_mv?: number | string | null;
  flags?: number | string | null;
  source?: string;
  captured_at?: string;
};

type BackendControlCommand = {
  command_id?: string;
  target_node_id?: string;
  target_device?: string;
  action?: string;
  status?: string;
  created_at?: string;
  ttl_seconds?: number | string | null;
};

type BackendSensorNode = {
  node_id?: string;
  gateway_id?: string | null;
  parent_node_id?: string | null;
  role?: SensorNodeRow["role"];
  area?: string | null;
  ip_address?: string | null;
  battery_mv?: number | string | null;
  rssi?: number | string | null;
  packet_loss?: number | string | null;
  last_seen_at?: string | null;
  status?: SensorNodeRow["status"];
};

type BackendUser = {
  id?: number;
  name?: string;
  email?: string;
  role?: UserRole;
  status?: UserStatus;
  last_login?: string | null;
};

type AuthResponse = {
  token_type: "Bearer";
  access_token: string;
  expires_in: number;
  user: BackendUser;
};

type BackendUserSettings = {
  theme_mode?: ThemeMode;
  notifications?: Partial<UserSettingsState["notifications"]>;
  vision?: Partial<UserSettingsState["vision"]>;
  chatbot?: Partial<UserSettingsState["chatbot"]>;
};

type BackendDevice = {
  id?: number;
  name?: string;
  device_type?: string;
  location?: string | null;
  status?: DeviceStatus;
  client_id?: string | null;
  protocol?: string | null;
  ip_address?: string | null;
  port?: number | string | null;
  source_uri?: string | null;
  created_at?: string;
};

type CameraProbeResponse = {
  ok?: boolean;
  source_uri?: string;
  snapshot_path?: string | null;
  error?: string;
  stream?: {
    width?: number;
    height?: number;
    codec_name?: string;
    avg_frame_rate?: string;
    r_frame_rate?: string;
  };
};

type BackendModel = {
  model_id?: string;
  task?: string;
  version?: string;
  runtime?: string;
  status?: string;
  artifact_uri?: string;
};

type AlertRecord = AlertRecordItem;

const apiLabel = (route: string) => buildBackendUrl(route);

const icon = (children: ReactNode[]) =>
  h(
    "svg",
    {
      width: 16,
      height: 16,
      viewBox: "0 0 16 16",
      fill: "none",
      stroke: "currentColor",
      strokeWidth: 1.5,
      strokeLinecap: "round",
      strokeLinejoin: "round",
    },
    children,
  );

const icons = {
  grid: icon([
    h("rect", { key: 1, x: 2, y: 2, width: 5, height: 5, rx: 1 }),
    h("rect", { key: 2, x: 9, y: 2, width: 5, height: 5, rx: 1 }),
    h("rect", { key: 3, x: 2, y: 9, width: 5, height: 5, rx: 1 }),
    h("rect", { key: 4, x: 9, y: 9, width: 5, height: 5, rx: 1 }),
  ]),
  nodes: icon([
    h("circle", { key: 1, cx: 8, cy: 3, r: 1.6 }),
    h("circle", { key: 2, cx: 3, cy: 12, r: 1.6 }),
    h("circle", { key: 3, cx: 13, cy: 12, r: 1.6 }),
    h("path", { key: 4, d: "M7 4.3 4 10.5M9 4.3 12 10.5" }),
  ]),
  wave: icon([h("path", { key: 1, d: "M1.5 9c1.5 0 1.5-4 3-4s1.5 4 3 4 1.5-5 3-5 1.5 5 3 5" })]),
  cam: icon([
    h("rect", { key: 1, x: 1.8, y: 4, width: 12.4, height: 8, rx: 1.5 }),
    h("circle", { key: 2, cx: 8, cy: 8, r: 2.2 }),
    h("path", { key: 3, d: "M5.5 4 6.5 2.5h3L10.5 4" }),
  ]),
  chart: icon([h("path", { key: 1, d: "M2 13V3M2 13h11" }), h("path", { key: 2, d: "M4.5 10 7 7l2 1.5 3-4.5" })]),
  bell: icon([h("path", { key: 1, d: "M4 6.5a4 4 0 1 1 8 0c0 3 1 4 1 4H3s1-1 1-4Z" }), h("path", { key: 2, d: "M6.5 13a1.6 1.6 0 0 0 3 0" })]),
  slider: icon([h("path", { key: 1, d: "M2 4.5h8M13 4.5h1M2 11.5h4M9 11.5h5" }), h("circle", { key: 2, cx: 11, cy: 4.5, r: 1.6 }), h("circle", { key: 3, cx: 7, cy: 11.5, r: 1.6 })]),
  layers: icon([h("path", { key: 1, d: "M8 2 14 5 8 8 2 5 8 2Z" }), h("path", { key: 2, d: "M2 8.5 8 11.5 14 8.5M2 11.5 8 14 14 11.5" })]),
  users: icon([
    h("circle", { key: 1, cx: 6, cy: 5.3, r: 2.2 }),
    h("path", { key: 2, d: "M1.8 13.2c0-2.4 1.9-3.8 4.2-3.8s4.2 1.4 4.2 3.8" }),
    h("circle", { key: 3, cx: 11.6, cy: 5.6, r: 1.6 }),
    h("path", { key: 4, d: "M10.5 9.6c1.8.2 3.1 1.5 3.1 3.5" }),
  ]),
  gear: icon([
    h("path", { key: 1, d: "M6.5 2.2h3l.4 1.6 1.4.6 1.5-.8 1.5 2.6-1.2 1.1.1.7-.1.7 1.2 1.1-1.5 2.6-1.5-.8-1.4.6-.4 1.6h-3l-.4-1.6-1.4-.6-1.5.8-1.5-2.6 1.2-1.1-.1-.7.1-.7-1.2-1.1 1.5-2.6 1.5.8 1.4-.6.4-1.6Z" }),
    h("circle", { key: 2, cx: 8, cy: 8, r: 1.8 }),
  ]),
};

const navItems: NavItem[] = [
  { id: "dashboard", label: "Dashboard", icon: icons.grid },
  { id: "devices", label: "Device Management", icon: icons.nodes },
  { id: "history", label: "History", icon: icons.chart },
  { id: "topology", label: "Sensor Network", icon: icons.nodes },
  { id: "environment", label: "Environment", icon: icons.wave },
  { id: "vision", label: "Vision Monitoring", icon: icons.cam },
  { id: "prediction", label: "Prediction Center", icon: icons.chart },
  { id: "alerts", label: "Alert Center", icon: icons.bell, badge: "5" },
  { id: "control", label: "Remote Control", icon: icons.slider },
  { id: "assistant", label: "Lab Assistant", icon: icons.users },
  { id: "models", label: "Model Selection", icon: icons.layers },
  { id: "artifacts", label: "Artifacts", icon: icons.layers },
  { id: "users", label: "User Management", icon: icons.users, adminOnly: true },
];

const titles: Record<ViewId, [string, string]> = {
  dashboard: ["Operations Dashboard", "Real-time lab awareness · hardware bridge active"],
  devices: ["Devices", `Logical devices & sensor nodes · ${apiLabel(API_ROUTES.devices)} · ${apiLabel(API_ROUTES.nodes)}`],
  history: ["History", `Historical sensor readings · ${apiLabel(API_ROUTES.readings)}`],
  topology: ["Sensor Network", "Live hardware nodes · serial gateway · environmental telemetry"],
  environment: ["Environment Monitoring", "Temperature · Humidity · Light across 4 zones"],
  vision: ["Vision Monitoring", "Camera-01 · YOLO detection + scene classification"],
  prediction: ["Prediction Center", "Model comparison · actual vs predicted · active model control"],
  alerts: ["Alert Center", "Rules engine · 5 active"],
  control: ["Remote Control", "Command dispatch · Web → Backend → MQTT → Gateway → Node"],
  assistant: ["Lab Assistant", "Tool-calling LLM · grounded in live readings, alerts, vision & forecasts"],
  models: ["Model Selection", "Active runtime models by task"],
  artifacts: ["Artifacts", `${apiLabel(API_ROUTES.artifacts)} · RustFS object metadata`],
  users: ["User Management", `Administrators only · manage lab accounts & roles · ${apiLabel(API_ROUTES.users)}`],
};

const series = {
  temp: [23.8, 24.0, 24.2, 24.0, 24.4, 24.5, 24.1, 24.4, 24.7, 25.0, 24.8, 24.9, 25.1, 25.3, 25.0, 24.8, 24.9, 25.2, 25.4, 25.7, 25.6, 25.4, 25.1, 25.0],
  hum: [57, 56, 55, 55, 54, 53, 52, 52, 51, 50, 50, 51, 52, 53, 54, 54, 55, 56, 57, 57, 58, 59, 59, 60],
  light: [420, 410, 405, 390, 380, 370, 360, 350, 348, 355, 362, 370, 376, 385, 392, 400, 408, 415, 425, 435, 440, 448, 455, 462],
};

const sampleKpis: Kpi[] = [
  { label: "Active Nodes", value: "10", unit: "/ 11", delta: "1 offline", deltaColor: "#e08206", dot: "#129d6b", data: [8, 9, 10, 10, 10, 10, 10], color: "#129d6b", fill: "rgba(18,157,107,.08)" },
  { label: "Online Zones", value: "4", unit: "/ 4", delta: "all reporting", deltaColor: "#129d6b", dot: "#129d6b", data: [4, 4, 4, 4, 4, 4, 4], color: "#129d6b", fill: "rgba(18,157,107,.08)" },
  { label: "Active Alerts", value: "5", unit: "", delta: "2 critical", deltaColor: "#dc4444", dot: "#dc4444", data: [3, 3, 4, 5, 4, 5, 5], color: "#dc4444", fill: "rgba(220,68,68,.08)" },
  { label: "People", value: "3", unit: "in view", delta: "Camera-01", deltaColor: "#8c95a1", dot: "#3b5bdb", data: [1, 2, 2, 3, 3, 3, 3], color: "#3b5bdb", fill: "rgba(59,91,219,.08)" },
  { label: "Live Temp", value: "24.5", unit: "°C", delta: "range 23–34°C", deltaColor: "#8c95a1", dot: "#e08206", data: series.temp.slice(-8), color: "#e08206", fill: "rgba(224,130,6,.08)" },
  { label: "Live Humidity", value: "54", unit: "%", delta: "range 48–73%", deltaColor: "#8c95a1", dot: "#3b5bdb", data: series.hum.slice(-8), color: "#3b5bdb", fill: "rgba(59,91,219,.08)" },
  { label: "Live Light", value: "379", unit: "", delta: "range 68–820", deltaColor: "#8c95a1", dot: "#0ea5b7", data: series.light.slice(-8), color: "#0ea5b7", fill: "rgba(14,165,183,.08)" },
];

const alerts: AlertItem[] = [
  { title: "High Temperature", source: "Equipment-2 · N05", detail: "34.2 °C", severity: "critical", status: "ACTIVE", ago: "4m ago" },
  { title: "Node Offline", source: "Window-2 · N07", detail: "74s silent", severity: "critical", status: "ACTIVE", ago: "6m ago" },
  { title: "High Humidity", source: "Window-1 · N06", detail: "73 %", severity: "warning", status: "ACTIVE", ago: "9m ago" },
  { title: "Poor Sensor Link", source: "Window-2 · N07", detail: "22.4 %", severity: "warning", status: "ACTIVE", ago: "11m ago" },
];

const commands: CommandItem[] = [
  { node: "N03", action: "Turn OFF", device: "Light", state: "acked", ago: "44s ago" },
  { node: "N05", action: "Turn ON", device: "Fan", state: "sent", ago: "1m ago" },
  { node: "N04", action: "Cut Power", device: "Relay", state: "pending", ago: "2m ago" },
  { node: "N01", action: "Pulse 3×", device: "Buzzer", state: "failed", ago: "4m ago" },
];

const gatewayIds = ["gw-lab317-a", "gw-lab317-b"];

const initialDevices: LogicalDevice[] = [
  { id: 1, name: "Camera-01", device_type: "camera", location: "Lab Overview", status: "offline", client_id: "cam-rtsp-01", protocol: "rtsp", ip_address: "192.0.2.72", port: 554, source_uri: "rtsp://192.0.2.72", created_at: "2026-05-02T09:12:00Z" },
  { id: 2, name: "Relay Panel A", device_type: "relay", location: "Equipment Area", status: "online", client_id: "dev-relay-a", created_at: "2026-05-02T09:14:00Z" },
  { id: 3, name: "Fan Controller", device_type: "fan", location: "Equipment Area", status: "online", client_id: "dev-fan-01", created_at: "2026-05-03T11:02:00Z" },
  { id: 4, name: "Light Bank · Entrance", device_type: "lamp", location: "Lab Entrance", status: "online", client_id: "dev-lamp-ent", created_at: "2026-05-03T11:05:00Z" },
  { id: 5, name: "Buzzer Unit", device_type: "buzzer", location: "Workstation Area", status: "offline", client_id: "dev-buzz-01", created_at: "2026-05-04T08:40:00Z" },
  { id: 6, name: "MQTT Bridge Client", device_type: "gateway_client", location: "Server Rack", status: "online", client_id: "dev-mqtt-01", created_at: "2026-05-01T15:00:00Z" },
];

const initialNodes: SensorNodeRow[] = [
  { node_id: "S01", gateway_id: "gw-lab317-a", parent_node_id: "-", role: "sink", area: "Server Rack", ip_address: "-", battery_mv: null, rssi: -42, packet_loss: 0.2, last_seen_at: "1s ago", status: "online" },
  { node_id: "R01", gateway_id: "gw-lab317-a", parent_node_id: "S01", role: "router", area: "Lab Entrance", ip_address: "-", battery_mv: 3440, rssi: -48, packet_loss: 1.4, last_seen_at: "3s ago", status: "online" },
  { node_id: "R02", gateway_id: "gw-lab317-a", parent_node_id: "S01", role: "router", area: "Window Area", ip_address: "-", battery_mv: 3392, rssi: -53, packet_loss: 2.1, last_seen_at: "5s ago", status: "online" },
  { node_id: "N01", gateway_id: "gw-lab317-a", parent_node_id: "R01", role: "sensor", area: "Lab Entrance", ip_address: "-", battery_mv: 3276, rssi: -58, packet_loss: 3.6, last_seen_at: "8s ago", status: "online" },
  { node_id: "N02", gateway_id: "gw-lab317-a", parent_node_id: "R01", role: "sensor", area: "Workstation Area", ip_address: "-", battery_mv: 3188, rssi: -61, packet_loss: 5.2, last_seen_at: "12s ago", status: "online" },
  { node_id: "N03", gateway_id: "gw-lab317-a", parent_node_id: "R01", role: "actuator", area: "Light Bank", ip_address: "-", battery_mv: 3128, rssi: -63, packet_loss: 4.0, last_seen_at: "15s ago", status: "online" },
  { node_id: "N04", gateway_id: "gw-lab317-b", parent_node_id: "R02", role: "actuator", area: "Relay Panel", ip_address: "-", battery_mv: 3024, rssi: -68, packet_loss: 9.8, last_seen_at: "24s ago", status: "online" },
  { node_id: "N05", gateway_id: "gw-lab317-b", parent_node_id: "R02", role: "sensor", area: "Equipment Area", ip_address: "-", battery_mv: 2896, rssi: -71, packet_loss: 18.6, last_seen_at: "42s ago", status: "fault" },
  { node_id: "N06", gateway_id: "gw-lab317-b", parent_node_id: "R02", role: "sensor", area: "Window-1", ip_address: "-", battery_mv: 2960, rssi: -69, packet_loss: 7.7, last_seen_at: "35s ago", status: "online" },
  { node_id: "N07", gateway_id: "gw-lab317-b", parent_node_id: "R02", role: "sensor", area: "Window-2", ip_address: "-", battery_mv: null, rssi: null, packet_loss: null, last_seen_at: "74s ago (stale)", status: "offline" },
];

// light is a raw photoresistor index (0-100, lower = brighter), not calibrated lux — no unit to show.
const readingUnit = (metric: ReadingMetric) => (metric === "temperature" ? "°C" : metric === "humidity" ? "%" : metric === "light" ? "" : "mV");
const readingBase = (metric: ReadingMetric) => (metric === "temperature" ? 24 : metric === "humidity" ? 50 : metric === "light" ? 300 : 610);
const predictionMetricOptions: { id: ReadingMetric; label: string }[] = [
  { id: "temperature", label: "Temperature" },
  { id: "humidity", label: "Humidity" },
  { id: "light", label: "Light" },
  { id: "air_quality", label: "Air Quality" },
];
const predictionHorizonOptions: { id: PredictionHorizonId; label: string; minutes: number; historyMultiplier: number; limit: number }[] = [
  { id: "30m", label: "Next 30m", minutes: 30, historyMultiplier: 4, limit: 480 },
  { id: "2h", label: "Next 2h", minutes: 120, historyMultiplier: 4, limit: 960 },
  { id: "6h", label: "Next 6h", minutes: 360, historyMultiplier: 4, limit: 1600 },
];
const predictionHorizon = (horizonId: PredictionHorizonId) => predictionHorizonOptions.find((option) => option.id === horizonId) || predictionHorizonOptions[0];
const predictionWindow = (horizonId: PredictionHorizonId) => {
  const option = predictionHorizon(horizonId);
  const end = new Date();
  const start = new Date(end.getTime() - option.minutes * option.historyMultiplier * 60_000);
  return { option, start: start.toISOString(), end: end.toISOString(), limit: option.limit };
};
const metricLabel = (metric: ReadingMetric) => predictionMetricOptions.find((item) => item.id === metric)?.label || metric;
const isSamePredictionHorizon = (actual: number | string | null | undefined, horizon: ReturnType<typeof predictionHorizon>) =>
  Math.abs(toNumber(actual, -1) - horizon.minutes) < 0.001;
// Each option's `label`/`visibleMs` is how much time fills the chart's visible
// width; the chart canvas is rendered HISTORY_LOOKBACK_RATIO times wider than
// that, so the user can scroll left within one loaded window to see further
// back (30m -> up to 2h, 24h -> up to 4d, etc.) instead of needing to re-fetch.
const HISTORY_LOOKBACK_RATIO = 4;
const timeRangeOptions: { id: TimeRangeId; label: string; visibleMs: number; limit: number }[] = [
  { id: "30m", label: "30m", visibleMs: 30 * 60_000, limit: 3600 },
  { id: "2h", label: "2h", visibleMs: 2 * 60 * 60_000, limit: 4200 },
  { id: "6h", label: "6h", visibleMs: 6 * 60 * 60_000, limit: 4200 },
  { id: "12h", label: "12h", visibleMs: 12 * 60 * 60_000, limit: 4200 },
  { id: "24h", label: "24h", visibleMs: 24 * 60 * 60_000, limit: 4200 },
  { id: "48h", label: "48h", visibleMs: 48 * 60 * 60_000, limit: 4200 },
  { id: "72h", label: "72h", visibleMs: 72 * 60 * 60_000, limit: 4200 },
  { id: "7d", label: "7d", visibleMs: 7 * 24 * 60 * 60_000, limit: 4200 },
  { id: "30d", label: "30d", visibleMs: 30 * 24 * 60 * 60_000, limit: 5000 },
];

const rangeOption = (rangeId: TimeRangeId) => timeRangeOptions.find((option) => option.id === rangeId) || timeRangeOptions[0];
const rangeMaxMs = (rangeId: TimeRangeId) => rangeOption(rangeId).visibleMs * HISTORY_LOOKBACK_RATIO;
const rangeWindow = (rangeId: TimeRangeId, windowEnd: Date | null) => {
  const option = rangeOption(rangeId);
  const maxMs = option.visibleMs * HISTORY_LOOKBACK_RATIO;
  const end = windowEnd || new Date();
  const start = new Date(end.getTime() - maxMs);
  return { option, start: start.toISOString(), end: end.toISOString(), limit: option.limit, visibleMs: option.visibleMs, maxMs };
};
const shiftWindowEnd = (rangeId: TimeRangeId, currentEnd: Date | null, direction: -1 | 1) => {
  const maxMs = rangeMaxMs(rangeId);
  const now = Date.now();
  const base = currentEnd?.getTime() || now;
  const next = base + direction * maxMs * 0.8;
  return new Date(Math.min(now, Math.max(maxMs, next)));
};
const rangeLabel = (rangeId: TimeRangeId, windowEnd: Date | null) => {
  const option = rangeOption(rangeId);
  if (!windowEnd) return `${option.label} · live`;
  return `${option.label} · ending ${formatShortDate(windowEnd.toISOString())}`;
};

const initialReadings: SensorReading[] = Array.from({ length: 72 }, (_, index) => {
  const nodes = ["N01", "N02", "N03", "N04", "N05", "N06"];
  const metrics: ReadingMetric[] = ["temperature", "humidity", "light"];
  const metric = metrics[index % metrics.length];
  const captured = new Date(Date.UTC(2026, 6, 1, 12, 0, 0) - index * 90_000);
  const variance = metric === "light" ? ((index % 9) - 4) * 9 : metric === "humidity" ? ((index % 7) - 3) * 0.8 : ((index % 5) - 2) * 0.22;
  return {
    message_id: `msg-${9000 - index}`,
    gateway_id: index % 2 === 0 ? "gw-lab317-a" : "gw-lab317-b",
    batch_id: `batch-${Math.floor(index / 6)}`,
    node_id: nodes[index % nodes.length],
    seq: 4000 - index,
    metric,
    value: Number((readingBase(metric) + variance).toFixed(1)),
    unit: readingUnit(metric),
    rssi: -50 - (index % 30),
    battery_mv: 2750 + ((index * 37) % 450),
    flags: 0,
    source: index % 9 === 0 ? "manual" : "mqtt",
    captured_at: captured.toISOString(),
  };
});

const labNodes: LabNode[] = ([
  { id: "S01", name: "Coordinator", type: "sink", zone: "Server Rack", status: "online", batt: 100, rssi: -42, loss: 0.2, hb: 1, temp: 24.1, hum: 52, light: 310, x: 380, y: 70 },
  { id: "R01", name: "Entrance Router", type: "router", zone: "Lab Entrance", parent: "S01", status: "online", batt: 92, rssi: -48, loss: 1.4, hb: 3, temp: 22.8, hum: 49, light: 170, x: 210, y: 180 },
  { id: "R02", name: "Window Router", type: "router", zone: "Window Side", parent: "S01", status: "online", batt: 88, rssi: -53, loss: 2.1, hb: 5, temp: 25.9, hum: 73, light: 820, x: 550, y: 180 },
  { id: "N01", name: "Entrance Sensor", type: "terminal", zone: "Lab Entrance", parent: "R01", status: "online", batt: 72, rssi: -58, loss: 3.6, hb: 8, temp: 23.1, hum: 50, light: 168, x: 95, y: 315 },
  { id: "N02", name: "Workstation Sensor", type: "terminal", zone: "Workstation Area", parent: "R01", status: "online", batt: 61, rssi: -61, loss: 5.2, hb: 12, temp: 24.7, hum: 53, light: 422, x: 215, y: 340 },
  { id: "N03", name: "Light Actuator", type: "terminal", zone: "Light Bank", parent: "R01", status: "online", batt: 55, rssi: -63, loss: 4.0, hb: 15, temp: 24.3, hum: 51, light: 462, x: 330, y: 315 },
  { id: "N04", name: "Relay Actuator", type: "terminal", zone: "Relay Panel", parent: "R02", status: "online", batt: 46, rssi: -68, loss: 9.8, hb: 24, temp: 26.8, hum: 55, light: 290, x: 465, y: 330 },
  { id: "N05", name: "Equipment Sensor", type: "terminal", zone: "Equipment Area", parent: "R02", status: "fault", batt: 31, rssi: -71, loss: 18.6, hb: 42, temp: 34.2, hum: 56, light: 262, x: 580, y: 318 },
  { id: "N06", name: "Window-1 Sensor", type: "terminal", zone: "Window-1", parent: "R02", status: "online", batt: 41, rssi: -69, loss: 7.7, hb: 35, temp: 26.1, hum: 73, light: 810, x: 665, y: 260 },
  { id: "N07", name: "Window-2 Sensor", type: "terminal", zone: "Window-2", parent: "R02", status: "offline", batt: 18, rssi: -84, loss: 22.4, hb: 74, temp: 0, hum: 0, light: 0, x: 690, y: 375 },
] as Omit<LabNode, "label">[]).map((node) => ({ ...node, label: node.id }));

const predictionModels = [
  { id: "lr", name: "Linear Regression", type: "Baseline", mae: "1.28", rmse: "1.92", r2: "0.82", lat: "4ms", color: "#3b5bdb" },
  { id: "rf", name: "Random Forest", type: "Ensemble", mae: "0.91", rmse: "1.34", r2: "0.93", lat: "12ms", color: "#129d6b", active: true },
  { id: "lstm", name: "LSTM", type: "Sequence", mae: "0.76", rmse: "1.12", r2: "0.95", lat: "28ms", color: "#e08206", recommended: true },
];

const modelItems: ModelItem[] = [
  { name: "Random Forest", version: "py-2.1", task: "Prediction", metric: "R² 0.93 · RMSE 1.34", latency: "12ms", hash: "7b3c...f18", status: "active", recommended: true },
  { name: "Linear Regression", version: "py-1.0", task: "Prediction", metric: "R² 0.82 · RMSE 1.92", latency: "4ms", hash: "2a91...c40", status: "available" },
  { name: "LSTM", version: "py-1.4", task: "Prediction", metric: "R² 0.95 · RMSE 1.12", latency: "28ms", hash: "4dd8...091", status: "available" },
  { name: "SceneNet-Tiny", version: "scene-1.0", task: "Scene Classification", metric: "Acc 0.91", latency: "18ms", hash: "19ad...e51", status: "active" },
  { name: "YOLOv8n", version: "v8n-1.3", task: "Object Detection", metric: "mAP@50 0.52", latency: "125ms", hash: "a91f...7c2", status: "active" },
  { name: "YOLOv8s", version: "v8s-0.9", task: "Object Detection", metric: "mAP@50 0.61", latency: "246ms", hash: "d820...2e1", status: "available" },
];

const artifactItems: ArtifactItem[] = [
  { id: 101, bucket: "lab-snapshots", object_key: "snapshots/2026-07-01/cam01-1241.jpg", artifact_type: "snapshot", content_type: "image/jpeg", size: "180 KB", sha256: "a3f1c9...04e2", etag: "\"7c2a1e\"", meta: "camera_id=cam-01 · people_count=2", url: "#" },
  { id: 98, bucket: "lab-datasets", object_key: "datasets/env-14d-5min.parquet", artifact_type: "dataset", content_type: "application/octet-stream", size: "5.0 MB", sha256: "9be2d0...1a7f", etag: "\"51f0aa\"", meta: "rows=4032 · nodes=6", url: "#" },
  { id: 88, bucket: "lab-models", object_key: "models/prediction/random-forest-v2.pkl", artifact_type: "model", content_type: "application/octet-stream", size: "2.4 MB", sha256: "7b3c80...f18", etag: "\"be81a0\"", meta: "task=prediction · active=true", url: "#" },
  { id: 79, bucket: "lab-models", object_key: "models/vision/yolov8n.onnx", artifact_type: "onnx", content_type: "application/octet-stream", size: "12.1 MB", sha256: "a91f20...7c2", etag: "\"9c210d\"", meta: "task=detection · backend=onnx", url: "#" },
  { id: 73, bucket: "lab-reports", object_key: "reports/benchmark-2026-07-01.json", artifact_type: "benchmark", content_type: "application/json", size: "64 KB", sha256: "4de118...bc9", etag: "\"71ad09\"", meta: "runtime=cpu · batch=1", url: "#" },
];

const initialUsers: UserAccount[] = [
  { id: 1, name: "Lab Admin", email: "operator@lab-317", role: "admin", status: "active", last_login: "Just now" },
  { id: 2, name: "Wei Chen", email: "wei.chen@lab317.edu", role: "operator", status: "active", last_login: "2h ago" },
  { id: 3, name: "Priya Nair", email: "priya.nair@lab317.edu", role: "operator", status: "active", last_login: "1d ago" },
  { id: 4, name: "Course Evaluator", email: "evaluator@univ.edu", role: "viewer", status: "invited", last_login: "-" },
  { id: 5, name: "Sam Torres", email: "sam.torres@lab317.edu", role: "operator", status: "disabled", last_login: "12d ago" },
];

const roleMeta: Record<UserRole, { label: string; color: string; background: string }> = {
  admin: { label: "Admin", color: "#3b5bdb", background: "rgba(59, 91, 219, 0.14)" },
  operator: { label: "Operator", color: "#129d6b", background: "rgba(18, 157, 107, 0.14)" },
  viewer: { label: "Viewer", color: "#8c95a1", background: "rgba(140, 149, 161, 0.16)" },
};

const statusMeta: Record<UserStatus, { label: string; color: string; background: string }> = {
  active: { label: "Active", color: "#129d6b", background: "rgba(18, 157, 107, 0.14)" },
  invited: { label: "Invited", color: "#e08206", background: "rgba(224, 130, 6, 0.14)" },
  disabled: { label: "Disabled", color: "#dc4444", background: "rgba(220, 68, 68, 0.12)" },
};

const getInitialSystemTheme = () => (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light");
const getInitialAuthMode = (): AuthMode => (typeof window !== "undefined" && window.location.hash === "#register" ? "register" : "login");
const storedAccessToken = () => {
  if (typeof window === "undefined") return "";
  return window.sessionStorage.getItem("smart-lab-access-token") || window.localStorage.getItem("smart-lab-access-token") || "";
};

// Subscribes to the backend's SSE reading stream and returns a counter that
// increments whenever new sensor readings land, so data effects can list it as
// a dependency and refetch. EventSource reconnects automatically on errors.
function useLiveReadingsTick(): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (typeof window === "undefined" || typeof EventSource === "undefined") return;
    let lastBump = 0;
    const bump = () => {
      // Backend polls/pushes every ~1s (BREEZIO_STREAM_POLL_SECONDS); stay
      // under that so a real push is never swallowed by the debounce.
      const now = Date.now();
      if (now - lastBump > 400) {
        lastBump = now;
        setTick((value) => value + 1);
      }
    };
    const token = storedAccessToken();
    const url = buildBackendUrl(API_ROUTES.readingsStream, token ? { query: { access_token: token } } : {});
    const source = new EventSource(url);
    source.onmessage = bump;
    return () => source.close();
  }, []);
  return tick;
}

// Generic polling clock: increments every `ms` so components can list it as an
// effect dependency to refetch on a fixed cadence (used where no SSE push
// exists yet — devices/nodes/alerts/commands at 5s, inference-derived pages
// at 30s per the target report-rate budget).
function usePollTick(ms: number): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((value) => value + 1), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return tick;
}
const storeAccessToken = (token: string, remember: boolean) => {
  window.sessionStorage.removeItem("smart-lab-access-token");
  window.localStorage.removeItem("smart-lab-access-token");
  const target = remember ? window.localStorage : window.sessionStorage;
  target.setItem("smart-lab-access-token", token);
};
const clearAccessToken = () => {
  window.sessionStorage.removeItem("smart-lab-access-token");
  window.localStorage.removeItem("smart-lab-access-token");
};
const storedThemeMode = (): ThemeMode => {
  if (typeof window === "undefined") return "system";
  const stored = window.localStorage.getItem("smart-lab-theme-mode");
  return stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
};

const alertRecords: AlertRecord[] = [
  { id: "AL-2040", sev: "critical", title: "High Temperature", source: "Equipment-2 · N05", rule: "temperature > 32°C", value: "34.2°C", status: "active", created: "14:20:31", age: "4m", note: "Fan command suggested; equipment zone should be checked." },
  { id: "AL-2039", sev: "critical", title: "Node Offline", source: "Window-2 · N07", rule: "heartbeat > 60s", value: "74s silent", status: "active", created: "14:18:02", age: "6m", note: "Gateway still online; likely node power or link issue." },
  { id: "AL-2038", sev: "warning", title: "High Humidity", source: "Window-1 · N06", rule: "humidity > 70%", value: "73%", status: "active", created: "14:15:44", age: "9m", note: "Window side exceeded threshold for 12 minutes." },
  { id: "AL-2037", sev: "warning", title: "Poor Sensor Link", source: "Window-2 · N07", rule: "packet_loss > 15%", value: "22.4%", status: "ack", created: "14:13:18", age: "11m", note: "Acknowledged by Lab Admin." },
  { id: "AL-2033", sev: "critical", title: "Abnormal People Detection", source: "Camera-01", rule: "count > 0 outside hours", value: "2 people", status: "closed", created: "03:12:44", age: "11h", note: "Confirmed authorized night access." },
];

function formatDateTime(iso: string, includeSecond = false) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "-";
  const parts = new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    ...(includeSecond ? { second: "2-digit" } : {}),
  }).formatToParts(date);
  const part = (type: string) => parts.find((item) => item.type === type)?.value || "";
  const second = includeSecond ? `:${part("second")}` : "";
  return `${part("month")} ${part("day")}, ${part("hour")}:${part("minute")}${second} ${part("dayPeriod")}`;
}

const formatShortDate = (iso: string) => formatDateTime(iso);
const formatReadingDate = (iso: string) => formatDateTime(iso, true);
const toNumber = (value: number | string | null | undefined, fallback = 0) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};

function forecastSeriesToPoints(series: { target_time?: string; yhat?: number | string | null }[] | undefined): TrendPoint[] {
  return (series || [])
    .map((point) => ({ value: toNumber(point.yhat, Number.NaN), label: point.target_time || "" }))
    .filter((point) => Number.isFinite(point.value) && point.label);
}

function latestHistoricalToPoints(prediction: BackendLatestPrediction | null, horizon: ReturnType<typeof predictionHorizon>): TrendPoint[] {
  if (!prediction || prediction.error) return [];
  if (!isSamePredictionHorizon(prediction.horizon_minutes, horizon)) return [];
  return forecastSeriesToPoints(prediction.historical_series);
}

function latestPredictionToPoints(prediction: BackendLatestPrediction | null, horizon: ReturnType<typeof predictionHorizon>): TrendPoint[] {
  if (!prediction || prediction.error) return [];
  if (!isSamePredictionHorizon(prediction.horizon_minutes, horizon)) return [];
  const seriesPoints = forecastSeriesToPoints(prediction.series);
  if (seriesPoints.length > 0) return seriesPoints;
  if (!prediction.target_time || prediction.yhat === null || prediction.yhat === undefined) return [];
  return [{ value: toNumber(prediction.yhat), label: prediction.target_time }];
}

const normalizeReading = (reading: BackendReading): SensorReading => {
  const metric = reading.metric || "temperature";
  return {
    message_id: reading.message_id || `reading-${reading.node_id || "unknown"}-${reading.metric || "metric"}-${reading.captured_at || Date.now()}`,
    gateway_id: reading.gateway_id || "-",
    batch_id: reading.batch_id || "-",
    node_id: reading.node_id || "-",
    seq: toNumber(reading.seq),
    metric,
    value: toNumber(reading.value),
    unit: reading.unit || readingUnit(metric),
    rssi: toNumber(reading.rssi),
    battery_mv: toNumber(reading.battery_mv),
    flags: toNumber(reading.flags),
    source: reading.source || "mqtt",
    captured_at: reading.captured_at || new Date().toISOString(),
  };
};

const normalizeCommandState = (status?: string): CommandItem["state"] => {
  if (status === "acked" || status === "sent" || status === "failed" || status === "timeout") return status;
  return "pending";
};

const normalizeCommand = (command: BackendControlCommand): CommandItem => ({
  command_id: command.command_id,
  node: command.target_node_id || "-",
  device: command.target_device || "-",
  action: command.action || "-",
  state: normalizeCommandState(command.status),
  ago: command.created_at ? formatShortDate(command.created_at) : "-",
});

const normalizeNode = (node: BackendSensorNode): SensorNodeRow => ({
  node_id: node.node_id || "-",
  gateway_id: node.gateway_id || "-",
  parent_node_id: node.parent_node_id || "-",
  role: node.role || "sensor",
  area: node.area || "-",
  ip_address: node.ip_address || "-",
  battery_mv: node.battery_mv === null || node.battery_mv === undefined ? null : toNumber(node.battery_mv),
  rssi: node.rssi === null || node.rssi === undefined ? null : toNumber(node.rssi),
  packet_loss: node.packet_loss === null || node.packet_loss === undefined ? null : toNumber(node.packet_loss),
  last_seen_at: node.last_seen_at ? formatShortDate(node.last_seen_at) : "-",
  status: node.status || "offline",
});

const normalizeUser = (user: BackendUser): UserAccount => ({
  id: Number(user.id || 0),
  name: user.name || "-",
  email: user.email || "-",
  role: user.role || "viewer",
  status: user.status || "invited",
  last_login: user.last_login ? formatShortDate(user.last_login) : "-",
});

const normalizeLogicalDevice = (device: BackendDevice): LogicalDevice => ({
  id: Number(device.id || 0),
  name: device.name || "-",
  device_type: device.device_type || "-",
  location: device.location || "-",
  status: device.status || "offline",
  client_id: device.client_id || "-",
  protocol: device.protocol || "",
  ip_address: device.ip_address || "",
  port: device.port ?? "",
  source_uri: device.source_uri || "",
  created_at: device.created_at || new Date().toISOString(),
});

const cameraSourceLabel = (device: LogicalDevice) => device.source_uri || [device.protocol, device.ip_address].filter(Boolean).join("://") || device.client_id || "-";

const devicePayload = (device: DeviceModalState) => ({
  name: device.name,
  device_type: device.device_type,
  location: device.location,
  status: device.status,
  client_id: device.client_id,
  ...(device.device_type === "camera"
    ? {
        protocol: device.protocol || "rtsp",
        ip_address: device.ip_address || undefined,
        port: device.port === "" || device.port === undefined ? undefined : Number(device.port),
        source_uri: device.source_uri || undefined,
      }
    : {}),
});

const modelTaskLabel = (task?: string) =>
  ({
    "sensor-forecast": "Prediction",
    "scene-classification": "Scene Classification",
    "person-detection": "Object Detection",
    "face-identification": "Face Identification",
    "anomaly-detection": "Anomaly Detection",
    "scene-caption": "Scene Caption",
  })[task || ""] || task || "Other";

const modelTaskDescriptions: Record<string, string> = {
  Prediction: "Time-series forecasting",
  "Scene Classification": "Vacant / Normal / Crowded / Abnormal",
  "Object Detection": "YOLO detector for people counting",
  "Face Identification": "InsightFace embeddings vs enrolled identities",
  "Anomaly Detection": "DINOv2 kNN distance to normal-frame bank",
  "Scene Caption": "Moondream2 VLM free-text scene description",
};

const normalizeModel = (model: BackendModel): ModelItem => ({
  id: model.model_id,
  name: model.model_id || "-",
  version: model.version || "-",
  task: modelTaskLabel(model.task),
  metric: model.runtime || "-",
  latency: "-",
  hash: model.artifact_uri || model.task || "-",
  status: model.status === "active" ? "active" : "available",
});

const deviceToApi = (device: string) =>
  ({ Light: "lamp", Fan: "fan", Window: "window", Buzzer: "buzzer" })[device] as "lamp" | "fan" | "window" | "buzzer";

const actionToApi = (action: string) =>
  ({ "Turn ON": "on", "Turn OFF": "off", Toggle: "toggle", Open: "open", Close: "close" })[action] as "on" | "off" | "toggle" | "open" | "close";

function Sparkline({ data, color, fill }: { data: number[]; color: string; fill?: string }) {
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const points = data
    .map((value, index) => {
      const x = (index / (data.length - 1)) * 120;
      const y = 3 + (1 - (value - min) / range) * 16;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return h(
    "svg",
    { className: "chart", viewBox: "0 0 120 22", preserveAspectRatio: "none" },
    fill ? h("polygon", { points: `0,22 ${points} 120,22`, fill }) : null,
    h("polyline", { points, fill: "none", stroke: color, strokeWidth: 1.6, strokeLinecap: "round", strokeLinejoin: "round" }),
  );
}

function EnvironmentChart() {
  return h(
    "svg",
    { className: "chart", viewBox: "0 0 600 132", preserveAspectRatio: "none" },
    [0, 1, 2].map((i) => h("line", { key: i, x1: 0, x2: 600, y1: 18 + i * 36, y2: 18 + i * 36, stroke: "var(--c-bordersoft)" })),
    h("polyline", {
      points: series.hum.map((value, index) => `${(index / 23) * 600},${20 + (60 - value) * 3}`).join(" "),
      fill: "none",
      stroke: "#3b5bdb",
      strokeWidth: 2,
      strokeLinecap: "round",
      strokeLinejoin: "round",
    }),
    h("polyline", {
      points: series.temp.map((value, index) => `${(index / 23) * 600},${92 + (26 - value) * 4}`).join(" "),
      fill: "none",
      stroke: "#e08206",
      strokeWidth: 2,
      strokeLinecap: "round",
      strokeLinejoin: "round",
    }),
  );
}

function Sidebar({
  view,
  setView,
  currentUser,
  onOpenSettings,
  onSignOut,
}: {
  view: ViewId;
  setView: (view: ViewId) => void;
  currentUser: UserAccount;
  onOpenSettings: () => void;
  onSignOut: () => void;
}) {
  const initials = currentUser.name
    .split(" ")
    .map((part) => part[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return h(
    "aside",
    { className: "sidebar" },
    h("div", { className: "brand" }, h("div", { className: "brand-mark" }, "LX"), h("div", null, h("div", { className: "brand-title" }, "Smart Lab"), h("div", { className: "brand-subtitle" }, "IoT Monitor & Control"))),
    h(
      "nav",
      { className: "nav" },
      navItems.filter((item) => !item.adminOnly || currentUser.role === "admin").map((item) =>
        h(
          "button",
          { key: item.id, className: `nav-button ${view === item.id ? "active" : ""}`, onClick: () => setView(item.id) },
          h("span", { className: "nav-icon" }, item.icon),
          h("span", { className: "nav-label" }, item.label),
          item.badge ? h("span", { className: "nav-badge" }, item.badge) : null,
        ),
      ),
    ),
    h(
      "div",
      { className: "sidebar-footer" },
      h("div", { className: "live-pill" }, h("span", { className: "dot" }), "Live · ", h("span", { style: { fontFamily: "'JetBrains Mono', monospace" } }, "99.6%"), " uptime"),
      h(
        "button",
        { className: "profile-button", onClick: onOpenSettings },
        h("div", { className: "admin-avatar" }, initials),
        h("div", null, h("div", { className: "profile-name" }, currentUser.name), h("div", { className: "profile-email" }, currentUser.email)),
      ),
      h("button", { className: "signout-button", onClick: onSignOut }, "Sign out"),
    ),
  );
}

function Topbar({ view, themeMode, resolvedTheme, onToggleTheme, onOpenSettings }: { view: ViewId; themeMode: ThemeMode; resolvedTheme: "light" | "dark"; onToggleTheme: () => void; onOpenSettings: () => void }) {
  const [title, sub] = titles[view];
  return h(
    "header",
    { className: "topbar" },
    h("div", null, h("div", { className: "view-title" }, title), h("div", { className: "view-sub" }, sub)),
    h(
      "div",
      { className: "topbar-right" },
      h(
        "div",
        { className: "systems" },
        ["Gateway", "MQTT", "Backend", "SSE", "Storage"].map((name) =>
          h("div", { key: name, className: "system-pill" }, h("span", { className: "dot", style: { background: name === "Storage" ? "#e08206" : "#129d6b", animation: "none" } }), name),
        ),
      ),
      h("button", { className: "icon-action", title: `Theme: ${themeMode}`, onClick: onToggleTheme }, resolvedTheme === "dark" ? "☀" : "☾"),
      h("button", { className: "icon-action", title: "Settings", onClick: onOpenSettings }, h("span", { className: "nav-icon" }, icons.gear)),
      h("div", { className: "divider" }),
      h("div", { className: "clock" }, h("span", { className: "clock-time" }, "14:17:59"), h("span", { className: "clock-date" }, "Wed, Jul 1")),
    ),
  );
}

function KpiCard({ item }: { item: Kpi }) {
  return h(
    "div",
    { className: "card kpi-card" },
    h("div", { className: "kpi-label" }, h("span", { style: { width: 6, height: 6, borderRadius: "50%", background: item.dot } }), item.label),
    h("div", { className: "kpi-value" }, h("span", { className: "kpi-number" }, item.value), h("span", { className: "kpi-unit" }, item.unit)),
    h("div", { style: { height: 22, marginTop: "auto" } }, h(Sparkline, { data: item.data, color: item.color, fill: item.fill })),
    h("div", { className: "kpi-delta", style: { color: item.deltaColor } }, item.delta),
  );
}

const clampNumber = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

const normalizeVisionSettings = (value?: Partial<UserSettingsState["vision"]>): UserSettingsState["vision"] => ({
  preview_fps: clampNumber(Number(value?.preview_fps ?? DEFAULT_VISION_SETTINGS.preview_fps), 1, 30),
  preview_width: clampNumber(Number(value?.preview_width ?? DEFAULT_VISION_SETTINGS.preview_width), 320, 1920),
  inference_hz: clampNumber(Number(value?.inference_hz ?? DEFAULT_VISION_SETTINGS.inference_hz), 0.1, 30),
  face_inference_hz: clampNumber(Number(value?.face_inference_hz ?? DEFAULT_VISION_SETTINGS.face_inference_hz), 0.1, 30),
  person_confidence: clampNumber(Number(value?.person_confidence ?? DEFAULT_VISION_SETTINGS.person_confidence), 0.01, 0.99),
  anomaly_inference_hz: clampNumber(Number(value?.anomaly_inference_hz ?? DEFAULT_VISION_SETTINGS.anomaly_inference_hz), 0.01, 30),
  caption_periodic_seconds: clampNumber(Number(value?.caption_periodic_seconds ?? DEFAULT_VISION_SETTINGS.caption_periodic_seconds), 10, 86_400),
  caption_on_anomaly: typeof value?.caption_on_anomaly === "boolean" ? value.caption_on_anomaly : DEFAULT_VISION_SETTINGS.caption_on_anomaly,
});

const cameraPreviewUrl = (device: LogicalDevice | null, vision: UserSettingsState["vision"]) => {
  if (!device) return "";
  const token = storedAccessToken();
  return buildBackendUrl(API_ROUTES.devicePreview(device.id), {
    query: {
      fps: vision.preview_fps,
      width: vision.preview_width,
      ...(token ? { access_token: token } : {}),
    },
  });
};

const CAMERA_SOURCE_WIDTH = 1920;
const CAMERA_SOURCE_HEIGHT = 1080;

const clampPercent = (value: number) => Math.max(0, Math.min(100, value));

const detectionBoxStyle = (detection: VisionDetectionBox): React.CSSProperties => {
  const [rawX1, rawY1, rawX2, rawY2] = detection.box;
  const isNormalized = Math.max(rawX1, rawY1, rawX2, rawY2) <= 1;
  const widthBase = isNormalized ? 1 : CAMERA_SOURCE_WIDTH;
  const heightBase = isNormalized ? 1 : CAMERA_SOURCE_HEIGHT;
  const x1 = clampPercent((Math.min(rawX1, rawX2) / widthBase) * 100);
  const x2 = clampPercent((Math.max(rawX1, rawX2) / widthBase) * 100);
  const y1 = clampPercent((Math.min(rawY1, rawY2) / heightBase) * 100);
  const y2 = clampPercent((Math.max(rawY1, rawY2) / heightBase) * 100);
  return {
    left: `${x1}%`,
    top: `${y1}%`,
    width: `${Math.max(0, x2 - x1)}%`,
    height: `${Math.max(0, y2 - y1)}%`,
  };
};

function DetectionOverlay({ event }: { event?: VisionTimelineItem }) {
  const detections = event?.detections || [];
  if (detections.length === 0) return null;
  return h(
    "div",
    { className: "vision-overlay", "aria-label": `${detections.length} detection boxes` },
    detections.map((detection, index) => {
      const label = detection.label.toLowerCase();
      const className = `vision-box ${label.includes("face") || label.includes("unknown") || label.includes("cao") || label.includes("yingmo") ? "face-box" : ""}`;
      return (
      h(
        "div",
        { key: `${event?.id || "detection"}-${index}`, className, style: detectionBoxStyle(detection) },
        h("span", { className: "vision-box-tag" }, `${detection.label} ${detection.confidence.toFixed(2)}`),
      )
      );
    }),
  );
}

function CameraPanel({ device, latestEvent, detectionEvent, visionSettings }: { device: LogicalDevice | null; latestEvent?: VisionTimelineItem; detectionEvent?: VisionTimelineItem; visionSettings: UserSettingsState["vision"] }) {
  const previewUrl = cameraPreviewUrl(device, visionSettings);
  const title = device ? `${device.name} · Live` : "Camera · Offline";
  const source = device ? cameraSourceLabel(device) : "no camera registered";
  const people = detectionEvent ? `people=${detectionEvent.peopleCount} · ${detectionEvent.runtime || "runtime?"}` : source;
  return h(
    "div",
    { className: "camera-panel" },
    h(
      "div",
      { className: "panel-header-dark" },
      h("div", { className: "dark-title" }, h("span", { className: "dot", style: { background: previewUrl ? "#dc4444" : "#8a97a8" } }), title, h("span", { className: "tag" }, device?.location || "Unassigned")),
      h("div", { className: "dark-meta" }, h("span", null, "MJPEG"), h("span", null, device?.status || "offline")),
    ),
    h(
      "div",
      { className: "camera-stage" },
      previewUrl ? h("img", { className: "camera-frame", src: previewUrl, alt: `${device?.name || "Camera"} preview` }) : h("div", { className: "camera-grid" }),
      h(DetectionOverlay, { event: detectionEvent }),
      previewUrl ? null : h("div", { className: "scan-line" }),
      h("div", { className: "scene-row" }, h("span", { className: "scene-badge crowded" }, latestEvent?.title || "Preview"), h("span", { className: "mono-chip" }, people)),
      h("div", { className: "vision-label" }, previewUrl ? apiLabel(API_ROUTES.devicePreview(device!.id)) : "Register camera in Devices"),
    ),
  );
}

function EnvironmentCard({ kpis = sampleKpis }: { kpis?: Kpi[] }) {
  const metricValue = (label: string, fallback: string) => {
    const item = kpis.find((entry) => entry.label === label);
    return item ? `${item.value}${item.unit}` : fallback;
  };
  return h(
    "div",
    { className: "chart-card" },
    h("div", { className: "panel-header" }, h("span", { style: { font: "600 12px Inter", color: "var(--c-text)" } }, "Environment · 24h"), h("div", { className: "legend" }, h("span", null, h("i", { style: { background: "#e08206" } }), "Temp"), h("span", null, h("i", { style: { background: "#3b5bdb" } }), "Humidity"))),
    h("div", { className: "chart-area" }, h(EnvironmentChart)),
    h("div", { className: "chart-stats" }, [["Live Temp", metricValue("Live Temp", "24.5C")], ["Live Humidity", metricValue("Live Humidity", "54%")], ["Live Light", metricValue("Live Light", "379")]].map(([label, value]) => h("div", { key: label, className: "chart-stat" }, h("div", { className: "stat-label" }, label), h("div", { className: "stat-value" }, value)))),
  );
}

function ModelColumn() {
  const [prediction, setPrediction] = useState<PredictionMetricBackend | null>(null);
  const [visionModel, setVisionModel] = useState<ModelItem | null>(null);
  const pollTick = usePollTick(30000);
  useEffect(() => {
    let cancelled = false;
    apiRequest<PredictionMetricBackend[]>(API_ROUTES.predictionMetrics, { query: { metric: "temperature" } })
      .then((rows) => {
        if (!cancelled) setPrediction(rows.find((row) => row.is_active) || rows[0] || null);
      })
      .catch(() => {
        if (!cancelled) setPrediction(null);
      });
    apiRequest<BackendModel[]>(API_ROUTES.models)
      .then((items) => {
        if (!cancelled) setVisionModel(items.filter((model) => model.task === "person-detection" && model.status === "active").map(normalizeModel)[0] || null);
      })
      .catch(() => {
        if (!cancelled) setVisionModel(null);
      });
    return () => {
      cancelled = true;
    };
  }, [pollTick]);
  const metric = (label: string, value: string, color = "var(--c-text)") => h("div", null, h("div", { className: "stat-label" }, label), h("div", { className: "stat-value", style: { fontSize: 13, color } }, value));
  return h(
    "div",
    { className: "model-column" },
    prediction
      ? h("div", { className: "card model-card" }, h("div", { className: "model-title" }, "Prediction Model", h("span", { className: "active-badge" }, prediction.is_active ? "ACTIVE" : "LATEST")), h("div", { className: "model-name" }, prediction.model_name), h("div", { className: "model-metrics" }, metric("R²", String(prediction.r2 ?? "-"), "#129d6b"), metric("RMSE", String(prediction.rmse ?? "-")), metric("Latency", `${prediction.inference_latency_ms ?? "-"}ms`)), h("div", { className: "model-note" }, `${prediction.metric} · ${prediction.node_id}`))
      : h("div", { className: "card model-card" }, h("div", { className: "model-title" }, "Prediction Model"), h("div", { className: "model-name" }, "No prediction runs"), h("div", { className: "model-note" }, `GET ${apiLabel(API_ROUTES.predictionMetrics)}`)),
    visionModel
      ? h("div", { className: "card model-card" }, h("div", { className: "model-title" }, "Vision Model", h("span", { className: "active-badge green" }, "ACTIVE")), h("div", { className: "model-name" }, visionModel.name), h("div", { className: "model-metrics" }, metric("Version", visionModel.version, "#129d6b"), metric("Runtime", visionModel.metric)), h("div", { className: "model-note" }, visionModel.hash))
      : h("div", { className: "card model-card" }, h("div", { className: "model-title" }, "Vision Model"), h("div", { className: "model-name" }, "No active detector"), h("div", { className: "model-note" }, `GET ${apiLabel(API_ROUTES.models)}`)),
  );
}

function StatusPill({ state, children }: { state: string; children?: ReactNode }) {
  const color = state === "critical" || state === "failed" || state === "offline" ? "#dc4444" : state === "warning" || state === "pending" || state === "fault" ? "#e08206" : state === "sent" ? "#3b5bdb" : "#129d6b";
  const bg = state === "critical" || state === "failed" || state === "offline" ? "#fdecec" : state === "warning" || state === "pending" || state === "fault" ? "#fdf1e0" : state === "sent" ? "#eef1fd" : "#e6f6ef";
  return h("span", { className: "status-pill", style: { color, background: bg } }, children);
}

function BottomLists() {
  const [dashboardAlerts, setDashboardAlerts] = useState<AlertItem[]>([]);
  const [dashboardCommands, setDashboardCommands] = useState<CommandItem[]>([]);
  const [alertState, setAlertState] = useState<"loading" | "live" | "sample">("loading");
  const [commandState, setCommandState] = useState<"loading" | "live" | "sample">("loading");
  const pollTick = usePollTick(5000);

  useEffect(() => {
    let cancelled = false;
    apiRequest<DashboardAlertBackend[]>(API_ROUTES.alerts, { query: { status: "active" } })
      .then((items) => {
        if (cancelled) return;
        setDashboardAlerts(createDashboardAlertItems(items).slice(0, 5));
        setAlertState("live");
      })
      .catch(() => {
        if (cancelled) return;
        setDashboardAlerts(alerts);
        setAlertState("sample");
      });

    apiRequest<DashboardCommandBackend[]>(API_ROUTES.controlCommands)
      .then((items) => {
        if (cancelled) return;
        setDashboardCommands(createDashboardCommandItems(items).slice(0, 5));
        setCommandState("live");
      })
      .catch(() => {
        if (cancelled) return;
        setDashboardCommands(commands);
        setCommandState("sample");
      });

    return () => {
      cancelled = true;
    };
  }, [pollTick]);

  return h(
    "div",
    { className: "bottom-grid" },
    h(
      "div",
      { className: "card" },
      h("div", { className: "panel-header" }, h("span", { style: { font: "600 12px Inter" } }, "Latest Alerts"), h("span", { style: { font: "500 11px Inter", color: "#3b5bdb" } }, `${apiLabel(API_ROUTES.alerts)} · ${alertState}`)),
      dashboardAlerts.length === 0
        ? h("div", { className: "list-row" }, h("div", { className: "list-sub" }, alertState === "loading" ? "Loading alerts" : "No active alerts"))
        : dashboardAlerts.map((item) => h("div", { key: `${item.title}-${item.source}-${item.ago}`, className: "list-row" }, h("span", { style: { width: 8, height: 8, borderRadius: "50%", background: item.severity === "critical" ? "#dc4444" : item.severity === "warning" ? "#e08206" : "#3b5bdb" } }), h("div", { style: { flex: 1 } }, h("div", { className: "list-title" }, item.title), h("div", { className: "list-sub" }, `${item.source} · ${item.detail}`)), h(StatusPill, { state: item.severity }, item.status), h("span", { style: { font: "500 10px 'JetBrains Mono'", color: "#b0b7c0" } }, item.ago))),
    ),
    h(
      "div",
      { className: "card" },
      h("div", { className: "panel-header" }, h("span", { style: { font: "600 12px Inter" } }, "Recent Command ACKs"), h("span", { style: { font: "500 11px Inter", color: "#3b5bdb" } }, `${apiLabel(API_ROUTES.controlCommands)} · ${commandState}`)),
      dashboardCommands.length === 0
        ? h("div", { className: "list-row" }, h("div", { className: "list-sub" }, commandState === "loading" ? "Loading commands" : "No recent commands"))
        : dashboardCommands.map((item) => h("div", { key: `${item.command_id || item.node}-${item.action}`, className: "list-row" }, h("span", { className: "command-node-cell" }, item.node), h("div", { style: { flex: 1 } }, h("div", { className: "list-title" }, item.action), h("div", { className: "list-sub" }, item.device)), h(StatusPill, { state: item.state }, item.state.toUpperCase()), h("span", { style: { font: "500 10px 'JetBrains Mono'", color: "#b0b7c0" } }, item.ago))),
    ),
  );
}

function FilterButton({ active, onClick, children }: { active: boolean; onClick: () => void; children?: ReactNode }) {
  return h("button", { className: `filter-button ${active ? "active" : ""}`, onClick }, children);
}

function SegmentButton({ active, onClick, children }: { active: boolean; onClick: () => void; children?: ReactNode }) {
  return h("button", { className: `segment-button ${active ? "active" : ""}`, onClick }, children);
}

function DataHeader({ columns, className }: { columns: string[]; className: string }) {
  return h(
    "div",
    { className: `data-row data-head ${className}` },
    columns.map((column) => h("span", { key: column }, column)),
  );
}

function StatusTag({ state, children }: { state: string; children?: ReactNode }) {
  return h(StatusPill, { state }, children);
}

function chartWidthFor(values: number[]) {
  const spacing = values.length > 1000 ? 4 : values.length > 360 ? 8 : 18;
  return Math.max(820, Math.min(18_000, values.length * spacing));
}

const chartHeight = 220;
const chartTop = 14;
const chartBottom = 28;
const chartPlotHeight = chartHeight - chartTop - chartBottom;

function niceNumber(value: number, round: boolean) {
  const exponent = Math.floor(Math.log10(Math.max(value, 0.000001)));
  const fraction = value / 10 ** exponent;
  let niceFraction = 10;
  if (round) {
    if (fraction < 1.5) niceFraction = 1;
    else if (fraction < 3) niceFraction = 2;
    else if (fraction < 7) niceFraction = 5;
  } else if (fraction <= 1) niceFraction = 1;
  else if (fraction <= 2) niceFraction = 2;
  else if (fraction <= 5) niceFraction = 5;
  return niceFraction * 10 ** exponent;
}

function chartScale(values: number[]) {
  const finite = values.filter(Number.isFinite);
  const rawMin = finite.length > 0 ? Math.min(...finite) : 0;
  const rawMax = finite.length > 0 ? Math.max(...finite) : 1;
  const fallbackRange = Math.max(Math.abs(rawMin) * 0.01, 0.5);
  const rawRange = rawMax - rawMin || fallbackRange;
  const step = niceNumber(rawRange / 4, true);
  const min = Math.floor((rawMin - step * 0.25) / step) * step;
  const max = Math.ceil((rawMax + step * 0.25) / step) * step;
  const ticks = Array.from({ length: Math.round((max - min) / step) + 1 }, (_, index) => min + index * step);
  return { min, max, step, ticks };
}

function formatTickValue(value: number, step: number, unit?: string) {
  const decimals = step < 0.1 ? 2 : step < 1 ? 1 : 0;
  return `${value.toFixed(decimals)}${unit ? ` ${unit}` : ""}`;
}

function yForValue(value: number, min: number, max: number) {
  const range = max - min || 1;
  return chartTop + (1 - (value - min) / range) * chartPlotHeight;
}

function visibleIndexRange(pointCount: number, width: number, viewport: ChartViewport) {
  if (pointCount < 2) return { start: 0, end: Math.max(0, pointCount - 1) };
  const clientWidth = viewport.clientWidth || 980;
  const scrollLeft = viewport.scrollLeft || Math.max(0, width - clientWidth);
  const start = Math.max(0, Math.floor((scrollLeft / width) * (pointCount - 1)) - 3);
  const end = Math.min(pointCount - 1, Math.ceil(((scrollLeft + clientWidth) / width) * (pointCount - 1)) + 3);
  return { start, end };
}

function xForIndex(index: number, pointCount: number, width: number) {
  return pointCount < 2 ? 0 : (index / (pointCount - 1)) * width;
}

// Places each point by its actual elapsed time so a gap in the data (sensor
// offline, downsample bucket with no rows) shows as a gap on the axis
// instead of being silently compressed to even index spacing.
function xPositionsForData(data: TrendPoint[], width: number): number[] {
  if (data.length < 2) return data.map(() => 0);
  const times = data.map((point) => new Date(point.label || "").getTime());
  const hasValidTimes = times.every((time) => Number.isFinite(time));
  const span = hasValidTimes ? times[times.length - 1] - times[0] : 0;
  if (!hasValidTimes || span <= 0) return data.map((_, index) => xForIndex(index, data.length, width));
  return times.map((time) => ((time - times[0]) / span) * width);
}

// Same idea, but anchored to the caller's actual fetch window (rangeStartMs
// .. rangeEndMs) instead of the returned data's own min/max timestamp, so a
// point's x position reflects real elapsed time within that window even when
// the data doesn't reach all the way to either edge.
function xPositionsForRange(data: TrendPoint[], rangeStartMs: number | undefined, rangeEndMs: number | undefined, width: number): number[] {
  const span = rangeStartMs !== undefined && rangeEndMs !== undefined ? rangeEndMs - rangeStartMs : 0;
  if (!span || span <= 0) return xPositionsForData(data, width);
  return data.map((point) => {
    const time = new Date(point.label || "").getTime();
    return Number.isFinite(time) ? ((time - rangeStartMs!) / span) * width : 0;
  });
}

function visibleRangeForPositions(positions: number[], width: number, viewport: ChartViewport) {
  const count = positions.length;
  if (count < 2) return { start: 0, end: Math.max(0, count - 1) };
  const clientWidth = viewport.clientWidth || 980;
  const scrollLeft = viewport.scrollLeft || Math.max(0, width - clientWidth);
  const leftEdge = scrollLeft;
  const rightEdge = scrollLeft + clientWidth;
  let start = 0;
  while (start < count - 1 && positions[start] < leftEdge) start++;
  let end = count - 1;
  while (end > 0 && positions[end] > rightEdge) end--;
  return { start: Math.max(0, start - 3), end: Math.min(count - 1, end + 3) };
}

function formatAxisTime(label: string | undefined, index: number) {
  if (!label) return `#${index + 1}`;
  const date = new Date(label);
  if (Number.isNaN(date.getTime())) return label;
  return date.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function xTicksFor(points: TrendPoint[], visibleStart: number, visibleEnd: number) {
  const count = Math.max(1, visibleEnd - visibleStart);
  const tickSlots = Math.min(5, count + 1);
  return Array.from({ length: tickSlots }, (_, index) => {
    const ratio = tickSlots === 1 ? 0 : index / (tickSlots - 1);
    return Math.round(visibleStart + ratio * count);
  }).filter((item, index, arr) => item >= 0 && item < points.length && arr.indexOf(item) === index);
}

// Anchors ticks to fixed, evenly-spaced pixel slots within the current
// viewport (not to data indices), so labels never bunch up when point
// density varies and instead slide smoothly — each slot just swaps in
// whichever data point is nearest as the user scrolls.
function xTicksForViewport(positions: number[], viewport: ChartViewport, tickCount = 5): { x: number; index: number }[] {
  if (positions.length === 0) return [];
  const clientWidth = viewport.clientWidth || 980;
  const scrollLeft = viewport.scrollLeft || 0;
  const ticks: { x: number; index: number }[] = [];
  for (let slot = 0; slot < tickCount; slot++) {
    const x = scrollLeft + (slot / (tickCount - 1)) * clientWidth;
    let lo = 0;
    let hi = positions.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (positions[mid] < x) lo = mid + 1;
      else hi = mid;
    }
    ticks.push({ x, index: lo });
  }
  return ticks;
}

function YAxis({ ticks, min, max, step, unit }: { ticks: number[]; min: number; max: number; step: number; unit?: string }) {
  return h(
    "div",
    { className: "chart-y-axis" },
    ticks.map((tick) =>
      h(
        "span",
        {
          key: tick,
          className: "chart-y-tick",
          style: { top: `${(yForValue(tick, min, max) / chartHeight) * 100}%` },
        },
        formatTickValue(tick, step, unit),
      ),
    ),
  );
}

function ChartScroller({
  width,
  ratio,
  resetKey,
  renderAxis,
  renderChart,
}: {
  width?: number;
  ratio?: number;
  resetKey: string;
  renderAxis: (viewport: ChartViewport, width: number) => ReactNode;
  renderChart: (viewport: ChartViewport, width: number) => ReactNode;
}) {
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const [containerWidth, setContainerWidth] = useState(980);
  // `ratio` mode (used for time-range history charts): canvas is `ratio`x the
  // measured viewport so scrolling left reveals more loaded history without
  // re-fetching. `width` mode (fixed-size charts like prediction comparisons)
  // keeps the old fixed-width behavior.
  const resolvedWidth = ratio ? Math.max(containerWidth, Math.round(containerWidth * ratio)) : width || containerWidth;
  const [viewport, setViewport] = useState<ChartViewport>({ scrollLeft: 0, clientWidth: 0, scrollWidth: resolvedWidth });

  // Track the viewport's own box size continuously; this must stay decoupled
  // from canvas width changes below it, or resizing the canvas would falsely
  // retrigger this observer.
  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const measure = () => setContainerWidth(scroller.clientWidth || 980);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(scroller);
    return () => observer.disconnect();
  }, []);

  // Snap to the newest data whenever the loaded window changes (resetKey) or
  // the canvas is resized — runs after the DOM has the new canvas width
  // committed, so scrollWidth here is always current (not stale).
  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    scroller.scrollLeft = Math.max(0, scroller.scrollWidth - scroller.clientWidth);
    setViewport({ scrollLeft: scroller.scrollLeft, clientWidth: scroller.clientWidth, scrollWidth: scroller.scrollWidth });
  }, [resetKey, resolvedWidth]);

  useEffect(() => () => {
    if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
  }, []);

  return h(
    "div",
    { className: "chart-frame" },
    renderAxis(viewport, resolvedWidth),
    h(
      "div",
      {
        ref: scrollerRef,
        className: "chart-scroll",
        onScroll: (event: React.UIEvent<HTMLDivElement>) => {
          // Coalesce to one state update per animation frame — native scroll
          // events can fire far more often than the browser can paint, and
          // each update here re-renders the SVG, so without this the chart
          // stutters under trackpad/momentum scrolling.
          const target = event.currentTarget;
          if (rafRef.current !== null) return;
          rafRef.current = requestAnimationFrame(() => {
            rafRef.current = null;
            setViewport({ scrollLeft: target.scrollLeft, clientWidth: target.clientWidth, scrollWidth: target.scrollWidth });
          });
        },
      },
      h("div", { className: "chart-canvas", style: { width: `${resolvedWidth}px` } }, renderChart(viewport, resolvedWidth)),
    ),
  );
}

function TimeRangeControls({
  rangeId,
  windowEnd,
  onRangeChange,
  onShift,
  onLive,
}: {
  rangeId: TimeRangeId;
  windowEnd: Date | null;
  onRangeChange: (rangeId: TimeRangeId) => void;
  onShift: (direction: -1 | 1) => void;
  onLive: () => void;
}) {
  return h(
    "div",
    { className: "range-controls" },
    h("div", { className: "range-buttons" }, timeRangeOptions.map((option) => h(FilterButton, { key: option.id, active: rangeId === option.id, onClick: () => onRangeChange(option.id) }, option.label))),
    h(
      "div",
      { className: "range-pan" },
      h("button", { className: "icon-button", onClick: () => onShift(-1), title: "Older window" }, "‹"),
      h("button", { className: "icon-button", onClick: () => onShift(1), title: "Newer window", disabled: !windowEnd }, "›"),
      h("button", { className: "mini-action", onClick: onLive, disabled: !windowEnd }, "Live"),
      h("span", { className: "range-label" }, rangeLabel(rangeId, windowEnd)),
    ),
  );
}

function MetricTrend({
  points,
  color = "#3b5bdb",
  unit,
  rangeStartMs,
  rangeEndMs,
  ratio,
  viewKey,
}: {
  points?: TrendPoint[];
  color?: string;
  unit?: string;
  rangeStartMs?: number;
  rangeEndMs?: number;
  ratio?: number;
  viewKey?: string;
}) {
  const data: TrendPoint[] = points ?? [];
  if (data.length < 2) return h("div", { className: "empty-chart" }, "No readings match these filters");
  // Reset (snap-to-latest) only when the caller says the *view* changed
  // (range/filter/pan) — not on every background data refresh, which would
  // otherwise yank the chart back to the right while the user is scrolling
  // through history.
  const resetKey = viewKey ?? `${data.length}-${data[data.length - 1]?.label || ""}-${data[data.length - 1]?.value}`;
  // Positions only depend on canvas width, which only changes on resize (not
  // on every scroll tick) — cache them so scrolling doesn't recompute an
  // O(n) array on every animation frame.
  let cachedWidth = -1;
  let cachedPositions: number[] = [];
  const positionsFor = (width: number) => {
    if (width !== cachedWidth) {
      cachedPositions = xPositionsForRange(data, rangeStartMs, rangeEndMs, width);
      cachedWidth = width;
    }
    return cachedPositions;
  };
  return h(
    ChartScroller,
    {
      ratio,
      resetKey,
      renderAxis: (viewport, width) => {
        const xPositions = positionsFor(width);
        const { start, end } = visibleRangeForPositions(xPositions, width, viewport);
        const scale = chartScale(data.slice(start, end + 1).map((point) => point.value));
        return h(YAxis, { ...scale, unit });
      },
      renderChart: (viewport, width) => {
        const xPositions = positionsFor(width);
        const { start, end } = visibleRangeForPositions(xPositions, width, viewport);
        const scale = chartScale(data.slice(start, end + 1).map((point) => point.value));
        // Only build SVG elements for the visible (+padding) window — with
        // history charts now holding thousands of points, mapping the full
        // array on every scroll frame is what made scrolling choppy.
        const visibleData = data.slice(start, end + 1);
        const visiblePositions = xPositions.slice(start, end + 1);
        const linePoints = visibleData.map((point, index) => `${visiblePositions[index].toFixed(1)},${yForValue(point.value, scale.min, scale.max).toFixed(1)}`).join(" ");
        const leftX = visiblePositions[0] ?? 0;
        const rightX = visiblePositions[visiblePositions.length - 1] ?? width;
        const fillPoints = `${leftX},${chartHeight - chartBottom} ${linePoints} ${rightX},${chartHeight - chartBottom}`;
        const dotEvery = Math.max(1, Math.ceil(visibleData.length / 80));
        const xTicks = xTicksForViewport(xPositions, viewport);
        return h(
          "svg",
          { className: "chart", viewBox: `0 0 ${width} ${chartHeight}`, preserveAspectRatio: "none" },
          scale.ticks.map((tick) => h("line", { key: `y${tick}`, x1: 0, x2: width, y1: yForValue(tick, scale.min, scale.max), y2: yForValue(tick, scale.min, scale.max), stroke: "var(--c-bordersoft)" })),
          xTicks.map((tick, tickIndex) =>
            // A big gap in the underlying data (sensor offline for a stretch)
            // can make several evenly-spaced slots resolve to the same
            // nearest point — only label the first of any run of repeats.
            tickIndex > 0 && xTicks[tickIndex - 1].index === tick.index
              ? null
              : h("text", { key: `x${tickIndex}`, className: "chart-x-tick", x: tick.x, y: chartHeight - 6, textAnchor: tickIndex === 0 ? "start" : tickIndex === xTicks.length - 1 ? "end" : "middle" }, formatAxisTime(data[tick.index]?.label, tick.index)),
          ),
          h("polygon", { points: fillPoints, fill: "rgba(59,91,219,.07)" }),
          h("polyline", { points: linePoints, fill: "none", stroke: color, strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round" }),
          visibleData.map((point, index) => index % dotEvery === 0 ? h("circle", { key: `d${start + index}`, cx: visiblePositions[index], cy: yForValue(point.value, scale.min, scale.max), r: 2.5, fill: color }) : null),
        );
      },
    },
  );
}

function ComparisonTrend({ actual, predicted, labels = [], color = "#129d6b", unit }: { actual: number[]; predicted: number[]; labels?: string[]; color?: string; unit?: string }) {
  const values = [...actual, ...predicted].filter(Number.isFinite);
  if (actual.length < 2 || values.length < 2) return h("div", { className: "empty-chart" }, "No readings match this range");
  const width = chartWidthFor(actual);
  const points = actual.map((value, index) => ({ value, label: labels[index] }));
  const resetKey = `${actual.length}-${labels[labels.length - 1] || ""}-${actual[actual.length - 1]}`;
  const line = (seriesValues: number[], scale: ReturnType<typeof chartScale>) =>
    seriesValues.map((value, index) => `${xForIndex(index, seriesValues.length, width).toFixed(1)},${yForValue(value, scale.min, scale.max).toFixed(1)}`).join(" ");
  return h(
    ChartScroller,
    {
      width,
      resetKey,
      renderAxis: (viewport) => {
        const { start, end } = visibleIndexRange(actual.length, width, viewport);
        const scale = chartScale([...actual.slice(start, end + 1), ...predicted.slice(start, end + 1)]);
        return h(YAxis, { ...scale, unit });
      },
      renderChart: (viewport) => {
        const { start, end } = visibleIndexRange(actual.length, width, viewport);
        const scale = chartScale([...actual.slice(start, end + 1), ...predicted.slice(start, end + 1)]);
        const xTicks = xTicksFor(points, start, end);
        return h(
          "svg",
          { className: "chart", viewBox: `0 0 ${width} ${chartHeight}`, preserveAspectRatio: "none" },
          scale.ticks.map((tick) => h("line", { key: `y${tick}`, x1: 0, x2: width, y1: yForValue(tick, scale.min, scale.max), y2: yForValue(tick, scale.min, scale.max), stroke: "var(--c-bordersoft)" })),
          xTicks.map((index, tickIndex) => h("text", { key: `x${index}`, className: "chart-x-tick", x: xForIndex(index, actual.length, width), y: chartHeight - 6, textAnchor: tickIndex === 0 ? "start" : tickIndex === xTicks.length - 1 ? "end" : "middle" }, formatAxisTime(points[index]?.label, index))),
          h("polyline", { points: line(actual, scale), fill: "none", stroke: "#12151a", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round" }),
          h("polyline", { points: line(predicted, scale), fill: "none", stroke: color, strokeWidth: 2, strokeDasharray: "5 4", strokeLinecap: "round", strokeLinejoin: "round" }),
        );
      },
    },
  );
}

function PredictionForecastTrend({
  actual,
  historical,
  future,
  horizon,
  metric,
  color = "#129d6b",
}: {
  actual: TrendPoint[];
  historical: TrendPoint[];
  future: TrendPoint[];
  horizon: ReturnType<typeof predictionHorizon>;
  metric: ReadingMetric;
  color?: string;
}) {
  const values = [...actual, ...historical, ...future].map((point) => point.value).filter(Number.isFinite);
  if (actual.length < 2 || values.length < 2) return h("div", { className: "empty-chart" }, "No real readings match this prediction range");
  const width = 900;
  const scale = chartScale(values);
  const boundaryX = width * 0.8;
  // Anchor "now" to the last actual reading instead of the wall clock: the
  // backend's forecast is offset from that same reading, so if the sensor
  // has stalled, using Date.now() here would strand the actual line and the
  // forecast curve short of the boundary/right edge instead of meeting them.
  const latestActualMs = actual.reduce((max, point) => {
    const t = new Date(point.label || "").getTime();
    return Number.isFinite(t) ? Math.max(max, t) : max;
  }, 0);
  const nowMs = latestActualMs > 0 ? latestActualMs : Date.now();
  const historyStartMs = nowMs - horizon.minutes * horizon.historyMultiplier * 60_000;
  const futureEndMs = nowMs + horizon.minutes * 60_000;
  const xForTime = (label: string | undefined) => {
    const time = new Date(label || "").getTime();
    if (!Number.isFinite(time)) return Number.NaN;
    if (time <= nowMs) {
      return Math.max(0, Math.min(boundaryX, ((time - historyStartMs) / Math.max(1, nowMs - historyStartMs)) * boundaryX));
    }
    return Math.max(boundaryX, Math.min(width, boundaryX + ((time - nowMs) / Math.max(1, futureEndMs - nowMs)) * (width - boundaryX)));
  };
  const path = (points: TrendPoint[]) =>
    points
      .map((point) => [xForTime(point.label), yForValue(point.value, scale.min, scale.max)])
      .filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y))
      .map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`)
      .join(" ");
  const actualPath = path(actual);
  const historicalPath = path(historical);
  const futurePath = path(future);
  const staleMinutes = Math.round((Date.now() - nowMs) / 60_000);
  // Keep this short: it sits in a narrow gap between the history-start and
  // future-end tick labels, so a full timestamp here overlaps its neighbor.
  const nowLabel = staleMinutes > 3 ? `now (stale ${staleMinutes}m)` : "now";
  const tickLabels = [
    { x: 0, text: formatAxisTime(new Date(historyStartMs).toISOString(), 0), anchor: "start" },
    { x: boundaryX, text: nowLabel, anchor: "middle" },
    { x: width, text: formatAxisTime(new Date(futureEndMs).toISOString(), actual.length + future.length), anchor: "end" },
  ];
  return h(
    "div",
    { className: "chart-frame prediction-chart-frame" },
    h(YAxis, { ...scale, unit: readingUnit(metric) }),
    h(
      "div",
      { className: "chart-canvas prediction-chart-canvas" },
      h(
        "svg",
        { className: "chart", viewBox: `0 0 ${width} ${chartHeight}`, preserveAspectRatio: "none" },
        h("rect", { x: boundaryX, y: 0, width: width - boundaryX, height: chartHeight, fill: "rgba(18, 157, 107, 0.07)" }),
        scale.ticks.map((tick) => h("line", { key: `y${tick}`, x1: 0, x2: width, y1: yForValue(tick, scale.min, scale.max), y2: yForValue(tick, scale.min, scale.max), stroke: "var(--c-bordersoft)" })),
        h("line", { x1: boundaryX, x2: boundaryX, y1: chartTop, y2: chartHeight - chartBottom, stroke: "#9aa4b2", strokeDasharray: "4 4" }),
        tickLabels.map((tick) => h("text", { key: tick.text, className: "chart-x-tick", x: tick.x, y: chartHeight - 6, textAnchor: tick.anchor }, tick.text)),
        actualPath ? h("polyline", { points: actualPath, fill: "none", stroke: "var(--c-text)", strokeWidth: 2.2, strokeLinecap: "round", strokeLinejoin: "round" }) : null,
        historicalPath ? h("polyline", { points: historicalPath, fill: "none", stroke: color, strokeWidth: 2, strokeDasharray: "5 4", strokeLinecap: "round", strokeLinejoin: "round" }) : null,
        futurePath ? h("polyline", { points: futurePath, fill: "none", stroke: color, strokeWidth: 2.4, strokeLinecap: "round", strokeLinejoin: "round" }) : null,
        future.map((point, index) => {
          // With a dense forecast curve, only mark the endpoint; sparse
          // forecasts (legacy single-point payloads) keep per-point dots.
          if (future.length > 4 && index !== future.length - 1) return null;
          const x = xForTime(point.label);
          return Number.isFinite(x)
            ? h("circle", { key: `future-${index}`, cx: x, cy: yForValue(point.value, scale.min, scale.max), r: 3.8, fill: color })
            : null;
        }),
      ),
    ),
  );
}

function DeviceModal({ modal, onChange, onClose, onSave }: { modal: DeviceModalState; onChange: (next: DeviceModalState) => void; onClose: () => void; onSave: () => void }) {
  const deviceTypes = ["sensor_node", "camera", "relay", "fan", "lamp", "buzzer", "gateway_client"];
  const cameraProtocols = ["rtsp", "http", "https", "file"];
  const disabled = !modal.name || !modal.device_type;
  const isCamera = modal.device_type === "camera";
  return h(
    "div",
    { className: "modal-backdrop", onClick: onClose },
    h(
      "div",
      { className: "modal-panel", onClick: (event: MouseEvent) => event.stopPropagation() },
      h("div", { className: "modal-title" }, modal._isNew ? `New device (${apiLabel(API_ROUTES.devices)})` : `Edit device (${apiLabel(API_ROUTES.device("{deviceId}"))})`),
      h(
        "div",
        { className: "modal-body" },
        h("label", { className: "field" }, h("span", null, "name"), h("input", { value: modal.name, onChange: (event: ChangeEvent<HTMLInputElement>) => onChange({ ...modal, name: event.target.value }) })),
        h("div", null, h("span", { className: "field-label" }, "device_type"), h("div", { className: "choice-row" }, deviceTypes.map((type) => h(FilterButton, { key: type, active: modal.device_type === type, onClick: () => onChange({ ...modal, device_type: type, protocol: type === "camera" ? modal.protocol || "rtsp" : modal.protocol }) }, type)))),
        h("label", { className: "field" }, h("span", null, "location"), h("input", { value: modal.location, onChange: (event: ChangeEvent<HTMLInputElement>) => onChange({ ...modal, location: event.target.value }) })),
        h("label", { className: "field" }, h("span", null, "client_id"), h("input", { className: "mono-input", value: modal.client_id, onChange: (event: ChangeEvent<HTMLInputElement>) => onChange({ ...modal, client_id: event.target.value }) })),
        isCamera
          ? h(
              "div",
              { className: "camera-source-fields" },
              h("div", null, h("span", { className: "field-label" }, "protocol"), h("div", { className: "choice-row" }, cameraProtocols.map((protocol) => h(FilterButton, { key: protocol, active: (modal.protocol || "rtsp") === protocol, onClick: () => onChange({ ...modal, protocol }) }, protocol)))),
              h("label", { className: "field" }, h("span", null, "ip_address"), h("input", { className: "mono-input", value: modal.ip_address || "", placeholder: "192.0.2.72", onChange: (event: ChangeEvent<HTMLInputElement>) => onChange({ ...modal, ip_address: event.target.value }) })),
              h("label", { className: "field" }, h("span", null, "port"), h("input", { className: "mono-input", value: modal.port ?? "", placeholder: "554", onChange: (event: ChangeEvent<HTMLInputElement>) => onChange({ ...modal, port: event.target.value }) })),
              h("label", { className: "field" }, h("span", null, "source_uri (optional override)"), h("input", { className: "mono-input", value: modal.source_uri || "", placeholder: "rtsp://192.0.2.72", onChange: (event: ChangeEvent<HTMLInputElement>) => onChange({ ...modal, source_uri: event.target.value }) })),
            )
          : null,
        h("div", { className: "modal-status-row" }, h("span", { className: "field-label" }, "status"), h("button", { className: `status-toggle ${modal.status}`, onClick: () => onChange({ ...modal, status: modal.status === "online" ? "offline" : "online" }) }, modal.status)),
      ),
      h("div", { className: "modal-actions" }, h("button", { className: "secondary-action", onClick: onClose }, "Cancel"), h("button", { className: "primary-action", disabled, onClick: onSave }, "Save")),
    ),
  );
}

function NodeModal({ modal, onChange, onClose, onSave }: { modal: NodeModalState; onChange: (next: NodeModalState) => void; onClose: () => void; onSave: () => void }) {
  const roles: SensorNodeRow["role"][] = ["sensor", "router", "sink", "actuator"];
  const disabled = !modal.node_id || !modal.gateway_id;
  return h(
    "div",
    { className: "modal-backdrop", onClick: onClose },
    h(
      "div",
      { className: "modal-panel", onClick: (event: MouseEvent) => event.stopPropagation() },
      h("div", { className: "modal-title" }, "Register sensor node"),
      h(
        "div",
        { className: "modal-body" },
        h("label", { className: "field" }, h("span", null, "node_id"), h("input", { className: "mono-input", value: modal.node_id, placeholder: "e.g. N09", onChange: (event: ChangeEvent<HTMLInputElement>) => onChange({ ...modal, node_id: event.target.value }) })),
        h("div", null, h("span", { className: "field-label" }, "gateway_id"), h("div", { className: "choice-row" }, gatewayIds.map((id) => h(FilterButton, { key: id, active: modal.gateway_id === id, onClick: () => onChange({ ...modal, gateway_id: id }) }, id)))),
        h("label", { className: "field" }, h("span", null, "parent_node_id (optional)"), h("input", { className: "mono-input", value: modal.parent_node_id === "-" ? "" : modal.parent_node_id, onChange: (event: ChangeEvent<HTMLInputElement>) => onChange({ ...modal, parent_node_id: event.target.value || "-" }) })),
        h("div", null, h("span", { className: "field-label" }, "role"), h("div", { className: "choice-row" }, roles.map((role) => h(FilterButton, { key: role, active: modal.role === role, onClick: () => onChange({ ...modal, role }) }, role)))),
        h("label", { className: "field" }, h("span", null, "area"), h("input", { value: modal.area, onChange: (event: ChangeEvent<HTMLInputElement>) => onChange({ ...modal, area: event.target.value }) })),
        h("label", { className: "field" }, h("span", null, "node ip (optional / legacy Wi-Fi)"), h("input", { className: "mono-input", value: modal.ip_address === "-" ? "" : modal.ip_address, placeholder: "e.g. 192.0.2.57", onChange: (event: ChangeEvent<HTMLInputElement>) => onChange({ ...modal, ip_address: event.target.value }) })),
      ),
      h("div", { className: "modal-actions" }, h("button", { className: "secondary-action", onClick: onClose }, "Cancel"), h("button", { className: "primary-action", disabled, onClick: onSave }, "Register")),
    ),
  );
}

function DevicesPage() {
  const [tab, setTab] = useState<DeviceTab>("devices");
  const [deviceFilter, setDeviceFilter] = useState<StatusFilter>("all");
  const [nodeFilter, setNodeFilter] = useState<NodeStatusFilter>("all");
  const [devices, setDevices] = useState(initialDevices);
  const [nodes, setNodes] = useState(initialNodes);
  const [deviceLoadState, setDeviceLoadState] = useState<"loading" | "live" | "sample">("loading");
  const [nodeLoadState, setNodeLoadState] = useState<"loading" | "live" | "sample">("loading");
  const [deviceError, setDeviceError] = useState("");
  const [deviceModal, setDeviceModal] = useState<DeviceModalState | null>(null);
  const [nodeModal, setNodeModal] = useState<NodeModalState | null>(null);
  const [probeState, setProbeState] = useState<Record<number, string>>({});
  const filteredDevices = devices.filter((device) => deviceFilter === "all" || device.status === deviceFilter);
  const filteredNodes = nodes.filter((node) => nodeFilter === "all" || node.status === nodeFilter);
  const pollTick = usePollTick(5000);

  useEffect(() => {
    let cancelled = false;
    setDeviceLoadState((current) => (current === "live" ? current : "loading"));
    apiRequest<BackendDevice[]>(API_ROUTES.devices)
      .then((items) => {
        if (cancelled) return;
        setDevices(items.map(normalizeLogicalDevice));
        setDeviceLoadState("live");
        setDeviceError("");
      })
      .catch(() => {
        if (cancelled) return;
        setDevices(initialDevices);
        setDeviceLoadState("sample");
      });
    return () => {
      cancelled = true;
    };
  }, [pollTick]);

  useEffect(() => {
    let cancelled = false;
    setNodeLoadState((current) => (current === "live" ? current : "loading"));
    apiRequest<BackendSensorNode[]>(API_ROUTES.nodes)
      .then((items) => {
        if (cancelled) return;
        setNodes(items.map(normalizeNode));
        setNodeLoadState("live");
      })
      .catch(() => {
        if (cancelled) return;
        setNodes(initialNodes);
        setNodeLoadState("sample");
      });
    return () => {
      cancelled = true;
    };
  }, [pollTick]);

  const saveDevice = async () => {
    if (!deviceModal) return;
    setDeviceError("");
    try {
      const saved = deviceModal._isNew
        ? await apiRequest<BackendDevice>(API_ROUTES.devices, {
            method: "POST",
            body: JSON.stringify(devicePayload(deviceModal)),
          })
        : await apiRequest<BackendDevice>(API_ROUTES.device(deviceModal.id), {
            method: "PUT",
            body: JSON.stringify(devicePayload(deviceModal)),
          });
      const next = normalizeLogicalDevice(saved);
      setDevices((current) => (deviceModal._isNew ? [next, ...current] : current.map((device) => (device.id === next.id ? next : device))));
      setDeviceLoadState("live");
    } catch (error) {
      setDeviceError(error instanceof Error ? error.message : "save failed");
      return;
    }
    setDeviceModal(null);
  };

  const disableDevice = async (id: number) => {
    const previous = devices.find((device) => device.id === id);
    setDevices((current) => current.map((device) => (device.id === id ? { ...device, status: "offline" } : device)));
    try {
      await apiRequest(API_ROUTES.device(id), { method: "DELETE" });
      setDeviceLoadState("live");
      setDeviceError("");
    } catch {
      if (previous) setDevices((current) => current.map((device) => (device.id === id ? previous : device)));
      setDeviceError("disable failed");
      setDeviceLoadState("sample");
    }
  };

  const deleteDevice = async (id: number) => {
    const previous = devices;
    setDevices((current) => current.filter((device) => device.id !== id));
    try {
      await apiRequest(API_ROUTES.deviceDelete(id), { method: "DELETE" });
      setDeviceLoadState("live");
      setDeviceError("");
    } catch {
      setDevices(previous);
      setDeviceError("delete failed");
      setDeviceLoadState("sample");
    }
  };

  const probeCamera = async (device: LogicalDevice) => {
    setProbeState((current) => ({ ...current, [device.id]: "probing" }));
    try {
      const result = await apiRequest<CameraProbeResponse>(API_ROUTES.deviceProbe(device.id), { method: "POST" });
      const stream = result.stream || {};
      const size = stream.width && stream.height ? `${stream.width}x${stream.height}` : "stream";
      setProbeState((current) => ({ ...current, [device.id]: result.ok === false ? "failed" : `${size} ok` }));
      setDevices((current) => current.map((item) => (item.id === device.id ? { ...item, status: "online" } : item)));
    } catch {
      setProbeState((current) => ({ ...current, [device.id]: "failed" }));
    }
  };

  const saveNode = async () => {
    if (!nodeModal) return;
    const fallbackNode: SensorNodeRow = {
      node_id: nodeModal.node_id,
      gateway_id: nodeModal.gateway_id,
      parent_node_id: nodeModal.parent_node_id || "-",
      role: nodeModal.role,
      area: nodeModal.area || "-",
      ip_address: nodeModal.ip_address || "-",
      battery_mv: 3200,
      rssi: -55,
      packet_loss: 0,
      last_seen_at: "-",
      status: "offline",
    };
    try {
      const saved = await apiRequest<BackendSensorNode>(API_ROUTES.nodes, {
        method: "POST",
        body: JSON.stringify({
          node_id: nodeModal.node_id,
          gateway_id: nodeModal.gateway_id,
          parent_node_id: nodeModal.parent_node_id || undefined,
          role: nodeModal.role,
          area: nodeModal.area || undefined,
          ip_address: nodeModal.ip_address && nodeModal.ip_address !== "-" ? nodeModal.ip_address : undefined,
        }),
      });
      const nextNode = normalizeNode(saved);
      setNodes((current) => (current.some((node) => node.node_id === nextNode.node_id) ? current.map((node) => node.node_id === nextNode.node_id ? { ...node, ...nextNode } : node) : [nextNode, ...current]));
      setNodeLoadState("live");
    } catch {
      setNodes((current) => {
        const nextNode = fallbackNode;
        return current.some((node) => node.node_id === nextNode.node_id) ? current.map((node) => node.node_id === nextNode.node_id ? { ...node, ...nextNode } : node) : [nextNode, ...current];
      });
      setNodeLoadState("sample");
    }
    setNodeModal(null);
  };

  const deviceFilters: StatusFilter[] = ["all", "online", "offline"];
  const nodeFilters: NodeStatusFilter[] = ["all", "online", "offline", "fault"];

  return h(
    "div",
    { className: "page-stack" },
    h("div", { className: "segmented" }, h(SegmentButton, { active: tab === "devices", onClick: () => setTab("devices") }, "Logical Devices"), h(SegmentButton, { active: tab === "nodes", onClick: () => setTab("nodes") }, "Sensor Nodes")),
    tab === "devices"
      ? h(
          React.Fragment,
          null,
          h(
            "div",
            { className: "toolbar" },
            h("div", { className: "filter-row" }, deviceFilters.map((filter) => h(FilterButton, { key: filter, active: deviceFilter === filter, onClick: () => setDeviceFilter(filter) }, `${filter === "all" ? "All" : filter[0].toUpperCase() + filter.slice(1)} ${filter === "all" ? devices.length : devices.filter((device) => device.status === filter).length}`))),
            h("span", { className: "api-count" }, `GET ${apiLabel(API_ROUTES.devices)} · ${deviceLoadState}${deviceError ? ` · ${deviceError}` : ""}`),
            h("div", { className: "row-actions" }, h("button", { className: "secondary-action", onClick: () => setDeviceModal({ id: 0, name: "Lab RTSP Camera", device_type: "camera", location: "Lab Overview", client_id: "cam-rtsp-01", protocol: "rtsp", ip_address: "192.0.2.72", port: 554, source_uri: "rtsp://192.0.2.72", status: "offline", created_at: new Date().toISOString(), _isNew: true }) }, "+ Add Camera"), h("button", { className: "primary-action", onClick: () => setDeviceModal({ id: 0, name: "", device_type: "sensor_node", location: "", client_id: "", status: "offline", created_at: new Date().toISOString(), _isNew: true }) }, "+ Add Device")),
          ),
          h(
            "div",
            { className: "data-panel" },
            h(DataHeader, { className: "device-grid", columns: ["Name", "device_type", "location", "source", "status", "created_at", ""] }),
            filteredDevices.map((device) =>
              h(
                "div",
                { key: device.id, className: "data-row device-grid" },
                h("span", { className: "strong-cell" }, device.name),
                h("span", { className: "mono-cell" }, device.device_type),
                h("span", null, device.location || "-"),
                h("span", { className: "mono-cell muted-cell" }, device.device_type === "camera" ? cameraSourceLabel(device) : device.client_id || "-"),
                h("span", null, h(StatusTag, { state: device.status }, device.status.toUpperCase())),
                h("span", { className: "mono-cell muted-cell" }, formatShortDate(device.created_at)),
                h(
                  "span",
                  { className: "row-actions" },
                  device.device_type === "camera" ? h("button", { className: "mini-action", onClick: () => probeCamera(device), disabled: probeState[device.id] === "probing" }, probeState[device.id] || "Probe") : null,
                  h("button", { className: "mini-action", onClick: () => setDeviceModal({ ...device }) }, "Edit"),
                  h("button", { className: "mini-action danger", onClick: () => disableDevice(device.id) }, "Disable"),
                  h("button", { className: "mini-action danger", onClick: () => deleteDevice(device.id) }, "Delete"),
                ),
              ),
            ),
          ),
        )
      : h(
          React.Fragment,
          null,
          h("div", { className: "toolbar" }, h("div", { className: "filter-row" }, nodeFilters.map((filter) => h(FilterButton, { key: filter, active: nodeFilter === filter, onClick: () => setNodeFilter(filter) }, `${filter === "all" ? "All" : filter[0].toUpperCase() + filter.slice(1)} ${filter === "all" ? nodes.length : nodes.filter((node) => node.status === filter).length}`))), h("span", { className: "api-count" }, `GET ${apiLabel(API_ROUTES.nodes)} · ${nodeLoadState}`), h("button", { className: "primary-action", onClick: () => setNodeModal({ node_id: "", gateway_id: gatewayIds[0], parent_node_id: "", role: "sensor", area: "", ip_address: "", _isNew: true }) }, "+ Register Node")),
          h(
            "div",
            { className: "data-panel scroll-panel" },
            h(DataHeader, { className: "node-grid", columns: ["node_id", "gateway_id", "parent", "role", "area", "battery_mv", "rssi", "packet_loss", "last_seen_at", "status"] }),
            filteredNodes.map((node) =>
              h(
                "div",
                { key: node.node_id, className: "data-row node-grid clickable-row", onClick: () => setNodeModal({ node_id: node.node_id, gateway_id: node.gateway_id, parent_node_id: node.parent_node_id === "-" ? "" : node.parent_node_id, role: node.role, area: node.area, ip_address: node.ip_address === "-" ? "" : node.ip_address, _isNew: false }) },
                h("span", { className: "strong-cell mono-cell" }, node.node_id),
                h("span", { className: "mono-cell muted-cell" }, node.gateway_id),
                h("span", { className: "mono-cell muted-cell" }, node.parent_node_id),
                h("span", null, node.role),
                h("span", null, node.area),
                h("span", { className: "mono-cell muted-cell" }, node.battery_mv ?? "-"),
                h("span", { className: "mono-cell muted-cell" }, node.rssi ?? "-"),
                h("span", { className: "mono-cell muted-cell" }, node.packet_loss === null ? "-" : `${node.packet_loss.toFixed(1)}%`),
                h("span", { className: "mono-cell muted-cell" }, node.last_seen_at),
                h("span", { className: "align-right" }, h(StatusTag, { state: node.status === "fault" ? "warning" : node.status }, node.status.toUpperCase())),
              ),
            ),
          ),
        ),
    deviceModal ? h(DeviceModal, { modal: deviceModal, onChange: setDeviceModal, onClose: () => setDeviceModal(null), onSave: saveDevice }) : null,
    nodeModal ? h(NodeModal, { modal: nodeModal, onChange: setNodeModal, onClose: () => setNodeModal(null), onSave: saveNode }) : null,
  );
}

function HistoryPage() {
  const [nodeFilter, setNodeFilter] = useState("all");
  const [metric, setMetric] = useState<ReadingMetric>("temperature");
  const [rangeId, setRangeId] = useState<TimeRangeId>("24h");
  const [windowEnd, setWindowEnd] = useState<Date | null>(null);
  const [readings, setReadings] = useState(initialReadings);
  const [loadState, setLoadState] = useState<"loading" | "live" | "sample">("loading");
  const [ingestState, setIngestState] = useState("idle");
  const [ingestOpen, setIngestOpen] = useState(false);
  const [liveNodeIds, setLiveNodeIds] = useState<string[]>([]);
  // Node chips come from the registry, not from sample data, so retired
  // demo nodes never reappear as filter options.
  const nodeIds = ["all", ...(liveNodeIds.length > 0 ? liveNodeIds : Array.from(new Set(readings.map((reading) => reading.node_id))))];
  const metrics: ReadingMetric[] = ["temperature", "humidity", "light", "air_quality"];
  const liveTick = useLiveReadingsTick();
  useEffect(() => {
    let cancelled = false;
    apiRequest<BackendSensorNode[]>(API_ROUTES.nodes)
      .then((items) => {
        if (!cancelled) setLiveNodeIds(items.map((node) => node.node_id).filter(Boolean) as string[]);
      })
      .catch(() => {
        if (!cancelled) setLiveNodeIds([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const range = rangeWindow(rangeId, windowEnd);
  useEffect(() => {
    let cancelled = false;
    setLoadState((current) => (current === "live" ? current : "loading"));
    apiRequest<BackendReading[]>(API_ROUTES.readings, {
      query: {
        node_id: nodeFilter === "all" ? undefined : nodeFilter,
        metric,
        start: range.start,
        end: range.end,
        limit: range.limit,
      },
    })
      .then((items) => {
        if (cancelled) return;
        setReadings(items.map(normalizeReading));
        setLoadState("live");
      })
      .catch(() => {
        if (cancelled) return;
        setReadings(initialReadings);
        setLoadState("sample");
      });
    return () => {
      cancelled = true;
    };
  }, [nodeFilter, metric, rangeId, windowEnd?.getTime(), liveTick]);
  const startMs = new Date(range.start).getTime();
  const endMs = new Date(range.end).getTime();
  const filtered = readings
    .filter((reading) => {
      const captured = new Date(reading.captured_at).getTime();
      return (nodeFilter === "all" || reading.node_id === nodeFilter) && reading.metric === metric && captured >= startMs && captured <= endMs;
    })
    .sort((a, b) => new Date(b.captured_at).getTime() - new Date(a.captured_at).getTime())
    .slice(0, range.limit);
  const chartPoints = filtered.slice().reverse().map((reading) => ({ value: reading.value, label: reading.captured_at }));
  const unit = filtered[0]?.unit || readingUnit(metric);
  const shiftHistoryWindow = (direction: -1 | 1) => setWindowEnd(shiftWindowEnd(rangeId, windowEnd, direction));

  const simulateIngest = async () => {
    const node = nodeFilter === "all" ? nodeIds[1] : nodeFilter;
    if (!node) return;
    const nodeGateway = readings.find((reading) => reading.node_id === node)?.gateway_id || "gw-manual";
    const now = new Date();
    const value = Number((readingBase(metric) + (metric === "light" ? 35 : metric === "humidity" ? 3.2 : 1.1)).toFixed(1));
    const next: SensorReading = {
      message_id: `msg-sim-${Date.now()}`,
      gateway_id: nodeGateway,
      batch_id: `batch-manual-${Date.now()}`,
      node_id: node,
      seq: 5000 + (readings.length % 999),
      metric,
      value,
      unit: readingUnit(metric),
      rssi: -58,
      battery_mv: 3200,
      flags: 0,
      source: "manual",
      captured_at: now.toISOString(),
    };
    setIngestState("posting");
    try {
      await apiRequest(API_ROUTES.readings, {
        method: "POST",
        body: JSON.stringify(
          createReadingBatchPayload({
            gatewayId: next.gateway_id,
            nodeId: node,
            seq: next.seq,
            temperature: metric === "temperature" ? value : readingBase("temperature"),
            humidity: metric === "humidity" ? value : readingBase("humidity"),
            light: metric === "light" ? value : readingBase("light"),
          }),
        ),
      });
      setLoadState("live");
      setIngestState("posted");
    } catch {
      setLoadState("sample");
      setIngestState("local fallback");
    }
    setReadings((current) => [next, ...current]);
  };

  return h(
    "div",
    { className: "page-stack" },
    h(
      "div",
      { className: "filter-card" },
      h("div", { className: "filter-blocks" }, h("div", null, h("div", { className: "filter-label" }, "node_id"), h("div", { className: "choice-row wide" }, nodeIds.map((id) => h(FilterButton, { key: id, active: nodeFilter === id, onClick: () => setNodeFilter(id) }, id === "all" ? "All nodes" : id)))), h("div", null, h("div", { className: "filter-label" }, "metric"), h("div", { className: "choice-row" }, metrics.map((item) => h(FilterButton, { key: item, active: metric === item, onClick: () => setMetric(item) }, item))))),
      h(
        "div",
        { className: "history-controls" },
        h(TimeRangeControls, {
          rangeId,
          windowEnd,
          onRangeChange: (next) => {
            setRangeId(next);
            setWindowEnd(null);
          },
          onShift: shiftHistoryWindow,
          onLive: () => setWindowEnd(null),
        }),
        h("span", { className: "api-count" }, `GET ${apiLabel(API_ROUTES.readings)} · ${loadState} · ${filtered.length} rows`),
      ),
    ),
    h("div", { className: "data-panel" }, h("div", { className: "panel-header" }, h("span", { style: { font: "600 13px Inter", textTransform: "capitalize" } }, `${metric} trend (${unit})`)), h("div", { className: "history-chart" }, h(MetricTrend, { points: chartPoints, unit, rangeStartMs: startMs, rangeEndMs: endMs, ratio: HISTORY_LOOKBACK_RATIO, viewKey: `${nodeFilter}-${metric}-${rangeId}-${windowEnd ? windowEnd.getTime() : "live"}` }))),
    h(
      "div",
      { className: "data-panel scroll-panel" },
      h("div", { className: "section-title" }, "SensorReading records"),
      h(DataHeader, { className: "reading-grid", columns: ["Time", "Node", "Metric", "Reading", "Source", "Gateway", "Seq", "Signal", "Battery", "Flags"] }),
      filtered.slice(0, 60).map((reading) =>
        h(
          "div",
          { key: reading.message_id, className: "data-row reading-grid" },
          h("span", { className: "mono-cell muted-cell" }, formatReadingDate(reading.captured_at)),
          h("span", { className: "strong-cell mono-cell" }, reading.node_id),
          h("span", null, reading.metric),
          h("span", { className: "strong-cell mono-cell" }, `${reading.value}${reading.unit}`),
          h("span", null, h("span", { className: `source-pill ${reading.source}` }, reading.source)),
          h("span", { className: "mono-cell muted-cell" }, reading.gateway_id),
          h("span", { className: "mono-cell muted-cell" }, reading.seq),
          h("span", { className: "mono-cell muted-cell" }, reading.rssi ? `${reading.rssi} dBm` : "-"),
          h("span", { className: "mono-cell muted-cell" }, reading.battery_mv ? `${reading.battery_mv} mV` : "-"),
          h("span", { className: "mono-cell muted-cell" }, reading.flags),
        ),
      ),
    ),
    h(
      "div",
      { className: "ingest-panel" },
      h("button", { className: "ingest-toggle", onClick: () => setIngestOpen((open) => !open) }, h("span", null, ingestOpen ? "▾" : "▸"), `Manual ingestion (simulator utility) · POST ${apiLabel(API_ROUTES.readings)}`),
      ingestOpen ? h("div", { className: "ingest-body" }, h("span", null, `For simulator and test use only — real telemetry arrives via MQTT. Status: ${ingestState}.`), h("button", { className: "secondary-action", onClick: simulateIngest }, "Simulate one reading")) : null,
    ),
  );
}

function PageCard({ title, right, children }: { title?: string; right?: ReactNode; children?: ReactNode }) {
  return h("div", { className: "data-panel" }, title ? h("div", { className: "panel-header" }, h("span", { style: { font: "600 13px Inter" } }, title), right || null) : null, children);
}

function TopologyPage() {
  const [selected, setSelected] = useState("");
  const [filter, setFilter] = useState("all");
  const [nodes, setNodes] = useState<LabNode[]>(labNodes);
  const [loadState, setLoadState] = useState<"loading" | "live" | "live empty" | "sample">("loading");
  const pollTick = usePollTick(5000);

  useEffect(() => {
    let cancelled = false;
    setLoadState((current) => (current === "live" || current === "live empty" ? current : "loading"));
    Promise.all([
      apiRequest<BackendSensorNode[]>(API_ROUTES.nodes),
      apiRequest<BackendReading[]>(API_ROUTES.readingsLatest),
    ])
      .then(([nodeRows, latest]) => {
        if (cancelled) return;
        const mapped = createTopologyNodes(nodeRows, latest);
        if (mapped.length > 0) {
          setNodes(mapped);
          setSelected((current) => selectPreferredTopologyNode(mapped, current)?.id || mapped[0].id);
          setLoadState("live");
        } else {
          // Backend is reachable but has registered no nodes yet — don't keep the
          // hardcoded sample topology under a "live" label.
          setNodes([]);
          setLoadState("live empty");
        }
      })
      .catch(() => {
        if (cancelled) return;
        setNodes(labNodes);
        setLoadState("sample");
      });
    return () => {
      cancelled = true;
    };
  }, [pollTick]);

  const visible = nodes.filter((node) => filter === "all" || node.status === filter || (filter === "loss" && node.loss > 15));
  const selectedNode = nodes.find((node) => node.id === selected) || selectPreferredTopologyNode(nodes);
  const byId = Object.fromEntries(nodes.map((node) => [node.id, node]));
  const nodeColor = (node: LabNode) => node.status === "offline" ? "#dc4444" : node.status === "fault" || node.loss > 15 ? "#e08206" : "#26e07f";
  return h(
    "div",
    { className: "topology-layout" },
    h(
      "div",
      { className: "topology-map" },
      h("div", { className: "dark-toolbar" }, ["all", "online", "offline", "loss"].map((key) => h("button", { key, className: `dark-filter ${filter === key ? "active" : ""}`, onClick: () => setFilter(key) }, `${key === "all" ? "All" : key === "loss" ? "High Loss" : key}`))),
      h(
        "svg",
        { viewBox: "0 0 760 420", className: "topology-svg" },
        nodes.filter((node) => node.parent && byId[node.parent]).map((node) => {
          const parent = byId[node.parent || ""];
          return h("line", { key: `l${node.id}`, x1: parent.x, y1: parent.y, x2: node.x, y2: node.y, stroke: node.status === "offline" ? "#3a2530" : "#26344a", strokeWidth: 1.5, strokeDasharray: node.status === "offline" ? "4 4" : "0" });
        }),
        visible.map((node) =>
          h(
            "g",
            { key: node.id, onClick: () => setSelected(node.id), className: "topology-node" },
            node.id === selected ? h("circle", { cx: node.x, cy: node.y, r: node.type === "sink" ? 22 : 18, fill: "rgba(59,91,219,.12)", stroke: "#3b5bdb", strokeWidth: 2 }) : null,
            node.type === "sink" ? h("rect", { x: node.x - 15, y: node.y - 15, width: 30, height: 30, rx: 4, fill: "#0f1a14", stroke: nodeColor(node), strokeWidth: 2 }) : h("circle", { cx: node.x, cy: node.y, r: node.type === "router" ? 12 : 9, fill: node.status === "offline" ? "#1a1216" : "#0f1a14", stroke: nodeColor(node), strokeWidth: 2 }),
            h("circle", { cx: node.x, cy: node.y, r: 2.5, fill: nodeColor(node) }),
            h("text", { x: node.x, y: node.y + 26, fill: node.id === selected ? "#c9d3e0" : "#7c8898", fontSize: 10, fontFamily: "JetBrains Mono, monospace", textAnchor: "middle" }, node.label),
          ),
        ),
      ),
    ),
    h(
      "div",
      { className: "topology-side" },
      h("div", { className: "api-count" }, `GET ${apiLabel(API_ROUTES.nodes)} + ${apiLabel(API_ROUTES.readingsLatest)} · ${loadState}`),
      h(PageCard, { title: "Selected Node" }, selectedNode ? h("div", { className: "node-detail" }, h("div", { className: "detail-title" }, selectedNode.id, h(StatusTag, { state: selectedNode.status === "fault" ? "warning" : selectedNode.status }, selectedNode.status.toUpperCase())), h("div", { className: "detail-sub" }, selectedNode.name, " · ", selectedNode.zone), h("div", { className: "detail-grid" }, [["Source", selectedNode.source || "-"], ["RSSI", `${selectedNode.rssi} dBm`], ["Packet Loss", `${selectedNode.loss.toFixed(1)}%`], ["Heartbeat", `${selectedNode.hb}s ago`], ["Parent", selectedNode.parent || "-"], ["Temp", selectedNode.status === "offline" ? "-" : `${selectedNode.temp} °C`], ["Humidity", selectedNode.status === "offline" ? "-" : `${selectedNode.hum}%`], ["Light", selectedNode.status === "offline" ? "-" : `${selectedNode.light}`]].map(([label, value]) => h("div", { key: label }, h("div", { className: "stat-label" }, label), h("div", { className: "stat-value small" }, value))))) : h("div", { className: "empty-panel" }, h("b", null, "No backend topology nodes"), h("span", null, "Start a gateway or seed demo runtime to register nodes."))),
      h(PageCard, { title: "All Nodes" }, h("div", { className: "compact-list" }, nodes.length === 0 ? h("div", { className: "empty-panel compact-empty" }, "No nodes") : nodes.map((node) => h("button", { key: node.id, className: `node-row ${node.id === selected ? "active" : ""}`, title: node.id, onClick: () => setSelected(node.id) }, h("span", { style: { background: nodeColor(node) } }), h("b", null, node.label), h("em", null, node.name), h("code", null, `${node.batt}%`), h("code", null, node.rssi))))),
    ),
  );
}

function EnvironmentPage() {
  const [metric, setMetric] = useState<EnvironmentMetric>("temperature");
  const [rangeId, setRangeId] = useState<TimeRangeId>("24h");
  const [windowEnd, setWindowEnd] = useState<Date | null>(null);
  const [zones, setZones] = useState<EnvironmentZoneItem[]>([]);
  const [readings, setReadings] = useState<SensorReading[]>([]);
  const [loadState, setLoadState] = useState<"loading" | "live" | "live empty" | "sample">("loading");
  const liveTick = useLiveReadingsTick();
  const range = rangeWindow(rangeId, windowEnd);
  const startMs = new Date(range.start).getTime();
  const endMs = new Date(range.end).getTime();

  useEffect(() => {
    let cancelled = false;
    // On SSE-triggered refreshes keep showing the current data instead of
    // flashing back to the loading state.
    setLoadState((current) => (current === "live" || current === "live empty" ? current : "loading"));
    Promise.all([
      apiRequest<BackendSensorNode[]>(API_ROUTES.nodes),
      apiRequest<BackendReading[]>(API_ROUTES.readings, { query: { metric, start: range.start, end: range.end, limit: range.limit } }),
    ])
      .then(([nodeRows, backendReadings]) => {
        if (cancelled) return;
        const normalized = backendReadings.map(normalizeReading);
        const mapped = createEnvironmentZones(nodeRows, backendReadings, metric);
        setReadings(normalized);
        setZones(mapped);
        setLoadState(mapped.length > 0 ? "live" : "live empty");
      })
      .catch(() => {
        if (cancelled) return;
        setReadings([]);
        setZones([]);
        setLoadState("sample");
      });
    return () => {
      cancelled = true;
    };
  }, [metric, rangeId, windowEnd?.getTime(), liveTick]);

  // Only substitute hardcoded sample data before the backend has answered or when it
  // is unreachable — never under a "live" label, which would be a misleading data claim.
  const useSample = loadState === "loading" || loadState === "sample";
  const liveChartPoints = readings
    .filter((reading) => reading.metric === metric)
    .sort((a, b) => new Date(a.captured_at).getTime() - new Date(b.captured_at).getTime())
    .map((reading) => ({ value: reading.value, label: reading.captured_at }));
  const fallbackBase = metric === "temperature" ? series.temp : metric === "humidity" ? series.hum : series.light;
  const chartPoints = liveChartPoints.length > 0 ? liveChartPoints : useSample ? fallbackBase.map((value) => ({ value })) : [];
  const colorMap = ["#3b5bdb", "#129d6b", "#e08206", "#0ea5b7"];
  const cards = zones.length > 0
    ? zones
    : useSample
      ? createEnvironmentZones(initialNodes, initialReadings.filter((reading) => reading.metric === metric), metric)
      : [];
  const zoneCards = cards.map((zone, index) => {
    return h(
      "div",
      { key: zone.zone, className: "zone-card" },
      h("div", { className: "zone-bar", style: { background: colorMap[index] } }),
      h(
        "div",
        { className: "zone-body" },
        h("div", { className: "zone-head" }, h("span", null, zone.zone), h(StatusTag, { state: zone.status === "fault" ? "warning" : zone.status }, zone.status === "fault" ? "ANOMALY" : zone.status.toUpperCase())),
        h("div", { className: "zone-value" }, zone.current.toFixed(metric === "temperature" ? 1 : 0), h("span", null, readingUnit(metric))),
        h("div", { className: "mini-chart" }, h(Sparkline, { data: zone.values, color: colorMap[index % colorMap.length], fill: "rgba(0,0,0,.03)" })),
        h("div", { className: "zone-meta" }, `avg ${zone.avg.toFixed(1)}${readingUnit(metric)}`),
      ),
    );
  });
  const statRows = cards.map((zone, index) => {
    return h(
      "div",
      { key: zone.zone, className: "data-row env-grid" },
      h("span", { className: "strong-cell" }, h("i", { className: "row-dot", style: { background: colorMap[index % colorMap.length] } }), zone.zone),
      h("span", { className: "align-right mono-cell strong-cell" }, zone.current.toFixed(1)),
      h("span", { className: "align-right mono-cell muted-cell" }, zone.min.toFixed(1)),
      h("span", { className: "align-right mono-cell muted-cell" }, zone.max.toFixed(1)),
      h("span", { className: "align-right mono-cell" }, zone.avg.toFixed(1)),
    );
  });
  return h(
    "div",
    { className: "page-stack" },
    h(
      "div",
      { className: "toolbar stacked-toolbar" },
      h("div", { className: "filter-row" }, (["temperature", "humidity", "light"] as EnvironmentMetric[]).map((item) => h(FilterButton, { key: item, active: metric === item, onClick: () => setMetric(item) }, item))),
      h(TimeRangeControls, {
        rangeId,
        windowEnd,
        onRangeChange: (next) => {
          setRangeId(next);
          setWindowEnd(null);
        },
        onShift: (direction) => setWindowEnd(shiftWindowEnd(rangeId, windowEnd, direction)),
        onLive: () => setWindowEnd(null),
      }),
      h("span", { className: "api-count" }, `GET ${apiLabel(API_ROUTES.readings)}?metric=${metric} · ${loadState}`),
    ),
    h("div", { className: "zone-grid" }, cards.length > 0 ? zoneCards : h("div", { className: "empty-panel" }, h("b", null, "No backend readings for this metric"), h("span", null, `GET ${apiLabel(API_ROUTES.readings)}?metric=${metric} returned no rows`))),
    h(PageCard, { title: `${metric} · all zones`, right: h("span", { className: "active-badge" }, readingUnit(metric)) }, h("div", { className: "large-chart" }, h(MetricTrend, { points: chartPoints, color: "#3b5bdb", unit: readingUnit(metric), rangeStartMs: startMs, rangeEndMs: endMs, ratio: HISTORY_LOOKBACK_RATIO, viewKey: `${metric}-${rangeId}-${windowEnd ? windowEnd.getTime() : "live"}` })), h("div", { className: "anomaly-note" }, h("span", null), cards.length === 0 ? "No backend readings in range" : cards.some((zone) => zone.status === "fault") ? "Backend readings crossed configured threshold" : "No anomalies in backend range")),
    h("div", { className: "data-panel" }, h(DataHeader, { className: "env-grid", columns: ["Zone", "Current", "Min", "Max", "Avg"] }), statRows),
  );
}

function VisionPage({ visionSettings }: { visionSettings: UserSettingsState["vision"] }) {
  const [runtimeStatus, setRuntimeStatus] = useState("checking");
  const [events, setEvents] = useState<VisionTimelineItem[]>([]);
  const [cameras, setCameras] = useState<LogicalDevice[]>([]);
  const [selectedCameraId, setSelectedCameraId] = useState<number | null>(null);
  const [eventState, setEventState] = useState<"loading" | "live" | "live empty" | "sample">("loading");
  const [captionState, setCaptionState] = useState<"idle" | "captioning" | "failed">("idle");
  const [latestCaption, setLatestCaption] = useState("");
  // Poll fast enough to keep up with the configured person-inference cadence
  // (settings.vision.inference_hz), clamped so we never hammer the backend
  // faster than every 150ms even if the user sets a very high rate.
  const pollTick = usePollTick(clampNumber(1000 / visionSettings.inference_hz, 150, 5000));
  useEffect(() => {
    let mounted = true;
    apiRequest<{ status: string; runtimes: string[] }>(API_ROUTES.inferenceHealth)
      .then((data) => {
        if (mounted) setRuntimeStatus(`${data.status} · ${data.runtimes.join(", ")}`);
      })
      .catch(() => {
        if (mounted) setRuntimeStatus("offline");
      });
    return () => {
      mounted = false;
    };
  }, [pollTick]);
  useEffect(() => {
    let mounted = true;
    apiRequest<VisionEventBackend[]>(API_ROUTES.visionEvents, { query: { limit: 50 } })
      .then((items) => {
        if (!mounted) return;
        setEvents(createVisionTimelineItems(items));
        setEventState(items.length > 0 ? "live" : "live empty");
      })
      .catch(() => {
        if (!mounted) return;
        setEvents([]);
        setEventState("sample");
      });
    return () => {
      mounted = false;
    };
  }, [pollTick]);
  useEffect(() => {
    let mounted = true;
    apiRequest<BackendDevice[]>(API_ROUTES.devices)
      .then((items) => {
        if (!mounted) return;
        const cameraItems = items.map(normalizeLogicalDevice).filter((device) => device.device_type === "camera");
        setCameras(cameraItems);
        setSelectedCameraId((current) => current && cameraItems.some((device) => device.id === current) ? current : cameraItems[0]?.id || null);
      })
      .catch(() => {
        if (!mounted) return;
        const cameraItems = initialDevices.filter((device) => device.device_type === "camera");
        setCameras(cameraItems);
        setSelectedCameraId((current) => current || cameraItems[0]?.id || null);
      });
    return () => {
      mounted = false;
    };
  }, [pollTick]);
  const predictPayload = createInferencePredictPayload({
    task: "scene-classification",
    cameraId: cameras.find((camera) => camera.id === selectedCameraId)?.client_id || "cam-01",
    frameId: "latest",
    imageUri: "file:///tmp/breezio-camera-latest.jpg",
  });
  const latest = events[0];
  const selectedCamera = cameras.find((camera) => camera.id === selectedCameraId) || cameras[0] || null;
  const describeScene = async () => {
    if (!selectedCamera || captionState === "captioning") return;
    setCaptionState("captioning");
    try {
      const result = await apiRequest<{ outputs?: { scene_summary?: string | null; detail?: string | null } }>(API_ROUTES.deviceCaption(selectedCamera.id), { method: "POST" });
      const summary = result.outputs?.scene_summary;
      setLatestCaption(summary || `caption unavailable: ${result.outputs?.detail || "no summary returned"}`);
      setCaptionState("idle");
    } catch (error) {
      setLatestCaption(error instanceof Error ? error.message : "caption request failed");
      setCaptionState("failed");
    }
  };
  const isSelectedCameraEvent = (event: VisionTimelineItem) => !selectedCamera || !event.cameraId || event.cameraId === selectedCamera.client_id;
  const personEvent = events.find((event) => event.task === "person-detection" && isSelectedCameraEvent(event));
  const matchingFaceEvents = events.filter(
    (event) =>
      event.task === "face-identification" &&
      isSelectedCameraEvent(event) &&
      event.detections.length > 0 &&
      (!personEvent?.frameId || !event.frameId || event.frameId === personEvent.frameId),
  );
  const fallbackFaceEvent = events.find((event) => event.task === "face-identification" && isSelectedCameraEvent(event) && event.detections.length > 0);
  const faceEvents = matchingFaceEvents.length > 0 ? matchingFaceEvents : fallbackFaceEvent ? [fallbackFaceEvent] : [];
  const detectionEvent =
    personEvent || faceEvents[0]
      ? {
          ...(personEvent || faceEvents[0]),
          detections: [...(personEvent?.detections || []), ...faceEvents.flatMap((event) => event.detections)],
          peopleCount: personEvent?.peopleCount ?? 0,
          runtime: [personEvent?.runtime, ...faceEvents.map((event) => event.runtime)].filter(Boolean).join(" + "),
        }
      : undefined;
  const eventRows: VisionTimelineItem[] =
    events.length > 0
      ? events
      : [
          {
            id: "empty",
            cameraId: "",
            frameId: "",
            task: "vision-event",
            modelId: "",
            runtime: "",
            title: "No persisted vision events",
            detail: `GET ${apiLabel(API_ROUTES.visionEvents)} · ${eventState}`,
            captured: "-",
            peopleCount: 0,
            detections: [],
          },
        ];
  return h("div", { className: "vision-layout" }, h("div", { className: "page-stack" }, h(CameraPanel, { device: selectedCamera, latestEvent: latest, detectionEvent, visionSettings }), h("div", { className: "toolbar" }, h("button", { className: "primary-action", onClick: describeScene, disabled: !selectedCamera || captionState === "captioning" }, captionState === "captioning" ? "Describing…" : "Describe scene"), h("span", { className: captionState === "failed" ? "muted-cell" : "api-count" }, latestCaption || `VLM caption · POST ${selectedCamera ? apiLabel(API_ROUTES.deviceCaption(selectedCamera.id)) : "-"}`)), cameras.length > 1 ? h("div", { className: "filter-row" }, cameras.map((camera) => h(FilterButton, { key: camera.id, active: camera.id === selectedCamera?.id, onClick: () => setSelectedCameraId(camera.id) }, camera.name))) : null, h("div", { className: "metric-strip" }, [["Runtime", runtimeStatus], ["Events", String(events.length)], ["Latest", latest?.captured || "-"], ["Source", selectedCamera ? cameraSourceLabel(selectedCamera) : eventState]].map(([label, value]) => h("div", { key: label, className: "metric-card" }, h("div", { className: "stat-label" }, label), h("div", { className: "stat-value" }, value)))), h("div", { className: "data-panel model-runtime" }, h("div", null, h("div", { className: "stat-label" }, "Active Vision Route"), h("div", { className: "model-name" }, selectedCamera ? apiLabel(API_ROUTES.devicePreview(selectedCamera.id)) : apiLabel(API_ROUTES.visionEvents)), h("div", { className: "mono-cell muted-cell" }, `POST ${apiLabel(API_ROUTES.inferencePredict)}`)), h("div", null, h("div", { className: "stat-label" }, "Runtime / Backend"), h("div", { className: "mono-cell" }, runtimeStatus), h("div", { className: "mono-cell muted-cell" }, JSON.stringify(predictPayload.input))))), h("div", { className: "page-stack" }, h(PageCard, { title: "Scene Classification", right: h("span", { className: "active-badge" }, eventState) }, h("div", { className: "scene-states" }, ["Vacant", "Normal", "Crowded", "Abnormal"].map((name) => h("div", { key: name, className: `scene-state ${latest?.title.toLowerCase().includes(name.toLowerCase()) ? "active" : ""}` }, h("span", null), h("b", null, name), h("code", null, latest?.title.toLowerCase().includes(name.toLowerCase()) ? latest.detail : ""))))), h(PageCard, { title: "Latest Events" }, h("div", { className: "snap-grid" }, eventRows.slice(0, 4).map((event) => h("div", { key: event.id, className: "snap" }, h("div", { className: "snap-thumb" }, h("span", null, event.detail.split(" · ")[0])), h("div", null, event.captured))))), h(PageCard, { title: "Event Timeline" }, h("div", { className: "timeline-list" }, eventRows.map((event) => h("div", { key: event.id }, h("span", null), h("p", null, `${event.title} · ${event.detail} · ${event.captured}`)))))));
}

function PredictionPage() {
  const [metric, setMetric] = useState<ReadingMetric>("temperature");
  const [horizonId, setHorizonId] = useState<PredictionHorizonId>("30m");
  const [models, setModels] = useState<PredictionRowItem[]>([]);
  const [runs, setRuns] = useState<BackendPredictionRun[]>([]);
  const [active, setActive] = useState("");
  const [actualPoints, setActualPoints] = useState<TrendPoint[]>([]);
  const [latestPrediction, setLatestPrediction] = useState<BackendLatestPrediction | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "live" | "live empty" | "offline">("loading");
  const [refresh, setRefresh] = useState(0);
  const [trainState, setTrainState] = useState<"idle" | "training" | "failed">("idle");
  const forecastRange = predictionWindow(horizonId);
  // Prediction runs are inference output — refresh on the 30s inference
  // cadence rather than the 5s operational-data cadence.
  const pollTick = usePollTick(30000);
  // Model rows share the same id across horizons (node+metric+model_name has
  // no horizon component), but each horizon activates its own model
  // independently. Without tracking the horizon a selection was made under,
  // switching horizons would keep showing the previous horizon's model even
  // though it isn't the active one here — which starves the forecast fetch
  // below (it requires model.active) and silently drops the future curve.
  const selectionScopeRef = useRef("");

  useEffect(() => {
    let cancelled = false;
    setLoadState((current) => (current === "live" || current === "live empty" ? current : "loading"));
    Promise.all([
      apiRequest<PredictionMetricBackend[]>(API_ROUTES.predictionMetrics, { query: { metric, horizon_minutes: forecastRange.option.minutes } }),
      apiRequest<BackendPredictionRun[]>(API_ROUTES.predictionRuns, { query: { metric, limit: 200 } }),
      apiRequest<BackendReading[]>(API_ROUTES.readings, { query: { metric, start: forecastRange.start, end: forecastRange.end, limit: forecastRange.limit } }),
    ])
      .then(([metrics, predictionRuns, readings]) => {
        if (cancelled) return;
        const rows = createPredictionRows(metrics).filter((row) => row.type.startsWith(metric));
        setModels(rows);
        setRuns(predictionRuns.filter((run) => run.metric === metric && run.status !== "failed"));
        const scopeKey = `${metric}:${horizonId}`;
        const sameScope = selectionScopeRef.current === scopeKey;
        selectionScopeRef.current = scopeKey;
        setActive((current) => (sameScope && rows.some((row) => row.id === current)) ? current : rows.find((row) => row.active)?.id || rows[0]?.id || "");
        const orderedReadings = [...readings].sort((a, b) => new Date(a.captured_at || "").getTime() - new Date(b.captured_at || "").getTime());
        setActualPoints(orderedReadings.map((reading) => ({ value: toNumber(reading.value), label: reading.captured_at || "" })).filter((point) => Number.isFinite(point.value) && point.label));
        setLoadState(orderedReadings.length > 0 || rows.length > 0 ? "live" : "live empty");
      })
      .catch(() => {
        if (cancelled) return;
        setModels([]);
        setRuns([]);
        setActive("");
        setActualPoints([]);
        setLatestPrediction(null);
        setLoadState("offline");
      });
    return () => {
      cancelled = true;
    };
  }, [metric, horizonId, pollTick, refresh]);

  const model = models.find((item) => item.id === active) || models[0];
  const horizon = predictionHorizon(horizonId);
  const historical = latestHistoricalToPoints(latestPrediction, horizon);
  const future = latestPredictionToPoints(latestPrediction, horizon);
  const activeModelName = model?.name || "No active prediction model";
  const recommended = models.find((item) => item.active) || models[0];

  useEffect(() => {
    let cancelled = false;
    if (!model?.active || !model.nodeId) {
      setLatestPrediction(null);
      return;
    }
    apiRequest<BackendLatestPrediction>(API_ROUTES.predictionLatest, { query: { node_id: model.nodeId, metric, horizon_minutes: forecastRange.option.minutes } })
      .then((prediction) => {
        if (!cancelled) setLatestPrediction(prediction);
      })
      .catch(() => {
        if (!cancelled) setLatestPrediction(null);
      });
    return () => {
      cancelled = true;
    };
  }, [model?.id, model?.active, metric, horizonId, pollTick, refresh]);

  const selectModel = async (item: PredictionRowItem) => {
    setActive(item.id);
    const run = runs.find(
      (candidate) =>
        candidate.run_id &&
        candidate.node_id === item.nodeId &&
        candidate.model_name === item.modelName &&
        candidate.metric === metric &&
        isSamePredictionHorizon(candidate.horizon_minutes, horizon),
    );
    if (!run?.run_id) return;
    try {
      await apiRequest(API_ROUTES.predictionRunActivate(run.run_id), { method: "POST" });
      setRefresh((n) => n + 1);
    } catch {
      // keep the local selection; metrics table will still show the old active model
    }
  };

  const retrainModels = async () => {
    const nodeId = model?.nodeId || models[0]?.nodeId || runs.find((run) => run.node_id)?.node_id;
    if (!nodeId || trainState === "training") return;
    setTrainState("training");
    try {
      const features = await apiRequest<{ models?: string[] }>(API_ROUTES.predictionFeatures);
      await apiRequest(API_ROUTES.predictionRuns, {
        method: "POST",
        body: JSON.stringify({
          target_node_id: nodeId,
          metric,
          horizon_minutes: forecastRange.option.minutes,
          models: features.models && features.models.length > 0 ? features.models : ["persistence", "linear_regression", "random_forest", "hist_gradient_boosting", "mlp_regressor"],
        }),
      });
      setTrainState("idle");
      setRefresh((n) => n + 1);
    } catch {
      setTrainState("failed");
    }
  };

  return h(
    "div",
    { className: "page-stack" },
    h("div", { className: "pred-top" },
      h("div", { className: "data-panel pred-card" }, h("div", { className: "stat-label" }, "Active Model"), h("div", { className: "model-name" }, activeModelName), h("div", { className: "mono-cell muted-cell" }, `${metricLabel(metric)} · ${predictionHorizon(horizonId).label} · ${loadState}`)),
      h("div", { className: "data-panel pred-card recommended" }, h("div", { className: "stat-label" }, "Realtime Evaluation"), h("div", { className: "model-name" }, recommended?.name || "No backend recommendation"), h("p", null, recommended ? `MAE ${recommended.mae} · RMSE ${recommended.rmse} · R² ${recommended.r2} · ${recommended.lat}` : "No completed backend prediction runs yet."))),
    h(
      "div",
      { className: "toolbar stacked-toolbar" },
      h("div", { className: "filter-blocks" },
        h("div", null, h("div", { className: "filter-label" }, "metric"), h("div", { className: "choice-row wide" }, predictionMetricOptions.map((item) => h(FilterButton, { key: item.id, active: metric === item.id, onClick: () => setMetric(item.id) }, item.label)))),
        h("div", null, h("div", { className: "filter-label" }, "forecast horizon"), h("div", { className: "choice-row" }, predictionHorizonOptions.map((item) => h(FilterButton, { key: item.id, active: horizonId === item.id, onClick: () => setHorizonId(item.id) }, item.label)))),
      ),
      h("span", { className: "api-count" }, `${actualPoints.length} real history points · ${historical.length} backtest prediction points · ${future.length} future prediction points · ${readingUnit(metric)}`),
      h("button", { className: "primary-action", disabled: trainState === "training" || !(model?.nodeId || models[0]?.nodeId), onClick: () => void retrainModels() }, trainState === "training" ? "Training…" : trainState === "failed" ? "Retrain failed — retry" : "Retrain models"),
    ),
    h(PageCard, { title: `${metricLabel(metric)} Forecast`, right: h("span", { className: "active-badge" }, horizon.label) },
      h("div", { className: "forecast-legend" }, h("span", { className: "actual-line" }, "Actual history"), h("span", { className: "backtest-line" }, "Historical prediction"), h("span", { className: "future-line" }, "Future prediction only")),
      h("div", { className: "large-chart prediction-large-chart" }, h(PredictionForecastTrend, { actual: actualPoints, historical, future, horizon, metric, color: model?.color || "#129d6b" })),
      h("div", { className: "anomaly-note" }, h("span", null), historical.length === 0 && future.length === 0 ? `No real prediction series for ${horizon.label.toLowerCase()} yet; only measured history is shown.` : `Left region covers the past ${horizon.minutes * horizon.historyMultiplier >= 60 ? `${horizon.minutes * horizon.historyMultiplier / 60}h` : `${horizon.minutes * horizon.historyMultiplier}m`}; right region is ${horizon.label.toLowerCase()} forecast only.`),
    ),
    h("div", { className: "data-panel" }, h(DataHeader, { className: "pred-grid", columns: ["Model", "Live MAE", "RMSE", "R²", "Latency", "Selection"] }), models.length === 0 ? h("div", { className: "empty-panel" }, h("b", null, "No backend prediction metrics"), h("span", null, `POST ${apiLabel(API_ROUTES.predictionRuns)} to create a run`)) : models.map((item) => h("div", { key: item.id, className: `data-row pred-grid ${active === item.id ? "selected-row" : ""}` }, h("span", { className: "strong-cell" }, h("i", { className: "row-dot", style: { background: item.color } }), item.name, h("small", { className: "row-sub" }, item.type)), h("span", null, item.mae), h("span", null, item.rmse), h("span", null, item.r2), h("span", null, item.lat), h("button", { className: active === item.id ? "mini-action" : "primary-action", onClick: () => void selectModel(item) }, active === item.id ? "Active" : "Select")))),
  );
}

type AlertRule = {
  id: number;
  name: string;
  metric: string;
  node_id: string | null;
  condition: string;
  threshold: number;
  severity: string;
  enabled: boolean | number;
  cooldown_seconds: number;
  last_fired_at: string | null;
};

const RULE_METRICS = ["temperature", "humidity", "light", "air_quality", "battery_mv", "rssi"];
const RULE_CONDITIONS = ["gt", "gte", "lt", "lte"];
const RULE_SEVERITIES = ["low", "medium", "high", "critical"];
const CONDITION_LABELS: Record<string, string> = { gt: ">", gte: "\u2265", lt: "<", lte: "\u2264" };

function AlertRulesPanel() {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [state, setState] = useState<string>("loading");
  const [draft, setDraft] = useState({ name: "", metric: "temperature", condition: "gt", threshold: "30", node_id: "", severity: "medium", cooldown_seconds: "300" });

  const loadRules = () => {
    apiRequest<AlertRule[]>(API_ROUTES.alertRules)
      .then((items) => { setRules(items); setState("live"); })
      .catch(() => setState("backend unavailable"));
  };
  useEffect(loadRules, []);

  const createRule = async () => {
    if (!draft.name.trim() || !draft.threshold.trim()) { setState("name and threshold required"); return; }
    setState("saving");
    try {
      await apiRequest(API_ROUTES.alertRules, {
        method: "POST",
        body: JSON.stringify({
          name: draft.name.trim(),
          metric: draft.metric,
          condition: draft.condition,
          threshold: Number(draft.threshold),
          node_id: draft.node_id.trim() || null,
          severity: draft.severity,
          cooldown_seconds: Number(draft.cooldown_seconds) || 300,
        }),
      });
      setDraft({ ...draft, name: "", threshold: "30" });
      loadRules();
    } catch { setState("create failed"); }
  };

  const toggleRule = async (rule: AlertRule) => {
    setState("saving");
    try {
      await apiRequest(API_ROUTES.alertRule(rule.id), { method: "PUT", body: JSON.stringify({ enabled: !rule.enabled }) });
      loadRules();
    } catch { setState("update failed"); }
  };

  const deleteRule = async (rule: AlertRule) => {
    setState("saving");
    try {
      await apiRequest(API_ROUTES.alertRule(rule.id), { method: "DELETE" });
      loadRules();
    } catch { setState("delete failed"); }
  };

  return h(PageCard, { title: "Alert Rules" },
    h("div", { className: "toolbar" }, h("span", { className: "api-count" }, `${apiLabel(API_ROUTES.alertRules)} \u00b7 ${state}`)),
    h("div", { className: "data-panel" },
      h(DataHeader, { className: "rule-grid", columns: ["Rule", "Condition", "Node", "Severity", "Cooldown", "Enabled", ""] }),
      rules.map((rule) => h("div", { key: rule.id, className: "data-row rule-grid" },
        h("span", { className: "strong-cell" }, rule.name),
        h("span", { className: "mono-cell" }, `${rule.metric} ${CONDITION_LABELS[rule.condition] || rule.condition} ${rule.threshold}`),
        h("span", { className: "mono-cell muted-cell" }, rule.node_id || "all nodes"),
        h(StatusTag, { state: rule.severity === "critical" || rule.severity === "high" ? "critical" : "sent" }, rule.severity),
        h("span", { className: "mono-cell muted-cell" }, `${rule.cooldown_seconds}s`),
        h("button", { className: "secondary-action", onClick: () => toggleRule(rule) }, rule.enabled ? "On" : "Off"),
        h("button", { className: "secondary-action", onClick: () => deleteRule(rule) }, "Delete"))),
      rules.length === 0 && state === "live" ? h("div", { className: "note-box" }, "No rules yet \u2014 create one below; it fires alerts on matching sensor readings.") : null),
    h("div", { className: "form-grid" },
      h("input", { placeholder: "Rule name", value: draft.name, onInput: (e: any) => setDraft({ ...draft, name: e.currentTarget.value }) }),
      h("select", { value: draft.metric, onChange: (e: any) => setDraft({ ...draft, metric: e.currentTarget.value }) }, RULE_METRICS.map((m) => h("option", { key: m, value: m }, m))),
      h("select", { value: draft.condition, onChange: (e: any) => setDraft({ ...draft, condition: e.currentTarget.value }) }, RULE_CONDITIONS.map((c) => h("option", { key: c, value: c }, CONDITION_LABELS[c]))),
      h("input", { placeholder: "Threshold", value: draft.threshold, onInput: (e: any) => setDraft({ ...draft, threshold: e.currentTarget.value }) }),
      h("input", { placeholder: "Node (blank = all)", value: draft.node_id, onInput: (e: any) => setDraft({ ...draft, node_id: e.currentTarget.value }) }),
      h("select", { value: draft.severity, onChange: (e: any) => setDraft({ ...draft, severity: e.currentTarget.value }) }, RULE_SEVERITIES.map((sv) => h("option", { key: sv, value: sv }, sv))),
      h("input", { placeholder: "Cooldown s", value: draft.cooldown_seconds, onInput: (e: any) => setDraft({ ...draft, cooldown_seconds: e.currentTarget.value }) }),
      h("button", { className: "primary-action", onClick: createRule }, "Create rule")));
}

function AlertsPage() {
  const [filter, setFilter] = useState<AlertFilter>("all");
  const [alerts, setAlerts] = useState<AlertRecord[]>(alertRecords);
  const [selected, setSelected] = useState(alertRecords[0].id);
  const [loadState, setLoadState] = useState<"loading" | "live" | "sample" | "updating">("loading");
  const pollTick = usePollTick(5000);

  const loadAlerts = () => {
    setLoadState("loading");
    apiRequest<DashboardAlertBackend[]>(API_ROUTES.alerts)
      .then((items) => {
        const next = createAlertRecords(items);
        setAlerts(next);
        setSelected((current) => next.some((alert) => alert.id === current) ? current : next[0]?.id || "");
        setLoadState("live");
      })
      .catch(() => {
        setAlerts(alertRecords);
        setSelected(alertRecords[0].id);
        setLoadState("sample");
      });
  };

  useEffect(() => {
    let cancelled = false;
    setLoadState((current) => (current === "live" ? current : "loading"));
    apiRequest<DashboardAlertBackend[]>(API_ROUTES.alerts)
      .then((items) => {
        if (cancelled) return;
        const next = createAlertRecords(items);
        setAlerts(next);
        setSelected((current) => next.some((alert) => alert.id === current) ? current : next[0]?.id || "");
        setLoadState("live");
      })
      .catch(() => {
        if (cancelled) return;
        setAlerts(alertRecords);
        setSelected(alertRecords[0].id);
        setLoadState("sample");
      });
    return () => {
      cancelled = true;
    };
  }, [pollTick]);

  const rows = alerts.filter((alert) => filter === "all" || alert.status === filter || alert.sev === filter);
  const item = alerts.find((alert) => alert.id === selected) || alerts[0] || alertRecords[0];
  const numericAlertId = item.id.startsWith("AL-") ? Number(item.id.slice(3)) : Number.NaN;
  const updateAlert = async (action: "ack" | "resolve") => {
    if (!Number.isFinite(numericAlertId)) return;
    setLoadState("updating");
    try {
      await apiRequest(action === "ack" ? API_ROUTES.alertAck(numericAlertId) : API_ROUTES.alertResolve(numericAlertId), { method: "POST" });
      loadAlerts();
    } catch {
      setLoadState("sample");
    }
  };
  const activeCount = alerts.filter((alert) => alert.status === "active").length;
  const criticalCount = alerts.filter((alert) => alert.sev === "critical").length;
  const warningCount = alerts.filter((alert) => alert.sev === "warning").length;
  const ackCount = alerts.filter((alert) => alert.status === "ack").length;
  return h("div", { className: "alert-layout" }, h("div", { className: "page-stack" }, h("div", { className: "alert-summary" }, [["Active", String(activeCount)], ["Critical", String(criticalCount)], ["Warning", String(warningCount)], ["Acknowledged", String(ackCount)]].map(([label, value]) => h("div", { key: label, className: "metric-card" }, h("div", { className: "stat-label" }, label), h("div", { className: "stat-value" }, value)))), h("div", { className: "toolbar" }, h("div", { className: "filter-row" }, (["all", "active", "critical", "warning", "ack", "closed"] as AlertFilter[]).map((key) => h(FilterButton, { key, active: filter === key, onClick: () => setFilter(key) }, key))), h("span", { className: "api-count" }, `GET ${apiLabel(API_ROUTES.alerts)} · ${loadState}`)), h("div", { className: "data-panel" }, h(DataHeader, { className: "alert-grid", columns: ["", "Alert", "Source", "Value", "Age", "Status"] }), rows.map((alert) => h("div", { key: alert.id, className: `data-row alert-grid clickable-row ${alert.id === selected ? "selected-row" : ""}`, onClick: () => setSelected(alert.id) }, h("span", { className: `sev-dot ${alert.sev}` }), h("span", { className: "strong-cell" }, alert.title), h("span", null, alert.source), h("span", { className: "mono-cell" }, alert.value), h("span", { className: "mono-cell muted-cell" }, alert.age), h(StatusTag, { state: alert.status === "active" ? alert.sev : alert.status === "ack" ? "sent" : "offline" }, alert.status)))), h(AlertRulesPanel)), h(PageCard, { title: "Alert Detail" }, h("div", { className: "alert-detail" }, h("h3", null, item.title), h("div", { className: "detail-sub" }, item.id, " · ", item.source), h("div", { className: "detail-grid" }, [["Rule", item.rule], ["Value", item.value], ["Created", item.created], ["Severity", item.sev.toUpperCase()]].map(([label, value]) => h("div", { key: label }, h("div", { className: "stat-label" }, label), h("div", { className: "stat-value small" }, value)))), h("div", { className: "note-box" }, item.note), h("div", { className: "modal-actions inline" }, h("button", { className: "secondary-action", onClick: () => updateAlert("ack") }, "Acknowledge"), h("button", { className: "primary-action", onClick: () => updateAlert("resolve") }, "Resolve & Close")))));
}

function ControlPage() {
  const [node, setNode] = useState("");
  const [targetNodes, setTargetNodes] = useState<string[]>([]);
  const [device, setDevice] = useState("Light");
  const [action, setAction] = useState("Turn ON");
  const [commandsLog, setCommandsLog] = useState(commands);
  const [sendState, setSendState] = useState("ready");
  const pollTick = usePollTick(5000);
  useEffect(() => {
    let cancelled = false;
    apiRequest<BackendControlCommand[]>(API_ROUTES.controlCommands, { query: { limit: 50 } })
      .then((items) => {
        if (!cancelled && items.length > 0) setCommandsLog(items.map(normalizeCommand));
      })
      .catch(() => {
        if (!cancelled) setSendState("backend unavailable");
      });
    apiRequest<BackendSensorNode[]>(API_ROUTES.nodes)
      .then((items) => {
        if (cancelled) return;
        const ids = items.map((item) => item.node_id).filter(Boolean) as string[];
        setTargetNodes(ids);
        setNode((current) => (current && ids.includes(current) ? current : ids[0] || ""));
      })
      .catch(() => {
        if (!cancelled) setTargetNodes([]);
      });
    return () => {
      cancelled = true;
    };
  }, [pollTick]);
  const send = async () => {
    if (!node) {
      setSendState("no target node");
      return;
    }
    const pending: CommandItem = { node, action, device, state: "pending", ago: "now" };
    setCommandsLog((current) => [pending, ...current]);
    setSendState("posting");
    try {
      const created = await apiRequest<BackendControlCommand>(API_ROUTES.controlCommands, {
        method: "POST",
        body: JSON.stringify(
          createControlCommandPayload({
            nodeId: node,
            targetDevice: deviceToApi(device),
            action: actionToApi(action),
            ttlSeconds: 30,
            reason: "frontend remote control",
          }),
        ),
      });
      setSendState("queued");
      setCommandsLog((current) => [normalizeCommand(created), ...current.slice(1)]);
    } catch {
      setSendState("failed");
      setCommandsLog((current) => [{ ...pending, state: "failed" }, ...current.slice(1)]);
    }
  };
  return h("div", { className: "control-layout" }, h(PageCard, { title: "Command Composer" }, h("div", { className: "composer" }, h("div", null, h("div", { className: "filter-label" }, "Target Node"), h("div", { className: "choice-grid" }, targetNodes.length > 0 ? targetNodes.map((id) => h(FilterButton, { key: id, active: node === id, onClick: () => setNode(id) }, id)) : h("span", { className: "muted-cell" }, "no registered nodes"))), h("div", null, h("div", { className: "filter-label" }, "Device"), h("div", { className: "device-choice" }, ["Light", "Fan", "Window", "Buzzer"].map((name) => h(FilterButton, { key: name, active: device === name, onClick: () => setDevice(name) }, name)))), h("div", null, h("div", { className: "filter-label" }, "Action"), h("div", { className: "choice-row" }, ["Turn ON", "Turn OFF", "Toggle", "Open", "Close"].map((name) => h(FilterButton, { key: name, active: action === name, onClick: () => setAction(name) }, name)))), h("div", { className: "mono-cell muted-cell" }, `${device} · ${action} -> ${node} · ${sendState}`), h("button", { className: "primary-action", onClick: send }, "Send command"))), h("div", { className: "page-stack" }, h(PageCard, { title: "Dispatch Chain" }, h("div", { className: "chain" }, ["Web", "Backend", "Outbox", "MQTT", "Gateway", "UART", "ACK"].map((step, index) => h("div", { key: step, className: "chain-step" }, h("span", null, index + 1), h("b", null, step))))), h("div", { className: "data-panel" }, h("div", { className: "section-title" }, `Command Log & ACK Timeline · ${apiLabel(API_ROUTES.controlCommands)}`), h(DataHeader, { className: "cmd-grid", columns: ["Node", "Device", "Action", "TTL", "ACK", "State"] }), commandsLog.map((cmd, index) => h("div", { key: `${cmd.command_id || cmd.node}-${cmd.action}-${index}`, className: "data-row cmd-grid" }, h("span", { className: "mono-cell strong-cell" }, cmd.node), h("span", null, cmd.device), h("span", null, cmd.action), h("span", { className: "mono-cell muted-cell" }, "30s"), h("span", { className: "mono-cell" }, cmd.state === "acked" ? "ACK" : cmd.ago), h("span", { className: "align-right" }, h(StatusTag, { state: cmd.state }, cmd.state)))))));
}

function ModelsPage() {
  const [models, setModels] = useState<ModelItem[]>(modelItems);
  const [loadState, setLoadState] = useState("loading");
  const pollTick = usePollTick(30000);
  useEffect(() => {
    let cancelled = false;
    setLoadState((current) => (current === "live" || current === "live empty" ? current : "loading"));
    apiRequest<BackendModel[]>(API_ROUTES.models)
      .then((items) => {
        if (cancelled) return;
        if (items.length > 0) setModels(items.map(normalizeModel));
        setLoadState(items.length > 0 ? "live" : "live empty");
      })
      .catch(() => {
        if (cancelled) return;
        setModels(modelItems);
        setLoadState("sample");
      });
    return () => {
      cancelled = true;
    };
  }, [pollTick]);
  const activate = async (model: ModelItem) => {
    if (!model.id) return;
    try {
      const active = normalizeModel(await apiRequest<BackendModel>(API_ROUTES.modelActivate(model.id), { method: "POST" }));
      setModels((current) => current.map((item) => (item.id === active.id ? active : item.task === active.task ? { ...item, status: "available" } : item)));
      setLoadState("live");
    } catch {
      setLoadState("activation failed");
    }
  };
  const knownOrder = ["Prediction", "Scene Classification", "Object Detection", "Face Identification", "Anomaly Detection", "Scene Caption"];
  const presentTasks = Array.from(new Set(models.map((model) => model.task)));
  const taskSections = [...knownOrder.filter((task) => presentTasks.includes(task)), ...presentTasks.filter((task) => !knownOrder.includes(task))];
  const sections = taskSections.map((task) =>
    h(
      "div",
      { key: task },
      h("div", { className: "section-heading" }, h("span", null, task), h("em", null, modelTaskDescriptions[task] || "")),
      h(
        "div",
        { className: "model-grid" },
        models.filter((model) => model.task === task).map((model) =>
          h(
            "div",
            { key: model.id || model.name, className: `model-select-card ${model.status}` },
            h("div", { className: "model-title" }, h("span", null, model.name), h(StatusTag, { state: model.status === "failed" ? "failed" : model.status === "deprecated" ? "warning" : model.status === "active" ? "online" : "sent" }, model.status)),
            h("div", { className: "mono-cell muted-cell" }, model.version),
            h("div", { className: "model-kv" }, h("span", null, "Metric"), h("b", null, model.metric), h("span", null, "Latency"), h("b", null, model.latency), h("span", null, "Artifact"), h("code", null, model.hash)),
            h("button", { className: model.status === "active" ? "secondary-action" : "primary-action", onClick: () => activate(model) }, model.status === "active" ? "Active" : "Activate"),
          ),
        ),
      ),
    ),
  );
  return h("div", { className: "page-stack" }, h("div", { className: "data-panel model-runtime" }, h("div", null, h("div", { className: "stat-label" }, "Active Runtime / Backend"), h("div", { className: "mono-cell strong-cell" }, `GET ${apiLabel(API_ROUTES.models)}`)), h("span", { className: "active-badge" }, loadState)), sections);
}

type ChatEntry = {
  id: string;
  role: "user" | "assistant";
  content: string;
  toolCalls: string[];
};

function AssistantPage({ chatbotSettings }: { chatbotSettings: UserSettingsState["chatbot"] }) {
  const [sessionId, setSessionId] = useState("");
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [sendState, setSendState] = useState<"idle" | "sending" | "error">("idle");
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiRequest<{ session_id: string }>(API_ROUTES.chatSessions, { method: "POST" })
      .then((data) => {
        if (!cancelled) setSessionId(data.session_id);
      })
      .catch(() => {
        if (!cancelled) setSendState("error");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const scroller = scrollRef.current;
    if (scroller) scroller.scrollTop = scroller.scrollHeight;
  }, [entries.length, sendState]);

  const send = async () => {
    const content = draft.trim();
    if (!content || !sessionId || sendState === "sending") return;
    setDraft("");
    setEntries((current) => [...current, { id: `local-${Date.now()}`, role: "user", content, toolCalls: [] }]);
    setSendState("sending");
    try {
      const data = await apiRequest<{ message: { content?: string | null }; tool_calls_made?: string[]; error?: string | null }>(
        API_ROUTES.chatMessages(sessionId),
        { method: "POST", body: JSON.stringify({ content }) },
      );
      setEntries((current) => [
        ...current,
        { id: `assistant-${Date.now()}`, role: "assistant", content: data.message.content || "(empty reply)", toolCalls: data.tool_calls_made || [] },
      ]);
      setSendState("idle");
    } catch (error) {
      setEntries((current) => [
        ...current,
        { id: `error-${Date.now()}`, role: "assistant", content: error instanceof Error ? error.message : "chat request failed", toolCalls: [] },
      ]);
      setSendState("error");
    }
  };

  return h(
    "div",
    { className: "page-stack chat-page" },
    h(
      "div",
      { className: "data-panel chat-panel" },
      h("div", { className: "panel-header" }, h("span", { style: { font: "600 13px Inter" } }, "Lab Assistant"), h("span", { className: "api-count" }, `${chatbotSettings.provider_label || "custom"} · ${chatbotSettings.model} · ${sessionId || "connecting…"}`)),
      h(
        "div",
        { className: "chat-scroll", ref: scrollRef },
        entries.length === 0
          ? h("div", { className: "empty-panel" }, h("b", null, "Ask about the lab"), h("span", null, "“What's the temperature right now?” · “Any active alerts?” · “Describe what the camera sees”"))
          : entries.map((entry) =>
              h(
                "div",
                { key: entry.id, className: `chat-entry ${entry.role}` },
                h("div", { className: "chat-bubble" }, entry.content),
                entry.toolCalls.length > 0 ? h("div", { className: "chat-tools" }, `tools: ${entry.toolCalls.join(", ")}`) : null,
              ),
            ),
        sendState === "sending" ? h("div", { className: "chat-entry assistant" }, h("div", { className: "chat-bubble muted-cell" }, "thinking…")) : null,
      ),
      h(
        "div",
        { className: "chat-compose" },
        h("input", {
          value: draft,
          placeholder: sessionId ? "Ask the lab assistant…" : "Connecting…",
          disabled: !sessionId || sendState === "sending",
          onChange: (event: ChangeEvent<HTMLInputElement>) => setDraft(event.target.value),
          onKeyDown: (event: React.KeyboardEvent<HTMLInputElement>) => {
            if (event.key === "Enter") void send();
          },
        }),
        h("button", { className: "primary-action", onClick: () => void send(), disabled: !sessionId || sendState === "sending" || !draft.trim() }, "Send"),
      ),
    ),
  );
}

type ArtifactDraft = {
  bucket: string;
  object_key: string;
  artifact_type: ArtifactType;
  content_type: string;
  size_bytes: string;
  sha256: string;
  etag: string;
  meta: string;
};

const emptyArtifactDraft: ArtifactDraft = { bucket: "", object_key: "", artifact_type: "snapshot", content_type: "", size_bytes: "", sha256: "", etag: "", meta: "" };

function parseArtifactMeta(text: string): Record<string, string> {
  const meta: Record<string, string> = {};
  text.split(/[\n,]/).forEach((pair) => {
    const eq = pair.indexOf("=");
    if (eq > 0) meta[pair.slice(0, eq).trim()] = pair.slice(eq + 1).trim();
  });
  return meta;
}

function ArtifactsPage() {
  const [filter, setFilter] = useState<ArtifactType | "all">("all");
  const [remoteArtifacts, setRemoteArtifacts] = useState<ArtifactItem[] | null>(null);
  const [loadState, setLoadState] = useState("loading");
  const [showForm, setShowForm] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "failed">("idle");
  const [draft, setDraft] = useState<ArtifactDraft>(emptyArtifactDraft);

  const loadArtifacts = () => {
    apiRequest<BackendArtifact[]>(API_ROUTES.artifacts)
      .then((items) => {
        setRemoteArtifacts(items.map(toArtifactItem));
        setLoadState("backend");
      })
      .catch(() => {
        setRemoteArtifacts(null);
        setLoadState("sample");
      });
  };
  useEffect(loadArtifacts, []);

  const registerArtifact = async () => {
    setSaveState("saving");
    try {
      await apiRequest(API_ROUTES.artifacts, {
        method: "POST",
        body: JSON.stringify({
          bucket: draft.bucket.trim(),
          object_key: draft.object_key.trim(),
          artifact_type: draft.artifact_type,
          content_type: draft.content_type.trim() || null,
          size_bytes: draft.size_bytes.trim() ? Number(draft.size_bytes) : null,
          sha256: draft.sha256.trim() || null,
          etag: draft.etag.trim() || null,
          meta: parseArtifactMeta(draft.meta),
        }),
      });
      setSaveState("idle");
      setShowForm(false);
      setDraft(emptyArtifactDraft);
      loadArtifacts();
    } catch {
      setSaveState("failed");
    }
  };

  const openArtifact = async (item: ArtifactItem) => {
    if (item.url && item.url !== "#") {
      window.open(item.url, "_blank", "noopener");
      return;
    }
    try {
      const presigned = await apiRequest<{ url?: string }>(API_ROUTES.artifactPresign(item.id), { method: "POST" });
      if (presigned.url) window.open(presigned.url, "_blank", "noopener");
    } catch {
      // no object store reachable; leave the row as-is
    }
  };

  const artifacts = remoteArtifacts || artifactItems;
  const rows = artifacts.filter((item) => filter === "all" || item.artifact_type === filter);
  const types: Array<ArtifactType | "all"> = ["all", "snapshot", "dataset", "model", "onnx", "benchmark", "report"];
  const artifactTypes = types.slice(1) as ArtifactType[];
  const draftInvalid = !draft.bucket.trim() || !draft.object_key.trim();
  return h("div", { className: "page-stack" },
    h("div", { className: "toolbar" }, h("div", { className: "filter-row" }, types.map((type) => h(FilterButton, { key: type, active: filter === type, onClick: () => setFilter(type) }, `${type} ${type === "all" ? artifacts.length : artifacts.filter((item) => item.artifact_type === type).length}`))), h("span", { className: "active-badge" }, loadState === "backend" ? "POSTGRES" : loadState === "loading" ? "LOADING" : "SAMPLE"), h("button", { className: "primary-action", onClick: () => setShowForm(true) }, "+ Register Artifact")),
    h("div", { className: "data-panel scroll-panel" }, h(DataHeader, { className: "artifact-grid", columns: ["type", "bucket / object_key", "content_type", "sha256", "size", "etag", "meta", "object_key", "link"] }), rows.map((item) => h("div", { key: item.id, className: "data-row artifact-grid" }, h("span", null, h("span", { className: `artifact-pill ${item.artifact_type}` }, item.artifact_type)), h("span", null, h("b", { className: "mono-cell" }, item.object_key), h("small", null, item.bucket)), h("span", { className: "mono-cell muted-cell" }, item.content_type), h("span", { className: "mono-cell muted-cell" }, item.sha256), h("span", { className: "mono-cell strong-cell" }, item.size), h("span", { className: "mono-cell muted-cell" }, item.etag), h("span", { className: "muted-cell" }, item.meta), h("span", { className: "mono-cell muted-cell truncate-cell" }, item.object_key), h("a", { href: item.url === "#" ? undefined : item.url, onClick: (event: MouseEvent) => { event.preventDefault(); void openArtifact(item); } }, "Open ->")))),
    showForm
      ? h("div", { className: "modal-backdrop", onClick: () => setShowForm(false) },
          h("div", { className: "modal-panel", onClick: (event: MouseEvent) => event.stopPropagation() },
            h("div", { className: "modal-title" }, `Register artifact (POST ${apiLabel(API_ROUTES.artifacts)})`),
            h("div", { className: "modal-body" },
              h("label", { className: "field" }, h("span", null, "bucket"), h("input", { className: "mono-input", value: draft.bucket, placeholder: "lab-models", onChange: (event: ChangeEvent<HTMLInputElement>) => setDraft({ ...draft, bucket: event.target.value }) })),
              h("label", { className: "field" }, h("span", null, "object_key"), h("input", { className: "mono-input", value: draft.object_key, placeholder: "models/prediction/mlp-v1.pkl", onChange: (event: ChangeEvent<HTMLInputElement>) => setDraft({ ...draft, object_key: event.target.value }) })),
              h("div", null, h("span", { className: "field-label" }, "artifact_type"), h("div", { className: "choice-row" }, artifactTypes.map((type) => h(FilterButton, { key: type, active: draft.artifact_type === type, onClick: () => setDraft({ ...draft, artifact_type: type }) }, type)))),
              h("label", { className: "field" }, h("span", null, "content_type (optional)"), h("input", { className: "mono-input", value: draft.content_type, placeholder: "application/octet-stream", onChange: (event: ChangeEvent<HTMLInputElement>) => setDraft({ ...draft, content_type: event.target.value }) })),
              h("label", { className: "field" }, h("span", null, "size_bytes (optional)"), h("input", { className: "mono-input", value: draft.size_bytes, placeholder: "1048576", onChange: (event: ChangeEvent<HTMLInputElement>) => setDraft({ ...draft, size_bytes: event.target.value.replace(/[^0-9]/g, "") }) })),
              h("label", { className: "field" }, h("span", null, "sha256 (optional)"), h("input", { className: "mono-input", value: draft.sha256, onChange: (event: ChangeEvent<HTMLInputElement>) => setDraft({ ...draft, sha256: event.target.value }) })),
              h("label", { className: "field" }, h("span", null, "etag (optional)"), h("input", { className: "mono-input", value: draft.etag, onChange: (event: ChangeEvent<HTMLInputElement>) => setDraft({ ...draft, etag: event.target.value }) })),
              h("label", { className: "field" }, h("span", null, "meta (key=value, comma or newline separated)"), h("input", { className: "mono-input", value: draft.meta, placeholder: "task=prediction, active=true", onChange: (event: ChangeEvent<HTMLInputElement>) => setDraft({ ...draft, meta: event.target.value }) })),
            ),
            h("div", { className: "modal-actions" }, saveState === "failed" ? h("span", { className: "muted-cell" }, "register failed") : null, h("button", { className: "secondary-action", onClick: () => setShowForm(false) }, "Cancel"), h("button", { className: "primary-action", disabled: draftInvalid || saveState === "saving", onClick: () => void registerArtifact() }, saveState === "saving" ? "Saving…" : "Register")),
          ),
        )
      : null,
  );
}

function toArtifactItem(item: BackendArtifact): ArtifactItem {
  return {
    id: item.id,
    bucket: item.bucket,
    object_key: item.object_key,
    artifact_type: item.artifact_type,
    content_type: item.content_type || "-",
    size: formatBytes(item.size_bytes),
    sha256: item.sha256 || "-",
    etag: item.etag || "-",
    meta: item.meta ? Object.entries(item.meta).map(([key, value]) => `${key}=${String(value)}`).join(" · ") : "",
    url: item.presigned_url || "#",
  };
}

function formatBytes(value?: number): string {
  if (!value) return "-";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function Pill({ label, color, background }: { label: string; color: string; background: string }) {
  return h("span", { className: "role-pill", style: { color, background } }, label);
}

function UserManagementPage({
  users,
  currentUser,
  onAdd,
  onEdit,
  onToggleStatus,
}: {
  users: UserAccount[];
  currentUser: UserAccount;
  onAdd: () => void;
  onEdit: (user: UserAccount) => void;
  onToggleStatus: (id: number) => void;
}) {
  if (currentUser.role !== "admin") {
    return h("div", { className: "empty-panel" }, h("b", null, "Administrators only"), h("span", null, "Your current role doesn't include access to User Management."));
  }
  const activeCount = users.filter((user) => user.status === "active").length;
  return h(
    "div",
    { className: "page-stack" },
    h("div", { className: "toolbar" }, h("span", { className: "muted-cell" }, `${activeCount} active of ${users.length} accounts · ${apiLabel(API_ROUTES.users)}`), h("button", { className: "primary-action", onClick: onAdd }, "+ Invite User")),
    h(
      "div",
      { className: "data-panel scroll-panel" },
      h(DataHeader, { className: "user-grid", columns: ["Name", "Email", "Role", "Status", "Last login", ""] }),
      users.map((user) =>
        h(
          "div",
          { key: user.id, className: "data-row user-grid" },
          h("span", { className: "strong-cell" }, user.name, user.id === currentUser.id ? h("small", { className: "you-tag" }, " (you)") : null),
          h("span", { className: "mono-cell muted-cell" }, user.email),
          h("span", null, h(Pill, roleMeta[user.role])),
          h("span", null, h(Pill, statusMeta[user.status])),
          h("span", { className: "mono-cell muted-cell" }, user.last_login),
          h("span", { className: "row-actions" }, h("button", { className: "mini-action", onClick: () => onEdit(user) }, "Edit"), h("button", { className: `mini-action ${user.status === "disabled" ? "" : "danger"}`, onClick: () => onToggleStatus(user.id) }, user.status === "disabled" ? "Reactivate" : "Disable")),
        ),
      ),
    ),
  );
}

function UserModal({ user, onClose, onSave, onChange }: { user: UserModalState | null; onClose: () => void; onSave: () => void; onChange: (patch: Partial<UserModalState>) => void }) {
  if (!user) return null;
  const valid = user.name.trim().length > 0 && user.email.trim().length > 0;
  return h(
    "div",
    { className: "modal-backdrop", onClick: onClose },
    h(
      "div",
      { className: "modal-panel user-modal", onClick: (event: React.MouseEvent) => event.stopPropagation() },
      h("div", { className: "modal-title" }, user._isNew ? "Invite user · POST /api/users" : `Edit user · PUT ${apiLabel(API_ROUTES.user(user.id || ""))}`),
      h(
        "div",
        { className: "modal-body" },
        h("label", { className: "field" }, h("span", null, "name"), h("input", { value: user.name, onChange: (event: ChangeEvent<HTMLInputElement>) => onChange({ name: event.target.value }) })),
        h("label", { className: "field" }, h("span", null, "email"), h("input", { value: user.email, onChange: (event: ChangeEvent<HTMLInputElement>) => onChange({ email: event.target.value }) })),
        h("div", null, h("div", { className: "field-label" }, "role"), h("div", { className: "filter-row" }, (["admin", "operator", "viewer"] as UserRole[]).map((role) => h(FilterButton, { key: role, active: user.role === role, onClick: () => onChange({ role }) }, role)))),
        h("div", null, h("div", { className: "field-label" }, "status"), h("div", { className: "filter-row" }, (["active", "invited", "disabled"] as UserStatus[]).map((status) => h(FilterButton, { key: status, active: user.status === status, onClick: () => onChange({ status }) }, status)))),
      ),
      h("div", { className: "modal-actions" }, h("button", { className: "secondary-action", onClick: onClose }, "Cancel"), h("button", { className: "primary-action", disabled: !valid, onClick: onSave }, "Save")),
    ),
  );
}

function SecuritySection({ email }: { email: string }) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [status, setStatus] = useState("");

  const updatePassword = async () => {
    if (!currentPassword || !newPassword) {
      setStatus("both passwords are required");
      return;
    }
    setStatus("updating…");
    try {
      // /api/me has no current-password check, so verify it via login first.
      await apiRequest(API_ROUTES.authLogin, { method: "POST", body: JSON.stringify({ email, password: currentPassword }) });
    } catch {
      setStatus("current password is incorrect");
      return;
    }
    try {
      await apiRequest(API_ROUTES.me, { method: "PUT", body: JSON.stringify({ password: newPassword }) });
      setCurrentPassword("");
      setNewPassword("");
      setStatus("password updated");
    } catch {
      setStatus("update failed — backend unavailable");
    }
  };

  return h("div", { className: "settings-section" },
    h("div", { className: "settings-heading" }, "Security"),
    h("label", { className: "field" }, h("span", null, "Current password"), h("input", { type: "password", placeholder: "password", value: currentPassword, onChange: (event: ChangeEvent<HTMLInputElement>) => setCurrentPassword(event.target.value) })),
    h("label", { className: "field" }, h("span", null, "New password"), h("input", { type: "password", placeholder: "password", value: newPassword, onChange: (event: ChangeEvent<HTMLInputElement>) => setNewPassword(event.target.value) })),
    h("button", { className: "secondary-action", onClick: () => void updatePassword() }, "Update password"),
    status ? h("span", { className: "muted-cell" }, status) : null,
    h("div", { className: "settings-divider" }),
    h("b", { className: "strong-cell" }, "Active session"),
    h("span", { className: "muted-cell" }, "This browser · signed in"));
}

function SettingsModal({
  open,
  tab,
  currentUser,
  draftUser,
  settings,
  themeMode,
  onClose,
  onTab,
  onDraft,
  onSaveProfile,
  onSettings,
  onThemeMode,
  onRole,
}: {
  open: boolean;
  tab: SettingsTab;
  currentUser: UserAccount;
  draftUser: UserAccount;
  settings: UserSettingsState;
  themeMode: ThemeMode;
  onClose: () => void;
  onTab: (tab: SettingsTab) => void;
  onDraft: (patch: Partial<UserAccount>) => void;
  onSaveProfile: () => void;
  onSettings: (settings: UserSettingsState) => void;
  onThemeMode: (mode: ThemeMode) => void;
  onRole: (role: UserRole) => void;
}) {
  if (!open) return null;
  const tabs: SettingsTab[] = ["profile", "preferences", "vision", "assistant", "security", "demo"];
  const role = roleMeta[currentUser.role];
  const updateNotification = (key: keyof UserSettingsState["notifications"]) => onSettings({ ...settings, notifications: { ...settings.notifications, [key]: !settings.notifications[key] } });
  const updateVision = (key: keyof UserSettingsState["vision"], value: number | boolean) => onSettings({ ...settings, vision: normalizeVisionSettings({ ...settings.vision, [key]: value }) });
  const updateChatbot = (key: keyof UserSettingsState["chatbot"], value: string) => onSettings({ ...settings, chatbot: normalizeChatbotSettings({ ...settings.chatbot, [key]: value }) });
  return h(
    "div",
    { className: "modal-backdrop", onClick: onClose },
    h(
      "div",
      { className: "settings-panel", onClick: (event: React.MouseEvent) => event.stopPropagation() },
      h("div", { className: "settings-tabs" }, h("span", null, "Settings"), tabs.map((item) => h("button", { key: item, className: tab === item ? "active" : "", onClick: () => onTab(item) }, item[0].toUpperCase() + item.slice(1)))),
      h(
        "div",
        { className: "settings-content" },
        h(
          "div",
          { className: "settings-scroll" },
          tab === "profile"
            ? h("div", { className: "settings-section" }, h("div", { className: "settings-heading" }, h("span", null, "Profile"), h(Pill, { label: role.label, color: role.color, background: role.background })), h("label", { className: "field" }, h("span", null, "Name"), h("input", { value: draftUser.name, onChange: (event: ChangeEvent<HTMLInputElement>) => onDraft({ name: event.target.value }) })), h("label", { className: "field" }, h("span", null, "Email"), h("input", { value: draftUser.email, onChange: (event: ChangeEvent<HTMLInputElement>) => onDraft({ email: event.target.value }) })), h("button", { className: "primary-action", onClick: onSaveProfile }, "Save changes"))
            : null,
          tab === "preferences"
            ? h("div", { className: "settings-section" }, h("div", { className: "settings-heading" }, "Preferences"), h("div", { className: "pref-row" }, h("div", null, h("b", null, "Appearance"), h("span", null, `Currently ${themeMode}`)), h("div", { className: "segmented compact-segmented" }, (["system", "light", "dark"] as ThemeMode[]).map((mode) => h("button", { key: mode, className: `segment-button ${themeMode === mode ? "active" : ""}`, onClick: () => onThemeMode(mode) }, mode)))), h("div", { className: "settings-divider" }), h("span", { className: "field-label" }, "Notify me on"), ([
                ["critical_alerts", "Critical alerts"],
                ["warning_alerts", "Warning alerts"],
                ["vacant_energy_waste", "Vacant energy-waste notices"],
              ] as Array<[keyof UserSettingsState["notifications"], string]>).map(([key, label]) => h("label", { key, className: "check-row" }, h("span", null, label), h("input", { type: "checkbox", checked: settings.notifications[key], onChange: () => updateNotification(key) }))))
            : null,
          tab === "vision"
            ? h(
                "div",
                { className: "settings-section" },
                h("div", { className: "settings-heading" }, "Vision"),
                h("label", { className: "field" }, h("span", null, "Preview FPS"), h("input", { type: "number", min: 1, max: 30, step: 1, value: settings.vision.preview_fps, onChange: (event: ChangeEvent<HTMLInputElement>) => updateVision("preview_fps", Number(event.target.value)) })),
                h("label", { className: "field" }, h("span", null, "Preview width"), h("input", { type: "number", min: 320, max: 1920, step: 80, value: settings.vision.preview_width, onChange: (event: ChangeEvent<HTMLInputElement>) => updateVision("preview_width", Number(event.target.value)) })),
                h("label", { className: "field" }, h("span", null, "Person inference Hz"), h("input", { type: "number", min: 0.1, max: 30, step: 0.1, value: settings.vision.inference_hz, onChange: (event: ChangeEvent<HTMLInputElement>) => updateVision("inference_hz", Number(event.target.value)) })),
                h("label", { className: "field" }, h("span", null, "Face inference Hz"), h("input", { type: "number", min: 0.1, max: 30, step: 0.1, value: settings.vision.face_inference_hz, onChange: (event: ChangeEvent<HTMLInputElement>) => updateVision("face_inference_hz", Number(event.target.value)) })),
                h("label", { className: "field" }, h("span", null, "Person confidence"), h("input", { type: "number", min: 0.01, max: 0.99, step: 0.01, value: settings.vision.person_confidence, onChange: (event: ChangeEvent<HTMLInputElement>) => updateVision("person_confidence", Number(event.target.value)) })),
                h("label", { className: "field" }, h("span", null, "Anomaly check Hz"), h("input", { type: "number", min: 0.01, max: 30, step: 0.01, value: settings.vision.anomaly_inference_hz, onChange: (event: ChangeEvent<HTMLInputElement>) => updateVision("anomaly_inference_hz", Number(event.target.value)) })),
                h("label", { className: "field" }, h("span", null, "Scene caption every (s)"), h("input", { type: "number", min: 10, max: 86_400, step: 10, value: settings.vision.caption_periodic_seconds, onChange: (event: ChangeEvent<HTMLInputElement>) => updateVision("caption_periodic_seconds", Number(event.target.value)) })),
                h("label", { className: "check-row" }, h("span", null, "Caption scene when an anomaly fires"), h("input", { type: "checkbox", checked: settings.vision.caption_on_anomaly, onChange: () => updateVision("caption_on_anomaly", !settings.vision.caption_on_anomaly) })),
              )
            : null,
          tab === "assistant"
            ? h(
                "div",
                { className: "settings-section" },
                h("div", { className: "settings-heading" }, "Lab Assistant (LLM)"),
                h("span", { className: "muted-cell" }, "Any OpenAI-compatible /chat/completions server works: Ollama, LM Studio, llama.cpp or a hosted API. An API key, if the server needs one, is read from the backend's BREEZIO_LLM_API_KEY environment variable — it is never stored here."),
                h("label", { className: "field" }, h("span", null, "Base URL"), h("input", { value: settings.chatbot.base_url, placeholder: "http://127.0.0.1:11434/v1", onChange: (event: ChangeEvent<HTMLInputElement>) => updateChatbot("base_url", event.target.value) })),
                h("label", { className: "field" }, h("span", null, "Model"), h("input", { value: settings.chatbot.model, placeholder: "qwen2.5:7b", onChange: (event: ChangeEvent<HTMLInputElement>) => updateChatbot("model", event.target.value) })),
                h("label", { className: "field" }, h("span", null, "Provider label"), h("input", { value: settings.chatbot.provider_label, placeholder: "ollama-local", onChange: (event: ChangeEvent<HTMLInputElement>) => updateChatbot("provider_label", event.target.value) })),
              )
            : null,
          tab === "security"
            ? h(SecuritySection, { email: currentUser.email })
            : null,
          tab === "demo"
            ? h("div", { className: "settings-section" }, h("div", { className: "settings-heading" }, "Demo role"), h("span", { className: "muted-cell" }, "For course demonstration: switch the signed-in role to show how admin-only surfaces appear or disappear based on permissions."), h("div", { className: "filter-row" }, (["admin", "operator", "viewer"] as UserRole[]).map((roleId) => h(FilterButton, { key: roleId, active: currentUser.role === roleId, onClick: () => onRole(roleId) }, roleId))))
            : null,
        ),
        h("div", { className: "modal-actions" }, h("button", { className: "secondary-action", onClick: onClose }, "Close")),
      ),
    ),
  );
}

function AuthPage({
  mode,
  form,
  error,
  users,
  onMode,
  onForm,
  onSubmit,
}: {
  mode: AuthMode;
  form: AuthFormState;
  error: string;
  users: UserAccount[];
  onMode: (mode: AuthMode) => void;
  onForm: (patch: Partial<AuthFormState>) => void;
  onSubmit: () => void;
}) {
  const isRegister = mode === "register";
  const passwordMismatch = isRegister && form.confirmPassword.length > 0 && form.password !== form.confirmPassword;
  const ready = form.email.trim().length > 0 && form.password.trim().length > 0 && (!isRegister || (form.name.trim().length > 0 && form.confirmPassword.trim().length > 0 && !passwordMismatch));
  const activeUsers = users.filter((user) => user.status === "active").length;
  const submitLabel = isRegister ? "Create account" : "Sign in";
  const switchLabel = isRegister ? "Already have an account?" : "New to the lab?";
  const switchAction = isRegister ? "Sign in" : "Create account";

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (ready) onSubmit();
  };

  return h(
    "main",
    { className: "auth-shell" },
    h(
      "section",
      { className: "auth-card" },
      h(
        "div",
        { className: "auth-panel auth-form-panel" },
        h("div", { className: "auth-brand-row" }, h("div", { className: "brand-mark auth-mark" }, "LX"), h("div", null, h("div", { className: "brand-title" }, "Smart Lab"), h("div", { className: "brand-subtitle" }, "IoT Monitor & Control"))),
        h("div", { className: "auth-heading" }, isRegister ? "Register account" : "Welcome back"),
        h("div", { className: "auth-subtitle" }, isRegister ? "Create an operator account for the lab dashboard." : "Sign in to continue to the laboratory dashboard."),
        h(
          "form",
          { className: "auth-form", onSubmit: submit },
          isRegister ? h("label", { className: "field" }, h("span", null, "Name"), h("input", { value: form.name, autoComplete: "name", onChange: (event: ChangeEvent<HTMLInputElement>) => onForm({ name: event.target.value }) })) : null,
          h("label", { className: "field" }, h("span", null, "Email"), h("input", { type: "email", value: form.email, autoComplete: "email", onChange: (event: ChangeEvent<HTMLInputElement>) => onForm({ email: event.target.value }) })),
          h("label", { className: "field" }, h("span", null, "Password"), h("input", { type: "password", value: form.password, autoComplete: isRegister ? "new-password" : "current-password", onChange: (event: ChangeEvent<HTMLInputElement>) => onForm({ password: event.target.value }) })),
          isRegister ? h("label", { className: "field" }, h("span", null, "Confirm password"), h("input", { type: "password", value: form.confirmPassword, autoComplete: "new-password", onChange: (event: ChangeEvent<HTMLInputElement>) => onForm({ confirmPassword: event.target.value }) })) : null,
          passwordMismatch ? h("div", { className: "auth-error" }, "Passwords do not match.") : null,
          error ? h("div", { className: "auth-error" }, error) : null,
          isRegister ? h("div", null, h("div", { className: "field-label" }, "Role"), h("div", { className: "filter-row" }, (["operator", "viewer", "admin"] as UserRole[]).map((role) => h(FilterButton, { key: role, active: form.role === role, onClick: () => onForm({ role }) }, role)))) : null,
          h("label", { className: "auth-check" }, h("input", { type: "checkbox", checked: form.remember, onChange: (event: ChangeEvent<HTMLInputElement>) => onForm({ remember: event.target.checked }) }), h("span", null, "Keep this session on this device")),
          h("button", { className: "primary-action auth-submit", disabled: !ready }, submitLabel),
        ),
        h("div", { className: "auth-switch" }, h("span", null, switchLabel), h("button", { onClick: () => onMode(isRegister ? "login" : "register") }, switchAction)),
      ),
      h(
        "div",
        { className: "auth-panel auth-status-panel" },
        h("div", { className: "auth-preview-title" }, "Smart Laboratory IoT Dashboard"),
        h("div", { className: "auth-preview-grid" }, h("div", null, h("b", null, String(activeUsers)), h("span", null, "active users")), h("div", null, h("b", null, "99.6%"), h("span", null, "uptime")), h("div", null, h("b", null, "5"), h("span", null, "alerts"))),
        h(
          "div",
          { className: "auth-systems" },
          ["Gateway", "MQTT", "Backend", "Storage"].map((name) => h("div", { key: name, className: "auth-system-row" }, h("span", { className: "dot", style: { background: name === "Storage" ? "#e08206" : "#129d6b", animation: "none" } }), h("span", null, name), h("code", null, name === "Storage" ? "SYNC" : "LIVE"))),
        ),
        h("div", { className: "auth-camera-mini" }, h("div", { className: "camera-grid" }), h("div", { className: "scan-line" }), h("div", { className: "box", style: { left: "33%", top: "28%", width: "18%", height: "46%" } }, h("span", { className: "box-tag" }, "person")), h("div", { className: "scene-row" }, h("span", { className: "scene-badge crowded" }, "Crowded"), h("span", { className: "mono-chip" }, "3 people"))),
      ),
    ),
  );
}

function Dashboard({ visionSettings }: { visionSettings: UserSettingsState["vision"] }) {
  const [dashboardKpis, setDashboardKpis] = useState<Kpi[]>(sampleKpis);
  const [camera, setCamera] = useState<LogicalDevice | null>(null);
  const [loadState, setLoadState] = useState<"loading" | "live" | "sample">("loading");
  const pollTick = usePollTick(5000);

  useEffect(() => {
    let cancelled = false;
    setLoadState((current) => (current === "live" ? current : "loading"));
    apiRequest<DashboardSummary>(API_ROUTES.summary)
      .then((summary) => {
        if (cancelled) return;
        setDashboardKpis(createDashboardKpis(summary, { temperature: series.temp.slice(-8), humidity: series.hum.slice(-8), light: series.light.slice(-8) }));
        setLoadState("live");
      })
      .catch(() => {
        if (cancelled) return;
        setDashboardKpis(sampleKpis);
        setLoadState("sample");
      });
    apiRequest<BackendDevice[]>(API_ROUTES.devices)
      .then((items) => {
        if (cancelled) return;
        setCamera(items.map(normalizeLogicalDevice).find((device) => device.device_type === "camera") || null);
      })
      .catch(() => {
        if (cancelled) return;
        setCamera(null);
      });
    return () => {
      cancelled = true;
    };
  }, [pollTick]);

  return h(
    "div",
    { className: "dashboard" },
    h("div", { className: "api-count dashboard-source" }, `GET ${apiLabel(API_ROUTES.summary)} · ${loadState}`),
    h("div", { className: "kpi-strip" }, dashboardKpis.map((item) => h(KpiCard, { key: item.label, item }))),
    h("div", { className: "dashboard-grid" }, h(CameraPanel, { device: camera, visionSettings }), h(EnvironmentCard, { kpis: dashboardKpis }), h(ModelColumn)),
    h(BottomLists),
  );
}

function Placeholder({ view }: { view: ViewId }) {
  const [title, sub] = titles[view];
  return h("div", { className: "placeholder" }, h("div", { className: "placeholder-inner" }, h("div", { className: "placeholder-icon" }, navItems.find((item) => item.id === view)?.icon), h("div", { className: "placeholder-title" }, title), h("div", { className: "placeholder-copy" }, sub)));
}

function App() {
  const [view, setView] = useState<ViewId>(() => viewFromHash() ?? "dashboard");
  const [users, setUsers] = useState(initialUsers);
  const [currentUser, setCurrentUser] = useState(initialUsers[0]);
  const [draftUser, setDraftUser] = useState(initialUsers[0]);
  const [userModal, setUserModal] = useState<UserModalState | null>(null);
  const [isAuthenticated, setIsAuthenticated] = useState(() => !!storedAccessToken());
  const [authMode, setAuthMode] = useState<AuthMode>(getInitialAuthMode);
  const [authForm, setAuthForm] = useState<AuthFormState>({
    name: "",
    email: "",
    password: "",
    confirmPassword: "",
    role: "operator",
    remember: true,
  });
  const [authError, setAuthError] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("profile");
  const [themeMode, setThemeMode] = useState<ThemeMode>(storedThemeMode);
  const [systemTheme, setSystemTheme] = useState<"light" | "dark">(getInitialSystemTheme);
  const [settings, setSettings] = useState<UserSettingsState>({
    notifications: { critical_alerts: true, warning_alerts: true, vacant_energy_waste: false },
    vision: DEFAULT_VISION_SETTINGS,
    chatbot: DEFAULT_CHATBOT_SETTINGS,
  });
  const resolvedTheme = themeMode === "system" ? systemTheme : themeMode;

  // Keep the URL hash in sync with the active page and honor back/forward or
  // manually edited hashes, so refreshing stays on the same page.
  useEffect(() => {
    if (!isAuthenticated) return;
    if (window.location.hash !== `#/${view}`) {
      window.history.replaceState(null, "", `#/${view}`);
    }
  }, [view, isAuthenticated]);

  useEffect(() => {
    const onHashChange = () => {
      const next = viewFromHash();
      if (next) setView(next);
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const update = () => setSystemTheme(media.matches ? "dark" : "light");
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = resolvedTheme;
    window.localStorage.setItem("smart-lab-theme-mode", themeMode);
  }, [resolvedTheme, themeMode]);

  useEffect(() => {
    if (isAuthenticated) return;
    const updateAuthMode = () => setAuthMode(getInitialAuthMode());
    updateAuthMode();
    window.addEventListener("hashchange", updateAuthMode);
    return () => window.removeEventListener("hashchange", updateAuthMode);
  }, [isAuthenticated]);

  useEffect(() => {
    let cancelled = false;
    if (storedAccessToken()) {
      apiRequest<BackendUser>(API_ROUTES.me)
        .then((user) => {
          if (cancelled) return;
          const next = normalizeUser(user);
          setCurrentUser(next);
          setDraftUser(next);
          setIsAuthenticated(true);
        })
        .catch(() => {
          if (!cancelled) {
            clearAccessToken();
            setIsAuthenticated(false);
          }
        });
      apiRequest<BackendUser[]>(API_ROUTES.users)
        .then((items) => {
          if (!cancelled && items.length > 0) setUsers(items.map(normalizeUser));
        })
        .catch(() => undefined);
      apiRequest<BackendUserSettings>(API_ROUTES.mySettings)
        .then((remote) => {
          if (cancelled) return;
          if (remote.theme_mode) setThemeMode(remote.theme_mode);
          setSettings((current) => ({
            notifications: { ...current.notifications, ...(remote.notifications || {}) },
            vision: normalizeVisionSettings({ ...current.vision, ...(remote.vision || {}) }),
            chatbot: normalizeChatbotSettings({ ...current.chatbot, ...(remote.chatbot || {}) }),
          }));
        })
        .catch(() => undefined);
    }
    return () => {
      cancelled = true;
    };
  }, []);

  const openSettings = (tab: SettingsTab = "profile") => {
    setDraftUser(currentUser);
    setSettingsTab(tab);
    setSettingsOpen(true);
  };
  const submitAuth = async () => {
    const email = authForm.email.trim().toLowerCase();
    const password = authForm.password;
    setAuthError("");
    try {
      const response = await apiRequest<AuthResponse>(authMode === "register" ? API_ROUTES.authRegister : API_ROUTES.authLogin, {
        method: "POST",
        body: JSON.stringify(
          authMode === "register"
            ? { name: authForm.name.trim(), email, password, role: authForm.role }
            : { email, password },
        ),
      });
      storeAccessToken(response.access_token, authForm.remember);
      const next = normalizeUser(response.user);
      setCurrentUser(next);
      setDraftUser(next);
      setUsers((list) => {
        const exists = list.some((user) => user.id === next.id);
        return exists ? list.map((user) => (user.id === next.id ? next : user)) : [...list, next];
      });
      setView("dashboard");
      setSettingsOpen(false);
      setIsAuthenticated(true);
      window.history.replaceState(null, "", window.location.pathname);
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : "Authentication failed");
    }
  };
  const cycleTheme = () => setThemeMode((current) => (current === "system" ? "light" : current === "light" ? "dark" : "system"));
  const setSignedInRole = (role: UserRole) => {
    setCurrentUser((user) => ({ ...user, role }));
    setUsers((list) => list.map((user) => (user.id === currentUser.id ? { ...user, role } : user)));
    if (role !== "admin" && view === "users") setView("dashboard");
  };
  const saveProfile = async () => {
    try {
      const saved = await apiRequest<BackendUser>(API_ROUTES.me, {
        method: "PUT",
        body: JSON.stringify({ name: draftUser.name, email: draftUser.email }),
      });
      const next = normalizeUser(saved);
      setCurrentUser(next);
      setUsers((list) => list.map((user) => (user.id === next.id ? next : user)));
    } catch {
      setCurrentUser(draftUser);
      setUsers((list) => list.map((user) => (user.id === draftUser.id ? draftUser : user)));
    }
    setSettingsOpen(false);
  };
  const saveUser = async () => {
    if (!userModal) return;
    try {
      const saved = userModal._isNew
        ? await apiRequest<BackendUser>(API_ROUTES.users, {
            method: "POST",
            body: JSON.stringify({ name: userModal.name, email: userModal.email, role: userModal.role }),
          })
        : await apiRequest<BackendUser>(API_ROUTES.user(userModal.id || 0), {
            method: "PUT",
            body: JSON.stringify({ name: userModal.name, email: userModal.email, role: userModal.role, status: userModal.status }),
          });
      const next = normalizeUser(saved);
      setUsers((list) => (userModal._isNew ? [...list, next] : list.map((user) => (user.id === next.id ? next : user))));
    } catch {
      setUsers((list) => {
        if (!userModal._isNew && userModal.id) return list.map((user) => (user.id === userModal.id ? { ...user, ...userModal } : user));
        const nextId = Math.max(...list.map((user) => user.id)) + 1;
        return [...list, { id: nextId, name: userModal.name, email: userModal.email, role: userModal.role, status: userModal.status, last_login: "-" }];
      });
    }
    setUserModal(null);
  };
  const setRemoteSettings = (next: UserSettingsState) => {
    const normalized = { ...next, vision: normalizeVisionSettings(next.vision), chatbot: normalizeChatbotSettings(next.chatbot) };
    setSettings(normalized);
    apiRequest(API_ROUTES.mySettings, { method: "PUT", body: JSON.stringify(normalized) }).catch(() => undefined);
  };
  const setRemoteThemeMode = (mode: ThemeMode) => {
    setThemeMode(mode);
    apiRequest(API_ROUTES.mySettings, { method: "PUT", body: JSON.stringify({ theme_mode: mode }) }).catch(() => undefined);
  };
  const toggleUserStatus = async (id: number) => {
    const user = users.find((item) => item.id === id);
    if (!user) return;
    const nextStatus: UserStatus = user.status === "disabled" ? "active" : "disabled";
    setUsers((list) => list.map((item) => (item.id === id ? { ...item, status: nextStatus } : item)));
    try {
      if (nextStatus === "disabled") {
        await apiRequest(API_ROUTES.user(id), { method: "DELETE" });
      } else {
        const saved = await apiRequest<BackendUser>(API_ROUTES.user(id), { method: "PUT", body: JSON.stringify({ status: "active" }) });
        const next = normalizeUser(saved);
        setUsers((list) => list.map((item) => (item.id === id ? next : item)));
      }
    } catch {
      setUsers((list) => list.map((item) => (item.id === id ? user : item)));
    }
  };
  const pages: Record<ViewId, ReactNode> = {
    dashboard: h(Dashboard, { visionSettings: settings.vision }),
    devices: h(DevicesPage),
    history: h(HistoryPage),
    topology: h(TopologyPage),
    environment: h(EnvironmentPage),
    vision: h(VisionPage, { visionSettings: settings.vision }),
    prediction: h(PredictionPage),
    alerts: h(AlertsPage),
    control: h(ControlPage),
    assistant: h(AssistantPage, { chatbotSettings: settings.chatbot }),
    models: h(ModelsPage),
    artifacts: h(ArtifactsPage),
    users: h(UserManagementPage, {
      users,
      currentUser,
      onAdd: () => setUserModal({ name: "", email: "", role: "operator", status: "invited", _isNew: true }),
      onEdit: (user: UserAccount) => setUserModal(user),
      onToggleStatus: toggleUserStatus,
    }),
  };
  const content = pages[view] || h(Placeholder, { view });
  if (!isAuthenticated) {
    return h(AuthPage, {
      mode: authMode,
      form: authForm,
      error: authError,
      users,
      onMode: (mode: AuthMode) => {
        window.history.replaceState(null, "", `#${mode}`);
        setAuthMode(mode);
        setAuthError("");
        setAuthForm((form) => ({ ...form, confirmPassword: "" }));
      },
      onForm: (patch: Partial<AuthFormState>) => {
        setAuthError("");
        setAuthForm((form) => ({ ...form, ...patch }));
      },
      onSubmit: submitAuth,
    });
  }
  return h(
    "div",
    { className: "app-shell" },
    h(Sidebar, {
      view,
      setView,
      currentUser,
      onOpenSettings: () => openSettings("profile"),
      onSignOut: () => {
        clearAccessToken();
        window.history.replaceState(null, "", "#login");
        setIsAuthenticated(false);
      },
    }),
    h("div", { className: "main" }, h(Topbar, { view, themeMode, resolvedTheme, onToggleTheme: cycleTheme, onOpenSettings: () => openSettings("profile") }), h("div", { className: "viewport" }, content)),
    h(UserModal, { user: userModal, onClose: () => setUserModal(null), onSave: saveUser, onChange: (patch: Partial<UserModalState>) => setUserModal((user) => (user ? { ...user, ...patch } : user)) }),
    h(SettingsModal, {
      open: settingsOpen,
      tab: settingsTab,
      currentUser,
      draftUser,
      settings,
      themeMode,
      onClose: () => setSettingsOpen(false),
      onTab: setSettingsTab,
      onDraft: (patch: Partial<UserAccount>) => setDraftUser((user) => ({ ...user, ...patch })),
      onSaveProfile: saveProfile,
      onSettings: setRemoteSettings,
      onThemeMode: setRemoteThemeMode,
      onRole: setSignedInRole,
    }),
  );
}

const root = document.getElementById("root");
if (!root) throw new Error("Missing #root");
createRoot(root).render(h(App));
