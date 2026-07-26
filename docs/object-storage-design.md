# 对象存储选型

## 1. 需求

本项目需要一个 S3-compatible 对象存储来保存非结构化产物：

- 摄像头告警快照；
- 自采图像数据集；
- 模型权重；
- ONNX 文件；
- benchmark JSON；
- 混淆矩阵和训练曲线；
- 答辩报告附件。

约束：

- 必须开源；
- 部署不能太重；
- 能被 Python 后端通过 S3 SDK 访问；
- 首版单机可运行，后续可扩展；
- 不把大对象直接写入 PostgreSQL。

## 2. 选型结论

默认选型：RustFS S3-compatible endpoint。

RustFS 是 Rust 实现的高性能对象存储，官方 README 标注 Apache-2.0 许可证、S3 compatibility、single node mode 和 Docker quick start。它比 Ceph RGW 轻，也比 MinIO 更符合许可证约束；同时它和 Rust Gateway 的技术栈一致，适合作为本项目默认对象存储。

不默认使用 MinIO。虽然 MinIO 是 S3-compatible 对象存储，但当前许可证为 AGPLv3；对课程项目来说，使用 Apache-2.0 的 RustFS 作为默认依赖更稳。Ceph RGW 功能完整，但部署和运维明显过重，不适合本项目首版。

## 3. 候选比较

| 方案 | 许可证 | S3 兼容 | 部署重量 | 适合作为默认 | 评价 |
| --- | --- | --- | --- | --- | --- |
| RustFS | Apache-2.0 | 支持 S3-compatible API | 轻 | 是 | Rust 实现、single node mode、Docker quick start、与 Rust Gateway 技术栈一致 |
| SeaweedFS | Apache-2.0 | 支持 S3 endpoint | 轻 | 备选 | 单机/容器启动简单，许可风险低 |
| Garage | AGPLv3 | 支持 S3-compatible API | 轻 | 备选 | 分布式设计漂亮，但 AGPL 与 S3 子集限制需说明 |
| Ceph RGW | LGPL/GPL 组合 | 支持 S3 API | 重 | 否 | 生产级但不适合课程项目首版 |
| OpenStack Swift + S3 API | Apache-2.0 | 可通过兼容层支持 | 重 | 否 | 架构完整但部署成本高 |
| MinIO | AGPLv3 | 支持 S3 API | 中 | 否 | 功能强，但不作为本项目默认依赖 |

## 4. RustFS 部署方案

### 4.1 开发环境

RustFS 官方 Docker quick start 需要将挂载目录授权给容器内 `rustfs` 用户。开发环境可按以下方式启动：

```bash
mkdir -p data logs
chown -R 10001:10001 data logs

docker run -d --name breezio-rustfs \
  -p 9000:9000 \
  -p 9001:9001 \
  -v "$(pwd)/data:/data" \
  -v "$(pwd)/logs:/logs" \
  rustfs/rustfs:latest
```

S3 endpoint：

```text
http://localhost:9000
```

后端环境变量：

```text
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY_ID=breezio
S3_SECRET_ACCESS_KEY=change-me
S3_REGION=local
S3_FORCE_PATH_STYLE=true
```

实际 access key 和 secret key 以 RustFS 部署配置为准。开发脚本中应统一创建 bucket 和凭据，避免在代码中硬编码。

### 4.2 Bucket 规划

| Bucket | 内容 |
| --- | --- |
| `breezio-snapshots` | 告警快照、视觉事件样例 |
| `breezio-datasets` | 自采训练数据、manifest、标签文件 |
| `breezio-models` | PyTorch 权重、sklearn 模型、ONNX 文件 |
| `breezio-benchmarks` | benchmark JSON、性能图、ONNX 图分析 |
| `breezio-reports` | 答辩报告附件、导出图表 |

### 4.3 Object Key 规范

```text
snapshots/{camera_id}/{yyyy}/{mm}/{dd}/{event_id}.jpg
datasets/{dataset_key}/{version}/manifest.csv
datasets/{dataset_key}/{version}/images/{split}/{label}/{image_id}.jpg
models/{model_key}/{version}/model.pt
models/{model_key}/{version}/model.onnx
benchmarks/{model_key}/{version}/{backend}/result.json
reports/{yyyy}/{mm}/{artifact_name}
```

## 5. 后端集成

Flask 后端使用 `boto3` 或兼容 S3 SDK：

```python
import boto3

s3 = boto3.client(
    "s3",
    endpoint_url=app.config["S3_ENDPOINT_URL"],
    aws_access_key_id=app.config["S3_ACCESS_KEY_ID"],
    aws_secret_access_key=app.config["S3_SECRET_ACCESS_KEY"],
    region_name=app.config.get("S3_REGION", "local"),
)
```

数据库只保存 `bucket`、`object_key`、`sha256`、`etag`、`size_bytes`、`artifact_type` 和 `meta_json`。

## 6. 故障兜底

- 对象存储不可用时，AI 服务先写入本地 `spool/objects`。
- 后端定时任务扫描 spool 并补传。
- 上传成功后写入 `object_artifacts`。
- 告警页面若对象还未上传，只展示本地 pending 状态。

## 7. 答辩说明

答辩时可以强调：

- 使用 S3-compatible API，后端不绑定某一家对象存储实现。
- 首版选 RustFS 是因为 Apache-2.0、Rust 实现、S3-compatible、single node mode 和 Docker 部署简单。
- 如果未来进入生产环境，可以替换为 Ceph RGW、云厂商 S3 或其他兼容实现，业务表结构不需要变化。

## 8. 参考依据

- RustFS README：说明 RustFS 是 Rust 实现的高性能分布式对象存储，支持 S3 compatibility、single node mode 和 Docker quick start。
- RustFS LICENSE：许可证为 Apache License 2.0。
- SeaweedFS README：说明 SeaweedFS 是 Apache-licensed open source project，可作为备选。
- MinIO LICENSE：当前仓库许可证为 GNU Affero General Public License v3。
- Ceph Object Gateway S3 API 文档：说明 Ceph RGW 提供 S3 API，但本项目因部署重量不作为首选。
- Garage S3 compatibility 文档：说明 Garage 提供 S3-compatible API，但需要注意兼容子集和许可证约束。
