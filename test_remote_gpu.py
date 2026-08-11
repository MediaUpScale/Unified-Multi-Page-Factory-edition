#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone live test suite for remote ComfyUI / RunPod workflows.

Exercises each template under ``remote_GPU_workflows/`` independently and
saves outputs under ``outputs/remote_gpu_tests/``.

Examples
--------
    # Dry-run: load workflows + show config (no HTTP)
    python test_remote_gpu.py --dry-run

    # Live image only (requires a reachable ComfyUI)
    python test_remote_gpu.py --image --base-url http://127.0.0.1:8188

    # All three workflows
    python test_remote_gpu.py --all --base-url http://<POD_IP>:8188

    # Audio with a local reference voice sample
    python test_remote_gpu.py --audio --ref-audio path/to/voice.wav

    # Video from a local still
    python test_remote_gpu.py --video --image-file path/to/frame.png
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("test_remote_gpu")

DEFAULT_OUT = ROOT / "outputs" / "remote_gpu_tests"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Live / dry-run tests for remote_GPU_workflows ComfyUI graphs."
    )
    p.add_argument("--image", action="store_true", help="Run Flux Dev txt2img test")
    p.add_argument("--audio", action="store_true", help="Run F5-TTS txt2audio test")
    p.add_argument("--video", action="store_true", help="Run Wan2.2 img2video test")
    p.add_argument("--all", action="store_true", help="Run image + audio + video")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Only load/patch workflows and print summary (no HTTP)",
    )
    p.add_argument(
        "--base-url",
        default=None,
        help="ComfyUI base URL (default: REMOTE_GPU_BASE_URL / COMFYUI_BASE_URL)",
    )
    p.add_argument(
        "--mode",
        choices=("comfyui", "runpod"),
        default=None,
        help="Transport mode (default: REMOTE_GPU_MODE or comfyui)",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Directory for test outputs (default: {DEFAULT_OUT})",
    )
    p.add_argument(
        "--prompt",
        default=(
            "Cinematic wide aerial of an ancient stone temple at dusk, "
            "volumetric god rays, dust particles in foreground, starfield horizon, 8k"
        ),
        help="Positive prompt for image / video tests",
    )
    p.add_argument(
        "--speech",
        default=(
            "True strength is not measured by control over others, "
            "but by absolute mastery over your own mind."
        ),
        help="Speech text for F5-TTS audio test",
    )
    p.add_argument(
        "--ref-audio",
        type=Path,
        default=None,
        help="Local reference WAV for F5-TTS (uploaded to ComfyUI input/)",
    )
    p.add_argument(
        "--image-file",
        type=Path,
        default=None,
        help="Local still for Wan2.2 img2vid (required for --video unless dry-run)",
    )
    p.add_argument("--seed", type=int, default=42, help="Deterministic seed")
    p.add_argument("--steps", type=int, default=None, help="Optional steps override")
    p.add_argument("--width", type=int, default=736, help="Image / video width")
    p.add_argument("--height", type=int, default=1344, help="Image height (portrait)")
    p.add_argument(
        "--video-size",
        type=int,
        default=512,
        help="Wan2.2 square resolution (width=height)",
    )
    p.add_argument("--duration", type=float, default=5.0, help="Wan2.2 duration seconds")
    p.add_argument("--timeout", type=float, default=600.0, help="Job timeout seconds")
    return p.parse_args()


def _banner(title: str) -> None:
    bar = "=" * 64
    print(f"\n{bar}\n  {title}\n{bar}")


def test_dry_run(out_dir: Path) -> int:
    from core_engine.remote_gpu_manager import (
        WORKFLOW_F5TTS_TXT2AUDIO,
        WORKFLOW_FLUX_LORA_TXT2IMG,
        WORKFLOW_FLUX_TXT2IMG,
        WORKFLOW_WAN22_IMG2VID,
        RemoteGPUManager,
        find_nodes_by_class,
        get_image_adapter,
        is_remote_gpu_enabled,
        load_workflow,
        normalize_comfy_base_url,
        resolve_node,
        workflows_dir,
    )

    _banner("DRY RUN — workflow load + payload injection")
    wdir = workflows_dir()
    print(f"workflows_dir          : {wdir}")
    print(f"ENABLE_REMOTE_GPU...   : {is_remote_gpu_enabled()}")
    print(
        "fragment strip test    :",
        normalize_comfy_base_url("https://example.proxy.runpod.net/#deadbeef"),
    )
    print(f"get_image_adapter      : {type(get_image_adapter()).__name__}")
    out_dir.mkdir(parents=True, exist_ok=True)

    for name in (
        WORKFLOW_FLUX_TXT2IMG,
        WORKFLOW_FLUX_LORA_TXT2IMG,
        WORKFLOW_F5TTS_TXT2AUDIO,
        WORKFLOW_WAN22_IMG2VID,
    ):
        wf = load_workflow(name)
        print(f"  OK  {name:32s}  api_nodes={len(wf)}")

    class _StubClient:
        def upload_file(self, local_path, **_kw):
            return Path(local_path).name

    mgr = RemoteGPUManager(client=_StubClient())  # type: ignore[arg-type]
    mgr.workflows_path = wdir

    img_wf = mgr.prepare_image_workflow(
        "test prompt", seed=1, steps=4, width=736, height=1344,
    )
    p_node = resolve_node(img_wf, class_type="CLIPTextEncode", fallback_id="56:51")
    s_node = resolve_node(img_wf, class_type="KSampler", fallback_id="56:52")
    print(f"  patched flux prompt   : {img_wf[p_node]['inputs']['text']!r}")
    print(f"  patched flux seed     : {img_wf[s_node]['inputs']['seed']}")

    lora_wf = mgr.prepare_image_workflow(
        "lora prompt",
        workflow_name=WORKFLOW_FLUX_LORA_TXT2IMG,
        seed=9,
    )
    print(f"  lora nodes            : {find_nodes_by_class(lora_wf, 'LoraLoader')}")

    aud_wf = mgr.prepare_audio_workflow(
        "test speech", sample_text="sample", seed=2, speed=1.0,
        reference_audio_name="sample_10s.wav",
    )
    tts = resolve_node(aud_wf, class_type="F5TTSAudioInputs", fallback_id="13")
    print(f"  patched f5 speech     : {aud_wf[tts]['inputs']['speech']!r}")

    vid_wf = mgr.prepare_video_workflow(
        "dummy_frame.png",
        prompt="slow camera push-in",
        seed=3,
        duration_s=7.0,
        image_name="dummy_frame.png",
    )
    load_n = resolve_node(vid_wf, class_type="LoadImage", fallback_id="97")
    pos_n = resolve_node(
        vid_wf, class_type="CLIPTextEncode", fallback_id="129:93", title_contains="Positive",
    )
    print(f"  patched wan image     : {vid_wf[load_n]['inputs']['image']!r}")
    print(f"  patched wan prompt    : {vid_wf[pos_n]['inputs']['text']!r}")
    print("\nDry-run passed.")
    return 0


def _build_manager(args: argparse.Namespace):
    from core_engine.remote_gpu_manager import ComfyUIClient, RemoteGPUManager, reset_manager

    reset_manager()
    client = ComfyUIClient(
        base_url=args.base_url,
        mode=args.mode,
        timeout_s=args.timeout,
    )
    print(f"mode      : {client.mode}")
    print(f"base_url  : {client.base_url}")
    return RemoteGPUManager(client=client)


def test_image(mgr, args: argparse.Namespace) -> Path:
    _banner("IMAGE — Flux Dev txt2img")
    out = args.out_dir / "images"
    out.mkdir(parents=True, exist_ok=True)
    path = mgr.generate_image(
        args.prompt,
        output_dir=out,
        seed=args.seed,
        steps=args.steps if args.steps is not None else 20,
        width=args.width,
        height=args.height,
        stem="test_flux",
    )
    print(f"Saved image → {path}  ({path.stat().st_size} bytes)")
    return path


def test_audio(mgr, args: argparse.Namespace) -> Path:
    _banner("AUDIO — F5-TTS txt2audio")
    out = args.out_dir / "audio"
    out.mkdir(parents=True, exist_ok=True)
    path = mgr.generate_audio(
        args.speech,
        output_dir=out,
        reference_audio=args.ref_audio,
        seed=args.seed,
        speed=1.0,
        stem="test_f5tts",
    )
    print(f"Saved audio → {path}  ({path.stat().st_size} bytes)")
    return path


def test_video(mgr, args: argparse.Namespace) -> Path:
    _banner("VIDEO — Wan2.2 img2vid")
    if args.image_file is None or not Path(args.image_file).is_file():
        raise SystemExit(
            "--video requires --image-file pointing to a local PNG/JPG still"
        )
    out = args.out_dir / "video"
    out.mkdir(parents=True, exist_ok=True)
    path = mgr.generate_video(
        args.image_file,
        prompt=args.prompt,
        output_dir=out,
        seed=args.seed,
        steps=args.steps,
        width=args.video_size,
        height=args.video_size,
        duration_s=args.duration,
        stem="test_wan22",
    )
    print(f"Saved video → {path}  ({path.stat().st_size} bytes)")
    return path


def main() -> int:
    args = _parse_args()
    args.out_dir = Path(args.out_dir)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run and not (args.image or args.audio or args.video or args.all):
        return test_dry_run(args.out_dir)

    run_image = args.image or args.all
    run_audio = args.audio or args.all
    run_video = args.video or args.all
    if not (run_image or run_audio or run_video):
        print("Nothing selected. Use --image / --audio / --video / --all / --dry-run")
        return 2

    if args.dry_run:
        # Allow combining --dry-run with modality flags for injection-only checks
        return test_dry_run(args.out_dir)

    mgr = _build_manager(args)
    results: list[Path] = []
    try:
        if run_image:
            results.append(test_image(mgr, args))
        if run_audio:
            results.append(test_audio(mgr, args))
        if run_video:
            results.append(test_video(mgr, args))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Remote GPU test failed: %s", exc)
        return 1

    _banner("SUMMARY")
    for p in results:
        print(f"  ✓ {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
