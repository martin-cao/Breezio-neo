# 视觉模型设计

## 1. 定位

视觉模型模块负责满足课程评分中的摄像头实时读取、YOLO 部署和自采数据训练部署。它产出模型和推理事件；模型导出、图优化、算子融合和 runtime benchmark 交给 [AI Infra 设计](./ai-infra-design.md)。

## 2. YOLO 人员检测

- 使用已有 YOLO 权重进行人员检测。
- 输入来自 USB 摄像头、RTSP、HTTP stream 或本地视频文件。
- 输出人员数量、检测框、置信度和快照对象 key。
- 首版只要求部署已有模型，不把重新训练 YOLO 作为主线。

## 3. 自采场景分类

推荐训练一个轻量场景分类模型，而不是自采框标注训练检测器。

类别：

| 类别 | 说明 |
| --- | --- |
| `vacant` | 实验室空置 |
| `normal` | 正常使用 |
| `crowded` | 人员较多 |
| `abnormal` | 遮挡、混乱或异常场景 |

推荐模型：

- MobileNetV3 Small：主交付模型，训练快且稳定；
- DeiT-Tiny：增强实验，展示 Transformer 视觉模型；
- DINOv2 feature + Linear Classifier：小数据增强备选。

在 36 小时交付窗口下，MobileNetV3 Small 是唯一必须完成的模型；DeiT-Tiny/ViT 仅在主闭环（采集、YOLO、场景分类、预测、告警、控制 ACK）全部跑通后，作为时间允许的加分项补做，不得占用主线时间。

## 4. 数据集

```text
datasets/lab_scene_v1/
  vacant/
  normal/
  crowded/
  abnormal/
  manifest.csv
```

`manifest.csv`：

```text
image_path,label,captured_at,source,camera_id,split
vacant/0001.jpg,vacant,2026-06-30T10:00:00+08:00,camera,cam-01,train
```

切分：

- train: 70%
- validation: 15%
- test: 15%

同一段连续视频抽帧的相邻帧不能大量同时进入 train 和 test，避免指标虚高。

## 5. 训练产物

- `model.pt`
- `labels.json`
- `train_config.yaml`
- `metrics.json`
- `confusion_matrix.png`
- `training_curve.png`
- `test_predictions.csv`

ONNX 文件和图优化报告由 AI Infra 生成和登记。

## 6. 推理事件

```json
{
  "event_id": "a0f737bc-f203-43bd-8b15-a51f024b3df7",
  "camera_id": "cam-01",
  "captured_at": "2026-06-30T12:00:00+08:00",
  "people_count": 2,
  "detections": [
    {"label": "person", "confidence": 0.91, "box": [120, 80, 220, 360]}
  ],
  "scene_label": "normal",
  "scene_confidence": 0.87,
  "backend": "onnxruntime-cpu",
  "latency_ms": 24.5,
  "snapshot_object_key": "snapshots/cam-01/2026/06/30/a0f737bc.jpg"
}
```

## 7. 控制联动

```text
IF scene_label == vacant
AND people_count == 0
AND lamp_state == on
FOR 5 minutes
THEN create alert vacant_energy
AND command lamp off
```

```text
IF people_count > 0
AND outside_opening_hours
THEN save snapshot
AND create alert after_hours_person
```

## 8. 答辩证据

1. 摄像头实时画面；
2. YOLO 检测框；
3. 自采数据集目录和标签分布；
4. 训练配置；
5. 混淆矩阵；
6. 测试集指标；
7. 页面实时推理结果；
8. 与告警或控制规则联动。

## 9. 人员身份识别（Face ID）

### 9.1 定位

课程要求覆盖"识别房间内是谁"这一能力，范围是已知实验室成员的封闭集识别，不是开放世界安全系统，不训练"陌生人"类别。这是在 YOLO 人员检测之上的补充能力，不替代场景分类的自训练交付项——场景分类已经满足"自采数据训练一个轻量模型"的评分要求。

### 9.2 技术选型

- 检测 + 对齐 + 特征提取：InsightFace `buffalo_l`（ArcFace embedding），通过 ONNX Runtime 运行，与 [AI Infra 设计](./ai-infra-design.md) 的 ONNX Runtime 主线一致。
- 不训练识别网络本身。36 小时时间预算下，从零训练一个人脸识别网络不现实，且 5 张/人的登记照片也不足以稳定训练深度网络。
- 识别路径：登记（enrollment）+ 最近邻余弦相似度匹配，没有训练循环。
- 可选增强：在登记 embedding 上拟合一个轻量 `sklearn` `LogisticRegression`/线性 SVM，作为可展示的"自训练模型"产物（训练脚本、准确率、混淆矩阵），但线上识别仍以最近邻相似度为准——分类器在 5 样本/类规模下不如最近邻稳健。

### 9.3 登记流程（Enrollment）

- 每位已知成员采集 5 张照片：`front`、`up`、`down`、`left`、`right`。
- 对 5 张照片分别提取 embedding，取平均得到该成员的参考向量。
- 存储 `person_id`、`display_name`、每个 pose 的 `image_uri`、参考 embedding。
- 需要成员本人同意才能登记；仅用于本课程演示。

### 9.4 识别流程

- 摄像头帧经 YOLO 得到人员框（复用现有检测，不新增检测器）。
- 对人脸区域跑 InsightFace 提取 embedding。
- 与所有已登记成员的参考向量计算余弦相似度，取最高值；高于阈值判定为该成员，否则标记为 `unidentified`。不训练、不存储陌生人人脸数据。

### 9.5 数据集与产物

```text
identities/
  p001_alice/
    front.jpg
    up.jpg
    down.jpg
    left.jpg
    right.jpg
  manifest.csv
```

`manifest.csv`：`person_id,display_name,pose,image_path,captured_at`

产物：

- `identities.json`（每人的参考 embedding 和元数据）；
- 可选 `classifier.pkl` + `metrics.json` + `confusion_matrix.png`（若采用可选分类器路线）。

### 9.6 推理事件示例

```json
{
  "event_id": "b6b0b6d0-2f39-4e77-9c8b-6a6f1e9d5c11",
  "camera_id": "cam-01",
  "captured_at": "2026-07-01T12:00:00+08:00",
  "person_id": "p001_alice",
  "display_name": "Alice",
  "confidence": 0.86,
  "identified": true,
  "backend": "onnxruntime-cpu",
  "latency_ms": 18.2
}
```

### 9.7 隐私与保留

- 仅登记自愿同意的实验室成员；不采集、不存储陌生人的面部数据。
- 登记照片、embedding 和识别记录仅用于课程演示，项目结束后删除。

