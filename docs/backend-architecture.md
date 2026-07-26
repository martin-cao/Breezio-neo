# 后端解耦设计

## 1. 目标

后端需要在课程周期内保持可开发、可演示，同时避免把 MQTT 消费、控制状态机、预测训练、视觉事件、对象存储和 Web API 全部挤进一个 `app.py`。本项目采用“模块化单体 + 独立 worker”的方式解耦：部署上仍然简单，但代码边界清晰，后续可以按模块拆成服务。

## 2. 总体结构

```text
backend/
  app/
    api/                 # Flask Blueprint，只处理 HTTP DTO 和鉴权
    modules/
      identity/
      device_registry/
      telemetry/
      control/
      alerting/
      prediction/
      vision/
      model_registry/
      artifact_store/
    shared/
      db/
      mqtt/
      storage/
      events/
      config/
  workers/
    mqtt_ingestor.py
    control_dispatcher.py
    prediction_scheduler.py
    artifact_uploader.py
  migrations/
  tests/
```

## 3. 模块边界

| 模块 | 职责 | 不应该做的事 |
| --- | --- | --- |
| `identity` | 用户、JWT、角色权限 | 不直接访问设备或模型业务 |
| `device_registry` | 网关、节点、传感器、摄像头元数据 | 不处理原始 MQTT 消费 |
| `telemetry` | 传感器读数入库、当前状态、查询 | 不下发控制命令 |
| `control` | 控制命令状态机、ACK、重试、超时 | 不直接读摄像头帧 |
| `alerting` | 告警规则、告警生命周期、告警快照引用 | 不训练模型 |
| `prediction` | 特征构造、训练运行、在线预测、指标 | 不维护设备注册 |
| `vision` | 视觉事件入库、摄像头状态、检测结果查询 | 不执行重型推理训练 |
| `model_registry` | 模型版本、benchmark、准入状态 | 不保存大文件本体 |
| `artifact_store` | S3-compatible 对象索引、预签名 URL、spool 补传 | 不承载业务判断 |

## 4. 分层约束

每个模块内部按四层组织：

```text
api -> application service -> domain model/policy -> infrastructure adapter
```

- `api`：Flask Blueprint，负责参数校验、响应格式、鉴权。
- `application service`：用例编排，例如 `CreateControlCommand`、`IngestTelemetryBatch`。
- `domain model/policy`：状态机、阈值规则、模型准入规则等纯业务逻辑。
- `infrastructure adapter`：SQLAlchemy repository、MQTT publisher、S3 client、外部 AI client。

API 层不能直接调用 SQLAlchemy model 完成复杂业务；必须通过 application service。这样后续改框架或拆 worker 时影响较小。

## 5. 进程边界

首版建议拆成以下进程：

| 进程 | 内容 | 说明 |
| --- | --- | --- |
| `web-api` | Flask REST API、WebSocket/SSE | 用户请求入口 |
| `mqtt-ingestor` | 订阅 telemetry、vision、ACK topic | 避免 MQTT 消费阻塞 Web API |
| `control-dispatcher` | pending 命令发布、重试、超时扫描 | 控制链路独立可观测 |
| `prediction-worker` | 定时预测、训练/评估任务 | 避免模型任务占用 API 进程 |
| `artifact-uploader` | 本地 spool 到 RustFS/S3 补传 | 对象存储故障兜底 |
| `inference-service` | 摄像头、YOLO、自采模型推理 | 独立进程，不放在 Flask 内 |
| `mlsys-benchmark-worker` | ONNX 导出、图优化、算子融合、runtime benchmark | 可离线运行，不阻塞实时推理 |

部署上可以用 Docker Compose 启动多个容器，也可以本地用多个命令启动。它不是微服务化，数据库仍是一个 PostgreSQL，模块通过表和事件边界协作。

## 6. 事件与一致性

### 6.1 内部事件

使用 PostgreSQL 表记录关键业务事件：

- `telemetry_ingested`
- `node_offline_detected`
- `alert_created`
- `control_command_created`
- `control_command_acked`
- `prediction_generated`
- `vision_event_ingested`
- `artifact_uploaded`

首版可以使用简单 `events` 表：

```sql
CREATE TABLE events (
  id BIGSERIAL PRIMARY KEY,
  event_id UUID NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  aggregate_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  payload_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  processed_at TIMESTAMPTZ
);
```

### 6.2 Outbox

需要“写数据库 + 发 MQTT”的操作使用 outbox，避免数据库成功但 MQTT 发布失败：

```sql
CREATE TABLE outbox_messages (
  id BIGSERIAL PRIMARY KEY,
  message_id UUID NOT NULL UNIQUE,
  topic TEXT NOT NULL,
  payload_json JSONB NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  retry_count INT NOT NULL DEFAULT 0,
  next_retry_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  sent_at TIMESTAMPTZ
);
```

`control-dispatcher` 扫描 outbox，发布成功后标记 `sent`。这比在 HTTP 请求里直接发布 MQTT 更稳。

## 7. 模块间调用规则

- Web API 可以调用 application service，但不能直接发布 MQTT。
- MQTT ingestor 可以调用 `telemetry`、`vision`、`control` 的 ingestion service。
- `prediction` 读取 `telemetry` 的查询接口或 repository，不直接修改节点状态。
- `alerting` 可以消费 `telemetry`、`vision`、`prediction` 事件，创建告警。
- `control` 是唯一能创建和推进控制命令状态机的模块。
- `artifact_store` 是唯一能生成对象 key、登记 `object_artifacts` 的模块。

## 8. API 模块拆分

HTTP API 按业务边界拆分：

| API 模块 | 路径前缀 | 说明 |
| --- | --- | --- |
| System | `/api/health`, `/api/dashboard` | 健康检查、首页摘要 |
| Registry | `/api/devices`, `/api/gateways`, `/api/nodes`, `/api/cameras` | 元数据管理 |
| Telemetry | `/api/readings`, `/api/topology` | 读数、拓扑、当前状态 |
| Control & Alerts | `/api/control`, `/api/alerts` | 控制命令和告警 |
| Prediction | `/api/prediction` | 训练、评估、在线预测 |
| Vision AI | `/api/vision` | 视觉事件和摄像头推理结果 |
| Model & Artifact | `/api/models`, `/api/artifacts` | 模型版本、对象存储 |

AI Infra 是底层优化架构，不属于用户交互 API 模块。ONNX 导出、图优化、算子融合、benchmark 和后端准入由内部脚本、worker 或 CI 管道执行，并将结果写入 PostgreSQL/RustFS 供系统运行时使用。

`docs/openapi.yaml` 保留为聚合版；模块化设计见 `docs/api/`。

## 9. 后续拆分路线

如果项目时间允许，可以按风险从低到高拆分：

1. `inference-service` 独立进程，已经建议首版拆。
2. `mqtt-ingestor` 独立 worker。
3. `prediction-worker` 独立 worker。
4. `artifact-uploader` 独立 worker。
5. 若后续系统规模变大，再考虑把 `control` 和 `model_registry` 拆成独立服务。

不要在首版把 Flask 后端拆成多个数据库独立的微服务。课程项目更需要可演示和可解释的稳定闭环。
