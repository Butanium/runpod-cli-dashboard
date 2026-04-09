"""User configuration management for RunPod CLI."""

import os
import sys
from pathlib import Path
from typing import Optional

import json
import yaml
from huggingface_hub import get_token as hf_get_token

CONFIG_DIR = Path.home() / ".config" / "runpod-cli"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
LOCAL_CONFIG_FILE = Path(".runpod") / "config.yaml"
ACTIVE_POD_FILE = Path(".runpod") / "active_pod.json"


def _load_config_file(path: Path) -> dict:
    """Load a single YAML config file."""
    if path.exists():
        with open(path) as f:
            config = yaml.safe_load(f)
            return config if config else {}
    return {}


def _save_config_file(config: dict, path: Path):
    """Save config to a YAML file, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


def load_user_config() -> dict:
    """Load user config, merging global and local (local overrides global)."""
    config = _load_config_file(CONFIG_FILE)
    local = _load_config_file(LOCAL_CONFIG_FILE)
    config.update({k: v for k, v in local.items() if v is not None})
    return config


def save_user_config(config: dict):
    """Save user config to global config file."""
    _save_config_file(config, CONFIG_FILE)


def _mask_secret(value: str, show_chars: int = 4) -> str:
    """Mask a secret value for display."""
    if len(value) <= show_chars * 2:
        return "***"
    return f"{value[:show_chars]}...{value[-show_chars:]}"


def _validate_username(username: str) -> bool:
    """Check if username is valid (alphanumeric + hyphens/underscores)."""
    return bool(username) and username.replace("-", "").replace("_", "").isalnum()


def _prompt_field(
    label: str,
    current=None,
    required: bool = False,
    secret: bool = False,
) -> Optional[str]:
    """Prompt for a config field value.

    Returns new value, or current if user presses Enter.
    Returns None if optional field is skipped.
    """
    if current is not None:
        display = _mask_secret(str(current)) if secret else str(current)
        prompt = f"  {label} [{display}]: "
    elif required:
        prompt = f"  {label}: "
    else:
        prompt = f"  {label} (optional, Enter to skip): "

    value = input(prompt).strip()

    if not value:
        if required and current is None:
            print("    This field is required.")
            return _prompt_field(label, current, required, secret)
        return current

    return value


def run_setup(first_time: bool = True, local: bool = False) -> dict:
    """Interactive configuration setup.

    Args:
        first_time: If True, shows welcome message. If False, shows current config
                    and allows updating individual fields.
        local: If True, saves to .runpod/config.yaml in CWD instead of global config.
    """
    config_path = LOCAL_CONFIG_FILE if local else CONFIG_FILE
    config = _load_config_file(config_path)

    print("\n" + "=" * 60)
    if first_time:
        print("Welcome to RunPod CLI! Let's set up your configuration.")
    else:
        scope = "Local" if local else "Global"
        print(f"RunPod CLI — Update {scope} Configuration")
    print("=" * 60)
    print(f"Config file: {config_path}")

    if not first_time and config:
        print("\nCurrent settings:")
        for key, value in config.items():
            if value is None:
                display = "(not set)"
            elif key in ("hf_token", "api_key") and value:
                display = _mask_secret(str(value))
            else:
                display = str(value)
            print(f"    {key}: {display}")

    if not first_time:
        print("\nPress Enter to keep current value.")
    print()

    # 1. Username (required) — default to system username
    import getpass
    name_default = config.get("name") or getpass.getuser().lower().replace(" ", "-")

    print("Username (used to prefix pod names):")
    while True:
        name = _prompt_field(
            "Username (lowercase, alphanumeric)",
            name_default,
            required=True,
        )
        name = name.strip().lower()
        if _validate_username(name):
            config["name"] = name
            break
        print("    Must be alphanumeric (hyphens/underscores allowed)")

    # 2. RunPod API key (optional)
    env_key = os.environ.get("RUNPOD_API_KEY")
    if env_key:
        print(f"\nRunPod API key: detected from RUNPOD_API_KEY env var ({_mask_secret(env_key)})")
        print("  Enter a different key to store in config, or press Enter to use the env var.")
        config["api_key"] = _prompt_field("API key", config.get("api_key"), secret=True)
    else:
        print("\nRunPod API key:")
        print("  Enter your key, or press Enter if you'll set RUNPOD_API_KEY in your environment.")
        config["api_key"] = _prompt_field("API key", config.get("api_key"), secret=True)

    # 3. Git config (optional) — default to global git config
    import subprocess
    def _git_global(key: str) -> str | None:
        try:
            return subprocess.run(
                ["git", "config", "--global", key],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip() or None
        except Exception:
            return None

    git_name_default = config.get("git_name") or _git_global("user.name")
    git_email_default = config.get("git_email") or _git_global("user.email")

    print("\nGit configuration (for committing on pods):")
    config["git_name"] = _prompt_field("Git name", git_name_default)
    config["git_email"] = _prompt_field("Git email", git_email_default)

    # 4. HF token (optional) — auto-detect from huggingface-cli
    hf_default = config.get("hf_token")
    if not hf_default:
        hf_default = hf_get_token()

    if hf_default:
        print(f"\nHugging Face token: detected ({_mask_secret(hf_default)})")
        config["hf_token"] = _prompt_field("HF token", hf_default, secret=True)
    else:
        print("\nHugging Face token (for private models/datasets):")
        print("  No token detected. Enter one, or press Enter to skip.")
        config["hf_token"] = _prompt_field("HF token", secret=True)

    _save_config_file(config, config_path)
    print(f"\nConfiguration saved to {config_path}")
    print("=" * 60 + "\n")

    return config


def ensure_config() -> dict:
    """Load config, running first-time setup if needed."""
    config = load_user_config()
    if not config or "name" not in config or not config["name"]:
        config = run_setup(first_time=True)
    return config


def get_or_prompt_user(cli_override: Optional[str] = None) -> str:
    """Get username from CLI override or config."""
    if cli_override:
        username = cli_override.strip().lower()
        if not _validate_username(username):
            print("ERROR: Username must be alphanumeric (hyphens/underscores allowed)")
            sys.exit(1)
        return username

    config = ensure_config()
    return config["name"]


def get_git_config() -> tuple[Optional[str], Optional[str]]:
    """Get git name and email from config."""
    config = load_user_config()
    return config.get("git_name"), config.get("git_email")


def get_hf_token() -> Optional[str]:
    """Get HF token from config."""
    config = load_user_config()
    return config.get("hf_token")


def get_api_key() -> Optional[str]:
    """Get RunPod API key from env var or config.

    Resolution order:
    1. RUNPOD_API_KEY environment variable
    2. api_key in user config
    """
    env_key = os.environ.get("RUNPOD_API_KEY")
    if env_key:
        return env_key
    config = load_user_config()
    return config.get("api_key")


def save_pod_state(
    pod_id: str,
    task_config_name: str | None = None,
    task_config: dict | None = None,
    host: str | None = None,
    port: int | None = None,
    created_at: str | None = None,
):
    """Save active pod state to .runpod/active_pod.json.

    Can be called multiple times — later calls merge new fields into existing state.
    """
    from datetime import datetime, timezone

    ACTIVE_POD_FILE.parent.mkdir(parents=True, exist_ok=True)

    state = load_pod_state() or {}
    state["pod_id"] = pod_id
    if task_config_name is not None:
        state["task_config_name"] = task_config_name
    if task_config is not None:
        state["task_config"] = task_config
    if host is not None:
        state["host"] = host
    if port is not None:
        state["port"] = port
    if created_at is not None:
        state["created_at"] = created_at
    elif "created_at" not in state:
        state["created_at"] = datetime.now(timezone.utc).isoformat()

    ACTIVE_POD_FILE.write_text(json.dumps(state, indent=2) + "\n")
    print(f"   Saved pod state to {ACTIVE_POD_FILE}")


def load_pod_state() -> dict | None:
    """Load active pod state from .runpod/active_pod.json."""
    if ACTIVE_POD_FILE.exists():
        try:
            return json.loads(ACTIVE_POD_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def get_latest_pod_id() -> Optional[str]:
    """Get pod_id from saved pod state."""
    state = load_pod_state()
    return state.get("pod_id") if state else None


def clear_pod_state():
    """Remove the active pod state file."""
    ACTIVE_POD_FILE.unlink(missing_ok=True)
