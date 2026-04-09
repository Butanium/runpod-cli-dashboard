# RunPod CLI Dashboard

CLI tool for managing RunPod GPU pods — create, connect, run commands, and open services in one step.

## Installation

```bash
uv add git+https://github.com/Butanium/runpod-cli-dashboard

# Or with uv (development)
uv sync
```

This installs the `runpod-cli` command.

## Quick start

```bash
# First run — interactive setup (username, API key, git config, HF token)
runpod-cli config

# Launch a dashboard pod (default: A40 GPU)
runpod-cli

# Launch a vLLM server with LoRA support
runpod-cli task=vllm gpu=a100 app_port=8000 task.model=meta-llama/Llama-3.1-8B-Instruct

# Stop a pod (keeps it for later reuse)
runpod-cli pause

# Destroy a pod
runpod-cli destroy
```

## Commands

| Command | Description |
|---------|-------------|
| `runpod-cli` | Launch/connect to a pod (default task: dashboard) |
| `runpod-cli config` | Create or update global user configuration |
| `runpod-cli config --local` | Create or update local config (`.runpod/config.yaml`) |
| `runpod-cli pause` / `stop` | Stop the latest pod (can be resumed later) |
| `runpod-cli destroy` | Terminate and delete the latest pod |

## Configuration

### User config

Created interactively on first run or via `runpod-cli config`:

- **username** (required) — prefixes pod names for identification
- **api_key** — RunPod API key (or set `RUNPOD_API_KEY` env var)
- **git_name** / **git_email** — for git on pods
- **hf_token** — HuggingFace token for private models

Stored in `~/.config/runpod-cli/config.yaml` (global). Use `runpod-cli config --local` to save per-project overrides in `.runpod/config.yaml` — local values override global.

### Hydra overrides

The CLI uses [Hydra](https://hydra.cc) for configuration. Override any setting on the command line:

```bash
# Different GPU
runpod-cli gpu=h100

# Multiple overrides
runpod-cli gpu=a100 storage=large open_ide=false stream_output=false

# Show resolved config without running
runpod-cli --cfg job
```

### Built-in config options

**GPU** (`gpu=`): `a40` (default), `a100`, `h100`, `2a100`, `4a40`

**Storage** (`storage=`): `default` (100GB vol / 200GB disk), `large` (500GB / 500GB)

**Task** (`task=`): `dashboard` (default), `testing`, `vllm`

**IDE** (`ide=`): `cursor` (default), `vscode`

### Custom config overrides

Add your own configs at two levels — both are auto-discovered, no extra flags needed:

**Global** (`~/.config/runpod-cli/configs/`) — available everywhere:
```
~/.config/runpod-cli/configs/
  task/
    my_vllm.yaml       # runpod-cli task=my_vllm
  gpu/
    custom.yaml         # runpod-cli gpu=custom
```

**Local** (`.runpod/` in current directory) — per-project, takes precedence over global:
```
your-project/
  .runpod/
    task/
      experiment.yaml   # runpod-cli task=experiment
```

Precedence: local `.runpod/` > global `~/.config/runpod-cli/configs/` > built-in defaults.

## vLLM server

Launch a vLLM OpenAI-compatible server with dynamic LoRA support:

```bash
runpod-cli task=vllm gpu=a100 app_port=8000 task.model=meta-llama/Llama-3.1-8B-Instruct
```

This uses RunPod's official vLLM template (`vllm/vllm-openai:latest`). The server starts as the container's main process — no SSH/tmux needed.

**Endpoints** available at `http://<pod-ip>:<port>/v1/`:
- `/v1/chat/completions`
- `/v1/completions`
- `/v1/models`
- `/v1/load_lora_adapter` / `/v1/unload_lora_adapter` (dynamic LoRA)

**Override vLLM settings:**

```bash
# Custom LoRA config
runpod-cli task=vllm gpu=h100 app_port=8000 \
  task.model=my-org/my-model \
  task.max_loras=8 \
  task.max_lora_rank=128 \
  task.gpu_memory_utilization=0.9
```

## How it works

1. Creates (or resumes) a RunPod GPU pod
2. For `remote_command` tasks: SSHes in, runs the command in a tmux session
3. For `docker_args` tasks (vLLM): the container starts the server directly
4. Updates `~/.ssh/config` for easy SSH access (`ssh <pod-name>`)
5. Opens the service URL in your browser
