"""Admit-or-Avoid runtime admission benchmark.

Adapted from the short-job GPU admission idea: measure a stable baseline
first, then ADMIT an optimized runtime only when it (1) passes an output
consistency gate, (2) meaningfully improves P95 latency (>= --min-gain, default
10%), and (3) does not blow up load time / memory for the demo. Otherwise
AVOID and keep the baseline.

Workloads (all CPU, matching the demo machine):
  - person-detection: ultralytics yolov8n torch-eager (baseline) vs ONNX
    Runtime CPU export (candidate). Correctness gate: identical people_count
    per image and mean box IoU >= 0.9 on matched detections.
  - anomaly-embedding: DINOv2 ViT-S/14 torch-eager per-frame (baseline) vs
    "trigger-only" policy (candidate is a scheduling decision, so only the
    per-call cost is reported; admission of the policy is a design note).
  - prediction models are NOT benchmarked here: their measured predict
    latency is already ~0.02 ms (see prediction runs) -> AVOID any
    optimization without further measurement, per measure-first.

Images: ultralytics COCO sample images (bus.jpg, zidane.jpg — real photos
shipped by ultralytics for exactly this purpose) plus one synthetic gray
frame. They benchmark runtime cost only; no model quality claims are made
from them.

Usage:
    UV_CACHE_DIR=/tmp/breezio-uv-cache PYTHONPATH=inference uv run \
      --with ultralytics --with onnxruntime --with opencv-python-headless \
      python tools/runtime_admission_bench.py --runs 30 --out var/ml/admission
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def percentile(samples: list[float], q: float) -> float:
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, max(0, round(q / 100 * (len(ordered) - 1))))
    return ordered[idx]


def bench(fn, images: list[str], runs: int, warmup: int) -> dict:
    for i in range(warmup):
        fn(images[i % len(images)])
    samples = []
    for i in range(runs):
        start = time.perf_counter()
        fn(images[i % len(images)])
        samples.append((time.perf_counter() - start) * 1000)
    return {
        "runs": runs,
        "p50_ms": round(percentile(samples, 50), 2),
        "p95_ms": round(percentile(samples, 95), 2),
        "mean_ms": round(statistics.mean(samples), 2),
        "std_ms": round(statistics.stdev(samples), 2) if len(samples) > 1 else 0.0,
    }


def box_iou(a: list[float], b: list[float]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter + 1e-9)


def detections(result) -> list[dict]:
    out = []
    for box in result.boxes:
        out.append({"box": [float(v) for v in box.xyxy[0].tolist()], "conf": float(box.conf[0])})
    return sorted(out, key=lambda d: -d["conf"])


def correctness_gate(torch_model, onnx_model, images: list[str], iou_min: float = 0.9) -> dict:
    per_image = []
    passed = True
    for path in images:
        ref = detections(torch_model.predict(path, classes=[0], conf=0.25, verbose=False)[0])
        cand = detections(onnx_model.predict(path, classes=[0], conf=0.25, verbose=False)[0])
        count_match = len(ref) == len(cand)
        # greedy best-IoU matching: confidence ordering can differ between
        # backends for near-equal boxes, so pairing by rank understates IoU
        remaining = list(cand)
        ious = []
        for r in ref:
            if not remaining:
                break
            best = max(remaining, key=lambda c: box_iou(r["box"], c["box"]))
            ious.append(box_iou(r["box"], best["box"]))
            remaining.remove(best)
        mean_iou = round(sum(ious) / len(ious), 4) if ious else 1.0
        ok = count_match and mean_iou >= iou_min
        passed = passed and ok
        per_image.append({"image": Path(path).name, "ref_count": len(ref), "cand_count": len(cand), "mean_iou": mean_iou, "ok": ok})
    return {"passed": passed, "iou_min": iou_min, "per_image": per_image}


def bench_vlm_caption(args, report: dict, images: list[str]) -> None:
    """Admit-or-Avoid for the Moondream2 caption path: eager vs torch.compile.

    This is the paper's short-lived-GPU-job admission question applied to the
    VLM: a caption call is a finite decode job (analogous to the LLM decode
    proxy family), so the compile candidate must win on cold end-to-end
    completion time INCLUDING its startup cost, not on warmed steady-state.
    Correctness gate: greedy decode text equality against the eager reference
    (assert_close has no meaning for generated text).
    """
    import sys

    sys.path.insert(0, str(REPO_ROOT / "inference"))
    import os

    from breezio_inference.service import MoondreamCaptionRuntime, ModelRecord

    model = ModelRecord(model_id="bench-caption", task="scene-caption", version="bench", runtime="moondream-vlm")
    caption_images = images[: max(1, min(3, len(images)))]

    def cold_total(mode: str) -> tuple[float, list[str]]:
        """One cold job: fresh runtime, pay load (+compile) then caption each image."""
        os.environ["BREEZIO_VLM_MODE"] = mode
        runtime = MoondreamCaptionRuntime()
        start = time.perf_counter()
        captions = []
        for path in caption_images:
            result = runtime.predict(model, {"image_uri": path, "length": "short"})
            if result.get("reason"):
                raise RuntimeError(f"caption failed in {mode} mode: {result.get('detail')}")
            captions.append(result["scene_summary"])
        total_ms = (time.perf_counter() - start) * 1000
        # torch.compile state may leak across "cold" runs in one process;
        # documented limitation — a stricter protocol would fork per repeat.
        return total_ms, captions

    repeats = max(3, args.runs // 10)
    eager_samples, eager_captions = [], []
    compile_samples, compile_captions = [], []
    for _ in range(repeats):
        ms, captions = cold_total("eager")
        eager_samples.append(ms)
        eager_captions = captions
    for _ in range(repeats):
        ms, captions = cold_total("compile")
        compile_samples.append(ms)
        compile_captions = captions

    eager_p50 = round(percentile(eager_samples, 50), 1)
    compile_p50 = round(percentile(compile_samples, 50), 1)
    gate_passed = eager_captions == compile_captions
    gain = (eager_p50 - compile_p50) / eager_p50 if eager_p50 else 0.0
    admit = gate_passed and gain >= args.min_gain
    report["workloads"]["vlm-caption"] = {
        "model": f"{MoondreamCaptionRuntime.MODEL_ID}@{MoondreamCaptionRuntime.REVISION}",
        "protocol": f"{repeats}-repeat cold total (load + {len(caption_images)} captions), median compared",
        "baseline": {"runtime": "transformers-eager", "cold_total_p50_ms": eager_p50, "samples_ms": [round(s, 1) for s in eager_samples]},
        "candidate": {"runtime": "torch.compile", "cold_total_p50_ms": compile_p50, "samples_ms": [round(s, 1) for s in compile_samples]},
        "correctness_gate": {"passed": gate_passed, "rule": "greedy short-caption text equality vs eager reference"},
        "cold_total_gain": round(gain, 4),
        "decision": "ADMIT" if admit else "AVOID",
        "reason": (
            f"correctness {'passed' if gate_passed else 'FAILED (divergent captions)'}, "
            f"cold-total P50 {eager_p50}ms -> {compile_p50}ms ({gain:+.1%}, need >= {args.min_gain:.0%}); "
            "set BREEZIO_VLM_MODE accordingly (default eager = safe fallback)"
        ),
    }


def bench_gpu_offload(args, report: dict, images: list[str]) -> None:
    """Admit-or-Avoid candidate matrix for the Moondream2 VLM.

    The end goal is inference speed for the whole stack; the levers benched
    here are dtype (fp32/fp16/int8), placement (cuda-resident / per-call
    PCIe swap / cpu-resident), and — via --workload vlm-caption — compile.
    Each arm runs the *production* runtime (BREEZIO_VLM_PLACEMENT /
    BREEZIO_VLM_DTYPE), so an ADMIT maps 1:1 onto env config.

    Arms and admission rules (baseline: cuda-fp32, today's config):
      - cuda-fp16: speed+VRAM candidate. ADMIT if warm P50 gain >= --min-gain
        OR idle VRAM freed >= --min-vram-free-mib, gated on caption quality.
      - cuda-int8 (--with-int8, needs bitsandbytes): VRAM candidate; LLM.int8
        often runs *slower* than fp16 (Dettmers et al., NeurIPS '22), which
        is exactly why it must be measured. Same rule as cuda-fp16.
      - swap-fp16: VRAM candidate (Torpor/PipeSwitch-style late binding,
        naive blocking variant). ADMIT if warm P95 <= --budget-ms and VRAM
        freed >= --min-vram-free-mib. Compared against cuda-fp16 (same
        numerics), so its correctness gate is exact text equality.
      - cpu-fp32: fallback VRAM candidate, same rule as swap-fp16.

    Correctness gating is two-tier: same-numerics arms (swap vs its resident
    twin, cpu-fp32 vs cuda-fp32) must reproduce captions exactly; changed-
    numerics arms (fp16, int8) legitimately alter generated text, so they get
    quality="review" — captions from every arm are recorded in the report
    and the human verdict goes in the docs/status/ write-up. No automatic
    ADMIT is issued on a failed/review gate without that note.

    Everything else in the stack is already placed: face-ID and DINOv2 run
    on CPU, YOLO runs at 5Hz against a 300ms P95 budget (hot -> resident).

    Precedents (measure, don't assume, is the shared lesson): PipeSwitch
    (Bai et al., OSDI '20) showed on-demand GPU model swap-in can be fast
    enough to timeshare a GPU; Torpor (Yu et al., arXiv:2306.03622) parks
    models in host RAM and swaps per request under latency SLOs; SwapAdvisor
    (Huang et al., ASPLOS '20) planned GPU<->CPU swaps offline; FlexGen
    (Sheng et al., ICML '23) and ZeRO-Inference (Aminabadi et al., SC '22)
    accept per-token offload latency for batch throughput — the opposite
    trade to our latency-budgeted interactive caption; PowerInfer (Song et
    al., SOSP '24) targets exactly this consumer-GPU VRAM-starved regime.
    Moondream2 fits in VRAM whole, so layer-wise streaming buys nothing and
    the decision reduces to cadence-aware whole-model placement + precision.
    """
    import gc
    import os
    import sys

    sys.path.insert(0, str(REPO_ROOT / "inference"))
    import torch

    from breezio_inference.service import ModelRecord, MoondreamCaptionRuntime

    if not torch.cuda.is_available():
        report["workloads"]["gpu-offload"] = {"skipped": "no CUDA device; offload question does not exist here"}
        return

    def vram_free_mib() -> int:
        free, _total = torch.cuda.mem_get_info()
        return int(free / (1024 * 1024))

    model = ModelRecord(model_id="bench-offload", task="scene-caption", version="bench", runtime="moondream-vlm")
    caption_images = images[: max(1, min(3, len(images)))]

    # (arm, placement, dtype); order matters: residents first so swap/cpu can
    # be gated against their same-numerics resident twin
    arms = [
        ("cuda-fp32", "cuda", ""),
        ("cuda-fp16", "cuda", "float16"),
        ("swap-fp16", "swap", "float16"),
        ("cpu-fp32", "cpu", ""),
    ]
    if args.with_int8:
        arms.insert(2, ("cuda-int8", "cuda", "int8"))

    results: dict[str, dict] = {}
    for arm, placement, dtype in arms:
        os.environ["BREEZIO_VLM_PLACEMENT"] = placement
        os.environ["BREEZIO_VLM_DTYPE"] = dtype
        os.environ["BREEZIO_VLM_MODE"] = "eager"
        gc.collect()
        torch.cuda.empty_cache()
        free_before = vram_free_mib()
        runtime = MoondreamCaptionRuntime()
        try:
            t0 = time.perf_counter()
            runtime._moondream()
            load_ms = round((time.perf_counter() - t0) * 1000, 1)

            def call(path: str) -> str:
                result = runtime.predict(model, {"image_uri": path, "length": "short"})
                if result.get("reason"):
                    raise RuntimeError(f"caption failed in {arm}: {result.get('detail')}")
                return result["scene_summary"]

            captions = [call(p) for p in caption_images]  # warmup + correctness sample
            runs = 3 if placement == "cpu" else max(5, args.runs // 6)
            samples = []
            for i in range(runs):
                t0 = time.perf_counter()
                call(caption_images[i % len(caption_images)])
                samples.append((time.perf_counter() - t0) * 1000)
        except Exception as exc:  # int8 on trust_remote_code models may just not work — record, keep benching
            results[arm] = {"skipped": f"{type(exc).__name__}: {exc}"}
            del runtime
            gc.collect()
            torch.cuda.empty_cache()
            continue

        gc.collect()
        torch.cuda.empty_cache()
        results[arm] = {
            "placement": placement,
            "dtype": dtype or "float32",
            "load_ms": load_ms,
            "warm_p50_ms": round(percentile(samples, 50), 1),
            "warm_p95_ms": round(percentile(samples, 95), 1),
            "samples_ms": [round(s, 1) for s in samples],
            "idle_vram_held_mib": max(0, free_before - vram_free_mib()),
            "captions": captions,
        }
        del runtime
        gc.collect()
        torch.cuda.empty_cache()

    baseline = results.get("cuda-fp32", {})
    decisions: dict[str, dict] = {}
    # (arm, exact-equality reference arm or None for review-tier, kind)
    rules = [("cuda-fp16", None, "speed+vram"), ("cuda-int8", None, "vram"), ("swap-fp16", "cuda-fp16", "vram"), ("cpu-fp32", "cuda-fp32", "vram")]
    for arm, ref_arm, kind in rules:
        cand = results.get(arm)
        if cand is None or "skipped" in cand or "skipped" in baseline:
            continue
        vram_freed = baseline["idle_vram_held_mib"] - cand["idle_vram_held_mib"]
        speed_gain = (baseline["warm_p50_ms"] - cand["warm_p50_ms"]) / baseline["warm_p50_ms"]
        if ref_arm is None:
            gate, gate_rule = "review", "changed numerics: captions recorded for human quality review"
            resource_ok = speed_gain >= args.min_gain or vram_freed >= args.min_vram_free_mib
            decision = "ADMIT-pending-review" if resource_ok else "AVOID"
        else:
            ref = results.get(ref_arm, {})
            exact = "captions" in ref and cand["captions"] == ref["captions"]
            gate, gate_rule = ("passed" if exact else "FAILED"), f"exact caption equality vs same-numerics twin {ref_arm}"
            resource_ok = cand["warm_p95_ms"] <= args.budget_ms and vram_freed >= args.min_vram_free_mib
            decision = "ADMIT" if exact and resource_ok else "AVOID"
        decisions[arm] = {
            "kind": kind,
            "decision": decision,
            "correctness_gate": {"status": gate, "rule": gate_rule},
            "warm_p50_speed_gain_vs_fp32": round(speed_gain, 4),
            "warm_p95_ms": cand["warm_p95_ms"],
            "idle_vram_freed_mib": vram_freed,
            "reason": (
                f"gate {gate}; P50 gain {speed_gain:+.1%} (speed threshold {args.min_gain:.0%}); "
                f"P95 {cand['warm_p95_ms']}ms vs budget {args.budget_ms}ms; "
                f"VRAM freed {vram_freed}MiB (threshold {args.min_vram_free_mib}MiB)"
            ),
        }

    report["workloads"]["gpu-offload-vlm"] = {
        "model": f"{MoondreamCaptionRuntime.MODEL_ID}@{MoondreamCaptionRuntime.REVISION}",
        "baseline_arm": "cuda-fp32 (current production config)",
        "policy": {
            "budget_ms": args.budget_ms,
            "min_vram_free_mib": args.min_vram_free_mib,
            "min_speed_gain": args.min_gain,
            "cadence": "caption every 300s or on-demand -> cold model, latency budget is seconds",
        },
        "arms": results,
        "decisions": decisions,
        "note": "ADMIT-pending-review arms need the recorded captions eyeballed before flipping env config",
    }
    report["workloads"]["placement-notes"] = {
        "person-detection": "hot (5Hz, P95<=300ms budget) -> resident on GPU; speed levers (fp16/TensorRT/imgsz) are a separate bench arm",
        "face-identification": "already CPU (onnxruntime CPUExecutionProvider), holds no VRAM",
        "anomaly-embedding": "already CPU (torch.hub dinov2_vits14, CPU tensors), holds no VRAM",
    }


def bench_yolo_speed(args, report: dict, images: list[str]) -> None:
    """Admit-or-Avoid speed candidates for the hot person-detection path.

    YOLO runs at 5Hz against the P95 <= 300ms budget (docs/ai-infra-design.md)
    — the only hard-real-time model in the stack, so this is where a latency
    win actually matters. Arms (baseline fp32@640 on GPU):
      - fp16@640 (predict half=True): free-lunch candidate, same resolution
      - fp16@480: resolution trade — extra speed for possible accuracy loss,
        which is why the IoU gate applies to it just as hard
    Gate: people_count equality + greedy-matched box IoU >= 0.9 vs the fp32
    baseline on every image. ADMIT needs gate pass + P95 gain >= --min-gain
    + P95 within the 300ms budget. TensorRT is deliberately deferred: engine
    builds are driver/version-fragile and fp16 must prove itself first
    (see docs/status/inference-speedup-roadmap-2026-07-08.md).
    """
    import torch
    from ultralytics import YOLO

    if not torch.cuda.is_available():
        report["workloads"]["yolo-speed"] = {"skipped": "no CUDA device; this bench targets the GPU serving path"}
        return

    weights = args.yolo_weights
    if not Path(weights).exists():
        weights = "yolov8n.pt"
    model = YOLO(weights)
    budget_ms = 300.0

    arms = {
        "fp32-640": {"half": False, "imgsz": 640},
        "fp16-640": {"half": True, "imgsz": 640},
        "fp16-480": {"half": True, "imgsz": 480},
    }

    def predict(path: str, cfg: dict):
        return model.predict(path, classes=[0], conf=0.25, device=0, verbose=False, **cfg)[0]

    results: dict[str, dict] = {}
    references: dict[str, list[dict]] = {}
    for arm, cfg in arms.items():
        stats = bench(lambda p: predict(p, cfg), images, args.runs, args.warmup)
        dets = {Path(p).name: detections(predict(p, cfg)) for p in images}
        results[arm] = {"weights": weights, **stats}
        if arm == "fp32-640":
            references = dets
            continue
        per_image, passed = [], True
        for name, ref in references.items():
            cand = dets[name]
            count_match = len(ref) == len(cand)
            remaining = list(cand)
            ious = []
            for r in ref:
                if not remaining:
                    break
                best = max(remaining, key=lambda c: box_iou(r["box"], c["box"]))
                ious.append(box_iou(r["box"], best["box"]))
                remaining.remove(best)
            mean_iou = round(sum(ious) / len(ious), 4) if ious else 1.0
            ok = count_match and mean_iou >= 0.9
            passed = passed and ok
            per_image.append({"image": name, "ref_count": len(ref), "cand_count": len(cand), "mean_iou": mean_iou, "ok": ok})
        gain = (results["fp32-640"]["p95_ms"] - stats["p95_ms"]) / results["fp32-640"]["p95_ms"]
        admit = passed and gain >= args.min_gain and stats["p95_ms"] <= budget_ms
        results[arm]["correctness_gate"] = {"passed": passed, "iou_min": 0.9, "per_image": per_image}
        results[arm]["p95_gain"] = round(gain, 4)
        results[arm]["decision"] = "ADMIT" if admit else "AVOID"
        results[arm]["reason"] = (
            f"gate {'passed' if passed else 'FAILED'}; "
            f"P95 {results['fp32-640']['p95_ms']}ms -> {stats['p95_ms']}ms ({gain:+.1%}, need >= {args.min_gain:.0%}); "
            f"budget {budget_ms}ms"
        )

    report["workloads"]["yolo-speed"] = {
        "model": weights,
        "policy": {"p95_budget_ms": budget_ms, "min_gain": args.min_gain, "correctness": "people_count equal + mean IoU >= 0.9 vs fp32 baseline"},
        "arms": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--min-gain", type=float, default=0.10, help="required relative P95 improvement to ADMIT")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "var" / "ml" / "admission")
    parser.add_argument("--skip-dinov2", action="store_true")
    parser.add_argument(
        "--workload",
        choices=("all", "vlm-caption", "gpu-offload", "yolo-speed"),
        default="all",
        help="'vlm-caption' benchmarks the Moondream2 eager-vs-compile question; "
        "'gpu-offload' benchmarks Moondream2 placement cuda/cpu/swap (both need transformers==4.51.3 + einops + accelerate)",
    )
    parser.add_argument("--budget-ms", type=float, default=15000, help="gpu-offload: max acceptable warm caption P95")
    parser.add_argument("--min-vram-free-mib", type=int, default=1024, help="gpu-offload: min idle VRAM a candidate must free to ADMIT")
    parser.add_argument("--with-int8", action="store_true", help="gpu-offload: also bench a bitsandbytes int8 arm (needs bitsandbytes installed)")
    parser.add_argument("--yolo-weights", default=str(REPO_ROOT / "var" / "ml" / "yolo_lab_person" / "train" / "weights" / "best.pt"), help="yolo-speed: weights to bench (falls back to yolov8n.pt)")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    if args.workload == "yolo-speed":
        report = {
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "machine": {"platform": platform.platform(), "python": platform.python_version()},
            "workloads": {},
        }
        from ultralytics.utils.downloads import safe_download

        images_dir = args.out / "bench_images"
        images_dir.mkdir(parents=True, exist_ok=True)
        images = []
        for name in ("bus.jpg", "zidane.jpg"):
            dest = images_dir / name
            if not dest.exists():
                safe_download(f"https://ultralytics.com/images/{name}", file=dest)
            images.append(str(dest))
        images += sorted(str(q) for q in (REPO_ROOT / "var" / "camera_gateway").glob("*.jpg"))[-2:]
        bench_yolo_speed(args, report, images)
        out_json = args.out / "admission_report_yolo_speed.json"
        out_json.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        print(f"\nwrote {out_json}")
        return 0

    if args.workload in ("vlm-caption", "gpu-offload"):
        report = {
            "date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "machine": {"platform": platform.platform(), "python": platform.python_version()},
            "policy": {"min_cold_total_gain": args.min_gain, "correctness": "greedy caption text equality"},
            "workloads": {},
        }
        import numpy as np
        import cv2

        images_dir = args.out / "bench_images"
        images_dir.mkdir(exist_ok=True)
        gray = images_dir / "gray.jpg"
        cv2.imwrite(str(gray), np.full((480, 640, 3), 90, dtype="uint8"))
        existing = sorted(str(p) for p in (REPO_ROOT / "var" / "camera_gateway").glob("*.jpg"))[-2:]
        if args.workload == "gpu-offload":
            bench_gpu_offload(args, report, existing + [str(gray)])
            out_json = args.out / "admission_report_offload.json"
        else:
            bench_vlm_caption(args, report, existing + [str(gray)])
            out_json = args.out / "admission_report_vlm.json"
        out_json.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        print(f"\nwrote {out_json}")
        return 0

    from ultralytics import YOLO
    from ultralytics.utils.downloads import safe_download
    import numpy as np
    import cv2

    images_dir = args.out / "bench_images"
    images_dir.mkdir(exist_ok=True)
    images = []
    for name in ("bus.jpg", "zidane.jpg"):
        dest = images_dir / name
        if not dest.exists():
            safe_download(f"https://ultralytics.com/images/{name}", file=dest)
        images.append(str(dest))
    gray = images_dir / "gray.jpg"
    cv2.imwrite(str(gray), np.full((480, 640, 3), 90, dtype="uint8"))
    images.append(str(gray))

    report = {
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "machine": {"platform": platform.platform(), "python": platform.python_version(), "device": "cpu"},
        "policy": {"min_p95_gain": args.min_gain, "correctness": "people_count equal + mean IoU >= 0.9"},
        "workloads": {},
    }

    # --- person-detection: torch eager vs onnxruntime-cpu ---
    torch_model = YOLO("yolov8n.pt")
    load_t0 = time.perf_counter()
    onnx_path = torch_model.export(format="onnx", imgsz=640, verbose=False)
    export_ms = round((time.perf_counter() - load_t0) * 1000, 1)
    onnx_model = YOLO(onnx_path)

    baseline = bench(lambda p: torch_model.predict(p, classes=[0], conf=0.25, verbose=False), images, args.runs, args.warmup)
    candidate = bench(lambda p: onnx_model.predict(p, classes=[0], conf=0.25, verbose=False), images, args.runs, args.warmup)
    gate = correctness_gate(torch_model, onnx_model, images)
    gain = (baseline["p95_ms"] - candidate["p95_ms"]) / baseline["p95_ms"]
    admit = gate["passed"] and gain >= args.min_gain
    report["workloads"]["person-detection"] = {
        "model": "yolov8n (pretrained COCO)",
        "baseline": {"runtime": "torch-eager-fp32-cpu", **baseline},
        "candidate": {"runtime": "onnxruntime-cpu (ultralytics export)", "export_ms": export_ms, **candidate},
        "correctness_gate": gate,
        "p95_gain": round(gain, 4),
        "decision": "ADMIT" if admit else "AVOID",
        "reason": (
            f"correctness {'passed' if gate['passed'] else 'FAILED'}, "
            f"P95 {baseline['p95_ms']}ms -> {candidate['p95_ms']}ms ({gain:+.1%}, need >= {args.min_gain:.0%})"
        ),
    }

    # --- anomaly embedding: per-call cost of DINOv2 (policy note, no candidate runtime) ---
    if not args.skip_dinov2:
        try:
            import sys
            sys.path.insert(0, str(REPO_ROOT / "inference"))
            from breezio_inference.service import Dinov2AnomalyRuntime

            runtime = Dinov2AnomalyRuntime()
            embed_stats = bench(lambda p: runtime.embed(p), images, max(10, args.runs // 3), 3)
            report["workloads"]["anomaly-embedding"] = {
                "model": "dinov2_vits14 (pretrained)",
                "baseline": {"runtime": "torch-eager-fp32-cpu", **embed_stats},
                "candidate": {"runtime": "trigger-only scheduling (run after YOLO event/frame-skip)", "note": "policy, not a runtime"},
                "decision": "ADMIT (policy)" if embed_stats["p95_ms"] > 100 else "AVOID (per-frame is cheap enough)",
                "reason": f"per-call P95 {embed_stats['p95_ms']}ms vs 100ms scene-budget from docs/ai-infra-design.md",
            }
        except Exception as exc:  # torch/torchvision may be absent in this env
            report["workloads"]["anomaly-embedding"] = {"skipped": str(exc)}

    report["workloads"]["sensor-prediction"] = {
        "model": "persistence/linear/rf/hgb (sklearn)",
        "baseline": {"runtime": "sklearn-cpu", "measured_predict_ms": "<=0.03 (from prediction runs 2026-07-04)"},
        "candidate": None,
        "decision": "AVOID",
        "reason": "prediction latency is 3 orders of magnitude under the 50ms budget; optimizing it violates measure-first",
    }

    out_json = args.out / "admission_report.json"
    out_json.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nwrote {out_json}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
