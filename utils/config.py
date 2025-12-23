"""User identity and pod ID management utilities"""

import sys
from pathlib import Path
from typing import Optional
import yaml
from huggingface_hub import get_token as hf_get_token

USER_CONFIG_FILE = Path(__file__).parent.parent / ".user.yaml"


def load_user_config() -> dict:
    """Load user config from .user.yaml"""
    if USER_CONFIG_FILE.exists():
        with open(USER_CONFIG_FILE, "r") as f:
            config = yaml.safe_load(f)
            return config if config else {}
    return {}


def save_user_config(config: dict):
    """Save user config to .user.yaml"""
    with open(USER_CONFIG_FILE, "w") as f:
        yaml.dump(config, f, default_flow_style=False)


def get_or_prompt_user(cli_override: Optional[str] = None) -> str:
    """Get username from CLI override, .user.yaml, or prompt user"""

    # CLI override takes precedence
    if cli_override:
        username = cli_override.strip().lower()
        if not username.replace("-", "").replace("_", "").isalnum():
            print("ERROR: Username must be alphanumeric (hyphens/underscores allowed)")
            sys.exit(1)
        return username

    # Try loading from .user.yaml
    config = load_user_config()
    if "name" in config and config["name"]:
        return config["name"]

    # Interactive prompt
    print("\n" + "=" * 80)
    print("Welcome! Please set up your user identity.")
    print("=" * 80)
    print("\nYour username will be used to:")
    print("  - Prefix pod names for easy identification")
    print("  - Track your running pods")
    print("\nThis will be saved in .user.yaml (gitignored)")

    while True:
        username = (
            input("\nEnter your username (lowercase, alphanumeric): ").strip().lower()
        )

        if not username:
            print("ERROR: Username cannot be empty")
            continue

        if not username.replace("-", "").replace("_", "").isalnum():
            print("ERROR: Username must be alphanumeric (hyphens/underscores allowed)")
            continue

        # Save to .user.yaml
        config["name"] = username
        save_user_config(config)
        print(f"\nUser identity saved to {USER_CONFIG_FILE}")
        print("=" * 80 + "\n")
        return username


def get_git_config() -> tuple[Optional[str], Optional[str]]:
    """Get git name and email from .user.yaml, prompting if not set"""
    config = load_user_config()

    git_name = config.get("git_name")
    git_email = config.get("git_email")

    # If both are set, return them
    if git_name and git_email:
        return git_name, git_email

    # If already skipped (both are explicitly None in config), don't prompt again
    if (
        git_name is None
        and "git_name" in config
        and git_email is None
        and "git_email" in config
    ):
        return None, None

    # Prompt for git configuration
    print("\n" + "=" * 80)
    print("Git Configuration Setup (Optional)")
    print("=" * 80)
    print("\nTo commit on pods, you can configure your git identity.")
    print("This is optional - press Enter to skip.")
    print("You can always edit .user.yaml later to add these settings.")

    # Prompt for git name if not set
    if not git_name:
        git_name = input(
            "\nEnter your git name (e.g., 'John Doe') [optional]: "
        ).strip()
        if not git_name:
            print("\nSkipping git name.")
            print(f"You can add git_name to {USER_CONFIG_FILE} later if needed.")
            print("=" * 80 + "\n")
            # Save None value to indicate user was prompted and skipped
            config["git_name"] = None
            save_user_config(config)
            return None, git_email

    # Prompt for git email if not set
    if not git_email:
        git_email = input(
            "Enter your git email (e.g., 'john@example.com') [optional]: "
        ).strip()
        if not git_email:
            print("\nSkipping git email.")
            print(f"You can add git_email to {USER_CONFIG_FILE} later if needed.")
            print("=" * 80 + "\n")
            # Save None value to indicate user was prompted and skipped
            config["git_email"] = None
            save_user_config(config)
            return git_name, None

    # Save to config
    config["git_name"] = git_name
    config["git_email"] = git_email
    save_user_config(config)

    print(f"\nGit configuration saved to {USER_CONFIG_FILE}")
    print("=" * 80 + "\n")

    return git_name, git_email


def get_hf_token() -> Optional[str]:
    """Get Hugging Face token from .user.yaml, prompting if not set"""
    config = load_user_config()

    hf_token = config.get("hf_token")

    if hf_token:
        return hf_token

    if hf_token is None and "hf_token" in config:
        return None

    print("\n" + "=" * 80)
    print("Hugging Face Token Setup (Optional)")
    print("=" * 80)
    print("\nTo access private models or datasets, you can configure your HF token.")
    print("This is optional - press Enter to skip.")
    print("Type 'd' to use your default token from ~/.huggingface/token")
    print("You can always edit .user.yaml later to add this setting.")

    user_input = input(
        "\nEnter your Hugging Face token [optional, 'd' for default]: "
    ).strip()

    if not user_input:
        print("\nSkipping Hugging Face token.")
        print(f"You can add hf_token to {USER_CONFIG_FILE} later if needed.")
        print("=" * 80 + "\n")
        config["hf_token"] = None
        save_user_config(config)
        return None

    if user_input.lower() == "d":
        hf_token = hf_get_token()
        assert (
            hf_token is not None
        ), "Default HF token not found. Please login with 'huggingface-cli login' first."
        print("\nUsing default token from Hugging Face CLI")
    else:
        hf_token = user_input

    config["hf_token"] = hf_token
    save_user_config(config)

    print(f"\nHugging Face token saved to {USER_CONFIG_FILE}")
    print("=" * 80 + "\n")

    return hf_token


def save_latest_pod_id(pod_id: str):
    """Save pod ID to .latest_pod file"""
    try:
        latest_pod_file = Path(".latest_pod")
        latest_pod_file.write_text(pod_id)
        print("   Saved pod ID to .latest_pod file")
    except Exception as e:
        print(f"   Warning: Could not save pod ID to .latest_pod: {e}")


def get_latest_pod_id() -> Optional[str]:
    """Read pod ID from .latest_pod file if it exists"""
    try:
        latest_pod_file = Path(".latest_pod")
        if latest_pod_file.exists():
            pod_id = latest_pod_file.read_text().strip()
            if pod_id:
                return pod_id
    except Exception as e:
        print(f"   Warning: Could not read .latest_pod file: {e}")
    return None
