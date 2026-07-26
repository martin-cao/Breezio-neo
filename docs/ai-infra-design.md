# AI Infra 设计

## 1. 定位

本文档中的 AI Infra 指 MLSys/推理基础设施。它不负责视频采集、数据标注或模型训练流程本身，也不预设“必须做 ONNX 图优化和算子融合”。它的职责是：

1. 给视觉和预测链路建立可测量的端到端性能视图；
2. 定位真实瓶颈；
3. 只对瓶颈段选择合适优化手段；
4. 对优化结果做正确性门控和性能回归检查；
5. 将模型、runtime、图优化、benchmark 和准入结论登记到平台。

也就是说，AI Infra 的原则是：

```text
measure first -> identify bottleneck -> choose optimization -> verify correctness -> admit runtime
```

不是为了展示“用了 ONNX”而盲目导出，也不是为了展示“算子融合”而对所有模型都做图分析。

## 2. 系统性能目标

首版系统不需要追求工业级 FPS，而要保证答辩演示中实时性、稳定性和可解释性。

### 2.1 端到端目标

| 链路 | 目标 | 说明 |
| --- | --- | --- |
| 摄像头画面到页面检测结果 | P95 <= 800 ms | 课程演示可接受，重点是无明显卡顿 |
| YOLO 单帧推理 | P95 <= 300 ms | 若超过，优先考虑后端和输入尺寸 |
| 场景分类单帧推理 | P95 <= 100 ms | 轻量分类模型应低延迟 |
| 预测模型推理 | P95 <= 50 ms | 表格模型不应成为瓶颈 |
| 告警快照上传 | 不阻塞推理主循环 | 上传应异步 spool |
| 后端事件入库和页面推送 | P95 <= 200 ms | 超过时看数据库/推送链路 |

这些阈值是工程目标，不是实验结果。实现后必须用真实 benchmark 更新。

## 3. 可观测性设计

每一帧或每个推理事件带一个 `trace_id`，贯穿采集、推理、事件上报和页面展示。

### 3.1 视觉链路分段

```text
capture_start
capture_done
decode_done
preprocess_done
inference_start
inference_done
postprocess_done
snapshot_spooled
mqtt_publish_done
backend_ingest_done
web_push_done
```

由这些时间点计算：

| 指标 | 计算 |
| --- | --- |
| capture_ms | `capture_done - capture_start` |
| preprocess_ms | `preprocess_done - decode_done` |
| inference_ms | `inference_done - inference_start` |
| postprocess_ms | `postprocess_done - inference_done` |
| publish_ms | `mqtt_publish_done - postprocess_done` |
| backend_ms | `backend_ingest_done - mqtt_publish_done` |
| e2e_ms | `web_push_done - capture_start` |

### 3.2 资源指标

至少记录：

- CPU 使用率；
- 内存；
- GPU 使用率，如果有；
- 显存，如果有；
- 输入分辨率；
- batch size；
- runtime backend；
- 模型版本；
- frame skip ratio；
- queue lag。

## 4. 瓶颈判断

优化决策基于分段耗时，而不是先入为主。

| 观测现象 | 主要瓶颈 | 优先处理 |
| --- | --- | --- |
| `capture_ms` 高，推理不高 | 摄像头/解码 | 降低采集分辨率、固定 FPS、视频回放兜底 |
| `preprocess_ms` 高 | 图像 resize/normalize/copy | 复用 buffer、减少格式转换、把 resize 放进模型预处理 pipeline |
| `inference_ms` 高 | 模型推理 | 多后端、ONNX Runtime、FP16、输入尺寸、图优化 |
| `postprocess_ms` 高 | YOLO NMS/框过滤 | 降低类别数、调 conf/iou、优化 NMS 实现 |
| `snapshot_spooled` 后主循环卡顿 | 对象上传阻塞 | 本地 spool + 异步上传，不做模型优化 |
| `backend_ms` 高 | MQTT/后端/数据库 | 批量写入、索引、WebSocket/SSE 推送优化 |
| e2e 高但各段不高 | 队列积压 | latest-frame queue、丢弃旧帧、限制并发 |
| 首次请求慢，后续正常 | cold start/compile | 预热模型，避免在线 `torch.compile` 编译 |

只有当 `inference_ms` 是主要瓶颈时，才进入图优化、算子融合、多后端准入流程。

## 5. 工作负载优先级

### 5.1 YOLO 人员检测

YOLO 最可能成为实时链路瓶颈，因为输入分辨率大、后处理有 NMS。优化顺序：

1. 先测 `inference_ms` 和 `postprocess_ms`；
2. 若 `postprocess_ms` 高，先调 NMS 和类别过滤；
3. 若 `inference_ms` 高，再测 ONNX Runtime CPU/CUDA；
4. 若 GPU 可用，再比较 FP16；
5. 若仍不满足，再考虑输入尺寸或轻量 YOLO 版本。

不建议一开始就对 YOLO 做复杂图融合解释，因为 YOLO 的瓶颈可能在 NMS 或输入尺寸，而不是普通 Conv 融合。

### 5.2 自采场景分类

场景分类模型是最适合展示 AI Infra 的对象：

- 模型小；
- 输出简单；
- 正确性容易比较；
- CNN 模型通常能看到 Conv/BN 融合；
- ONNX Runtime CPU 就可能有收益。

如果使用 MobileNetV3/ResNet 类 CNN，重点看：

- `Conv + BatchNorm` 折叠；
- 激活算子融合；
- 节点数变化；
- P50/P95 是否下降。

如果使用 DeiT/ViT，重点看：

- MatMul/Add/LayerNorm 子图；
- attention 相关 op；
- CPU 上是否真的变快。若不变快，要如实说明 Transformer 小模型在 CPU 上可能被 memory/layout overhead 限制。

### 5.3 温湿度预测模型

预测模型一般不是瓶颈。Linear Regression、Random Forest、HistGradientBoosting 对课程规模数据来说延迟很低。

处理原则：

- 只记录模型加载时间和单次预测延迟；
- 不强行做 ONNX 图优化；
- 如果预测链路慢，优先查数据库查询、特征窗口构造和定时任务调度。

## 6. 多后端部署策略

多后端部署是合理的，但它是瓶颈处理工具，不是固定流程。

### 6.1 候选后端

| 后端 | 适用场景 | 风险 |
| --- | --- | --- |
| `torch-eager-fp32` | 正确性参考、兜底 | 可能慢 |
| `torch-compile` | 重复运行同一形状模型 | 首次编译慢，课程演示可能不划算 |
| `torch-fp16` | GPU 上 CNN/YOLO | CPU 不适合，精度需检查 |
| `onnxruntime-cpu` | 通用部署、无 GPU 环境 | 小模型收益不一定明显 |
| `onnxruntime-cuda` | GPU 可用时 | 环境依赖更复杂 |
| `onnxruntime-optimized` | 推理是瓶颈且图可优化 | 需要正确性门控 |

### 6.2 后端选择逻辑

```text
IF inference is not the bottleneck:
    keep stable baseline backend
ELSE:
    benchmark candidate backends
    reject backends that fail correctness
    reject backends that worsen P95 or memory
    select lowest P95 under threshold
```

`torch.compile` 不应该默认启用。如果冷启动或编译时间影响演示，就只作为离线 benchmark 项，不作为在线 runtime。

## 7. 图优化与算子融合

### 7.1 触发条件

只有满足以下条件才做深入图优化：

- `inference_ms` 是主要瓶颈；
- 模型可稳定导出 ONNX；
- PyTorch 与 ONNX 输出一致；
- 优化后的 ONNX 能稳定运行；
- 优化能带来实际 P95 或吞吐改善，或者能作为答辩中的可解释 MLSys 分析。

### 7.2 图优化层级

| Level | 用途 |
| --- | --- |
| disabled | baseline |
| basic | 基础图清理 |
| extended | 常用融合和重写 |
| all | 增强实验，必须严格验证正确性 |

记录：

- 节点数；
- op type 分布；
- initializer 数量；
- 模型大小；
- P50/P95；
- 正确性结果。

### 7.3 算子融合重点

| 融合模式 | 何时有价值 |
| --- | --- |
| `Conv + BatchNorm` | CNN 场景分类，推理时常见收益 |
| `Conv + Add + Relu` | 残差结构或 MobileNet 变体 |
| `MatMul + Add` | MLP/Transformer 线性层 |
| `LayerNorm` 子图 | Transformer 模型 |
| Attention 子图 | 只有 ViT/DeiT 才重点看 |

融合报告必须同时给出“是否变快”。节点数减少但 P95 不变，不能声称系统性能提升，只能说图结构更紧凑。

## 8. 正确性门控

任何优化后端必须通过正确性门控。

| 检查项 | 默认阈值 |
| --- | --- |
| Top-1 一致率 | `>= 99%` |
| logits max abs diff | FP32 `<= 1e-4` |
| logits mean abs diff | FP32 `<= 1e-5` |
| NaN/Inf | 不允许 |
| 测试集 accuracy 下降 | 不超过 0.5% |
| 预处理配置 hash | 必须一致 |

FP16、量化或不同 execution provider 可以单独设阈值，但必须在报告中说明。

## 9. Benchmark 协议

### 9.1 固定条件

每次 benchmark 记录：

```yaml
model_key: lab-scene-classifier
model_version: v1
backend: onnxruntime-cpu
input_shape: [1, 3, 224, 224]
batch_size: 1
warmup_runs: 20
measured_runs: 100
device: cpu
thread_count: 1
preprocess_included: false
postprocess_included: false
```

如果 benchmark 包含 preprocess/postprocess，必须显式标注，不能和纯模型推理混在一张表里比较。

### 9.2 指标

- load time；
- first inference latency；
- p50；
- p95；
- mean；
- std；
- FPS；
- CPU/GPU；
- 内存/显存；
- model size；
- correctness result。

## 10. 准入策略

后端准入规则：

```text
correctness_passed == true
AND p95_latency_ms <= target_p95
AND memory_mb <= memory_limit
AND no_runtime_error_in_stability_window
AND no_quality_regression
```

准入后端不是永久固定。若运行时监控发现 P95 退化、OOM 或连续推理失败，应降级到稳定后端。

推荐降级顺序：

```text
onnxruntime-cuda -> onnxruntime-cpu-optimized -> onnxruntime-cpu -> torch-eager-fp32
```

## 11. 产物管理

RustFS object key：

```text
models/{model_key}/{version}/raw/model.pt
models/{model_key}/{version}/onnx/model.raw.onnx
models/{model_key}/{version}/onnx/model.optimized.onnx
benchmarks/{model_key}/{version}/{backend}/benchmark.json
benchmarks/{model_key}/{version}/{backend}/trace.json
benchmarks/{model_key}/{version}/graph/graph_report.json
benchmarks/{model_key}/{version}/graph/fusion_report.json
benchmarks/{model_key}/{version}/graph/netron_raw.png
benchmarks/{model_key}/{version}/graph/netron_optimized.png
```

PostgreSQL：

- `model_versions`：模型版本、artifact、ONNX artifact、状态；
- `benchmark_results`：后端、shape、P50/P95、正确性、准入；
- `runtime_events`：后端切换、降级、失败原因；
- `object_artifacts`：RustFS 对象引用。

## 12. 内部执行边界

AI Infra 是系统底层优化架构，不属于用户交互功能，也不提供 Web-facing
REST API。ONNX 导出、图优化、算子融合、benchmark 和后端准入由内部脚本、
worker 或 CI 管道执行。

内部产物写入边界：

- PostgreSQL 保存模型版本、benchmark 结果、runtime event 和准入结论；
- RustFS 保存 ONNX 文件、benchmark JSON、trace、图报告和融合报告；
- 业务后端和推理服务只读取已准入的运行时选择，不向普通用户暴露优化开关；
- 前端只展示业务所需的模型选择/状态，不提供 AI infra 工程控制台。
- 准入后端；
- 降级和切换日志。

## 13. 实施优先级

| 优先级 | 内容 | 目的 |
| --- | --- | --- |
| P0 | trace 埋点、分段耗时、baseline benchmark | 先知道瓶颈在哪 |
| P1 | 多后端 benchmark、正确性门控 | 推理瓶颈时选择后端 |
| P2 | ONNX 图优化、节点统计、P50/P95 对比 | 证明优化是否有效 |
| P3 | 算子融合报告、Netron 截图、准入和降级 | 答辩展示 MLSys 深度 |
| P4 | FP16、CUDA、`torch.compile`、Transformer 子图分析 | 有硬件和时间再做 |

## 14. 答辩证据

答辩时按问题链条展示：

1. 系统端到端 trace 显示瓶颈在哪；
2. 为什么选择优化这个模型或这个阶段；
3. baseline 后端的 P50/P95；
4. 候选后端的正确性门控；
5. 原始 ONNX 与优化 ONNX 的图结构变化；
6. 算子融合是否实际降低延迟；
7. 最终准入后端和降级策略；
8. RustFS 中保存的模型、trace、图报告和 benchmark JSON。
