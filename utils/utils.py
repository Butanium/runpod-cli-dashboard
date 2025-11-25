"""General utility functions"""

import requests


def print_section(title: str):
    """Print a formatted section header"""
    print(f"\n{'=' * 80}")
    print(f"{title}")
    print("=" * 80)


def check_http_server_running(ip: str, public_port: int, timeout: int = 5) -> bool:
    """
    Check if HTTP server is responding on direct TCP connection.

    Args:
        ip: Public IP address
        public_port: Public port number
        timeout: Request timeout in seconds

    Returns:
        True if server responds with 2xx/3xx status, False otherwise
    """
    url = f"http://{ip}:{public_port}/"
    try:
        response = requests.get(url, timeout=timeout)
        return response.status_code < 400
    except Exception:
        return False
