# Breezio Neo documentation

- [`openapi.yaml`](openapi.yaml): aggregate HTTP API contract
- [`api/`](api/): API contract split by feature
- [`database-design.md`](database-design.md): PostgreSQL schema and state flows
- [`backend-architecture.md`](backend-architecture.md): backend and worker boundaries
- [`sensor-network-protocol.md`](sensor-network-protocol.md): ESP32-S3 and CC2530 device protocols
- [`prediction-model-design.md`](prediction-model-design.md): sensor forecasting workflow
- [`vision-model-design.md`](vision-model-design.md): vision task and training design
- [`vision-anomaly-dataset.md`](vision-anomaly-dataset.md): fixed-view anomaly data preparation
- [`ai-infra-design.md`](ai-infra-design.md): model runtime and admission workflow
- [`object-storage-design.md`](object-storage-design.md): model and artifact storage

Operational startup instructions are in [`../deploy/README.md`](../deploy/README.md).
