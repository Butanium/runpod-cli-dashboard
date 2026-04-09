# RunPod CLI Dashboard

CLI tool for managing RunPod GPU pods. Pip-installable, Hydra-configured.

## Project structure

```
runpod_cli/
  cli.py              # Entry point, Hydra main, subcommand routing
  api.py              # RunPod GraphQL API client (pods, templates, GPU types)
  config.py           # User config (~/.config/runpod-cli/), first-run setup
  ssh.py              # SSH connections, tmux management, git config on pods
  utils.py            # HTTP health checks, formatting
  hydra_config/       # Hydra YAML configs (bundled as package data)
    default.yaml      # Top-level defaults and hydra searchpath
    gpu/              # GPU presets (a40, a100, h100, 2a100, 4a40)
    storage/          # Storage presets (default, large)
    task/             # Task configs (dashboard, testing, vllm)
    ide/              # IDE configs (cursor, vscode)
```

## Key concepts

- **Entry point**: `runpod-cli` command, defined in `pyproject.toml` `[project.scripts]`
- **Subcommands**: `destroy`, `pause`/`stop`, `config` — handled before Hydra in `entry_point()`
- **User config**: `~/.config/runpod-cli/config.yaml` — username, API key, git, HF token
- **Pod tracking**: `~/.config/runpod-cli/latest_pod` — stores last created pod ID for reuse/destroy
- **Two task types**:
  - `remote_command` tasks (dashboard, testing): SSH in, run in tmux
  - `docker_args` tasks (vllm): container CMD, no SSH needed
- **Config overrides**: local `.runpod/` (CWD) and global `~/.config/runpod-cli/configs/` are auto-discovered via `hydra.searchpath`. Local > global > package defaults.

## Running

```bash
uv run runpod-cli                    # default dashboard task
uv run runpod-cli task=vllm gpu=a100 app_port=8000 task.model=org/model
uv run runpod-cli config             # interactive config
uv run runpod-cli --cfg job          # show resolved config
```

## API key resolution

1. `RUNPOD_API_KEY` env var (highest priority)
2. `api_key` in `~/.config/runpod-cli/config.yaml`
3. `.env` file via python-dotenv (backward compat)
