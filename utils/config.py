"""User identity and pod ID management utilities"""

import sys
from pathlib import Path
from typing import Optional
import yaml

USER_CONFIG_FILE = Path(__file__).parent.parent / ".user.yaml"


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
    if USER_CONFIG_FILE.exists():
        try:
            with open(USER_CONFIG_FILE, "r") as f:
                config = yaml.safe_load(f)
                if config and "name" in config:
                    return config["name"]
        except Exception as e:
            print(f"Warning: Could not read {USER_CONFIG_FILE}: {e}")

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
        try:
            with open(USER_CONFIG_FILE, "w") as f:
                yaml.dump({"name": username}, f, default_flow_style=False)
            print(f"\nUser identity saved to {USER_CONFIG_FILE}")
            print("=" * 80 + "\n")
            return username
        except Exception as e:
            print(f"ERROR: Could not save user config: {e}")
            sys.exit(1)


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
