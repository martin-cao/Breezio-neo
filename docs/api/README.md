# API 模块设计

API 按业务边界拆分。`docs/openapi.yaml` 是聚合版，便于 Swagger UI、Redoc 和校验工具直接读取；本目录是模块化设计入口，开发时按模块实现 Flask Blueprint。

维护约定：模块文件是接口设计源，聚合版 `docs/openapi.yaml` 是工具入口。后续如果接口继续扩展，应先改对应模块，再同步聚合版，避免一个大文件承载所有设计讨论。

## 模块清单

| 模块 | 文件 | Flask Blueprint | 路径前缀 |
| --- | --- | --- | --- |
| System | [system.yaml](./system.yaml) | `system_bp` | `/api/health`, `/api/dashboard` |
| Registry | [registry.yaml](./registry.yaml) | `registry_bp` | `/api/devices`, `/api/gateways`, `/api/nodes`, `/api/cameras` |
| Telemetry | [telemetry.yaml](./telemetry.yaml) | `telemetry_bp` | `/api/readings`, `/api/topology` |
| Control & Alerts | [control-alerts.yaml](./control-alerts.yaml) | `control_bp`, `alerts_bp` | `/api/control`, `/api/alerts` |
| Prediction | [prediction.yaml](./prediction.yaml) | `prediction_bp` | `/api/prediction` |
| Vision AI | [vision-ai.yaml](./vision-ai.yaml) | `vision_bp` | `/api/vision` |
| Models & Artifacts | [models-artifacts.yaml](./models-artifacts.yaml) | `models_bp`, `artifacts_bp` | `/api/models`, `/api/artifacts` |
| Users & Settings | [users-settings.yaml](./users-settings.yaml) | `users_bp`, `settings_bp` | `/api/me`, `/api/me/settings`, `/api/users` |

## 统一约定

- 所有业务响应使用 `{ "success": true, "data": ... }`。
- 错误响应使用 `{ "success": false, "error": "...", "message": "..." }`。
- 时间统一使用 ISO 8601 带时区字符串。
- 写接口生成幂等 ID，例如 `message_id`、`command_id`、`event_id`。
- MQTT 是设备和 AI 的主实时链路；REST API 主要服务 Web 页面、管理操作和测试回放。
- AI infra 属于底层优化架构，不作为用户或 Web-facing REST API 模块暴露；相关导出、优化、benchmark 和准入流程应由内部脚本、worker 或 CI 管道写入 PostgreSQL/RustFS 产物。

## 实现建议

每个 API 模块只负责 HTTP DTO 和鉴权，然后调用对应 application service。不要在 Blueprint 中直接写复杂 SQL、发布 MQTT 或调用模型推理。
