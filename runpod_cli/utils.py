"""General utility functions."""

import time
import requests


def print_section(title: str):
    """Print a formatted section header."""
    print(f"\n{'=' * 80}")
    print(f"{title}")
    print("=" * 80)


def check_http_server_running(ip: str, public_port: int, timeout: int = 5) -> bool:
    """Check if HTTP server is responding on direct TCP connection.

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


def wait_for_http_ready(ip: str, port: int, timeout: int = 300, interval: int = 10) -> bool:
    """Wait for HTTP server to respond, polling with retries.

    Args:
        ip: Public IP address
        port: Public port number
        timeout: Total timeout in seconds
        interval: Seconds between retries

    Returns:
        True if server responded, False if timed out
    """
    start = time.time()
    while time.time() - start < timeout:
        if check_http_server_running(ip, port):
            return True
        elapsed = int(time.time() - start)
        print(f"  [{elapsed}s] Waiting for server to be ready...")
        time.sleep(interval)
    return False
