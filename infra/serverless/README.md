# Serverless execution

RunPod serverless and dedicated-pod ComfyUI share one adapter:
`core/remote_gpu_manager.py`. Workflow JSON lives in `infra/runpod/workflows/`.

Mode is selected by `REMOTE_GPU_MODE` / `--model-api-flow` (`remote_gpu_pod` vs
`remote_gpu_serverless`). This folder is a placeholder so that split stays
visible without duplicating the manager module.
