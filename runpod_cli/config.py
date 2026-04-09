"""User configuration management for RunPod CLI."""

import os
import sys
from pathlib import Path
from typing import Optional

import yaml
from huggingface_hub import get_token as hf_get_token

CONFIG_DIR = Path.home() / ".config" / "runpod-cli"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
LOCAL_CONFIG_FILE = Path(".runpod") / "config.yaml"
LATEST_POD_FILE = CONFIG_DIR / "latest_pod"


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

    # 1. Username (required)
    print("Username (used to prefix pod names):")
    while True:
        name = _prompt_field(
            "Username (lowercase, alphanumeric)",
            config.get("name"),
            required=True,
        )
        name = name.strip().lower()
        if _validate_username(name):
            config["name"] = name
            break
        print("    Must be alphanumeric (hyphens/underscores allowed)")

    # 2. RunPod API key (optional)
    print("\nRunPod API key:")
    env_key = os.environ.get("RUNPOD_API_KEY")
    if env_key:
        print("  (RUNPOD_API_KEY found in environment — will be used automatically)")
    print("  Enter your key, or press Enter if you'll set RUNPOD_API_KEY in your environment.")
    config["api_key"] = _prompt_field("API key", config.get("api_key"), secret=True)

    # 3. Git config (optional)
    print("\nGit configuration (for committing on pods):")
    config["git_name"] = _prompt_field("Git name", config.get("git_name"))
    config["git_email"] = _prompt_field("Git email", config.get("git_email"))

    # 4. HF token (optional)
    print("\nHugging Face token (for private models/datasets):")
    print("  Enter a token, 'd' for default (~/.huggingface/token), or Enter to skip.")
    hf_input = _prompt_field("HF token", config.get("hf_token"), secret=True)
    if hf_input and hf_input.lower() == "d" and hf_input != config.get("hf_token"):
        hf_token = hf_get_token()
        assert hf_token is not None, (
            "Default HF token not found. Run 'huggingface-cli login' first."
        )
        config["hf_token"] = hf_token
        print("    Using default token from Hugging Face CLI.")
    else:
        config["hf_token"] = hf_input

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


def save_latest_pod_id(pod_id: str):
    """Save pod ID to latest_pod file."""
    _ensure_config_dir()
    LATEST_POD_FILE.write_text(pod_id)
    print(f"   Saved pod ID to {LATEST_POD_FILE}")


def get_latest_pod_id() -> Optional[str]:
    """Read pod ID from latest_pod file if it exists."""
    if LATEST_POD_FILE.exists():
        pod_id = LATEST_POD_FILE.read_text().strip()
        if pod_id:
            return pod_id
    return None
