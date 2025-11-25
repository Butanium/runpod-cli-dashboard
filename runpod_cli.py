#!/usr/bin/env python3
"""
RunPod CLI Dashboard
Creates a pod, connects via SSH, launches HTTP server, and opens in browser
"""

import os
import sys
import time
import webbrowser
import codecs
from typing import Dict, Optional
from pathlib import Path
import requests
import paramiko
import yaml
from dotenv import load_dotenv
import hydra
from omegaconf import DictConfig

# Load environment variables
load_dotenv()

USER_CONFIG_FILE = Path(__file__).parent / ".user.yaml"


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


class RunPodClient:
    """Client for interacting with RunPod API"""

    def __init__(self, api_key: str, api_url: str):
        self.api_key = api_key
        self.api_url = api_url

    def _graphql_query(self, query: str) -> Dict:
        """Execute a GraphQL query against RunPod API"""
        try:
            response = requests.post(
                f"{self.api_url}?api_key={self.api_key}",
                json={"query": query},
                headers={"content-type": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"API Error: {e}")
            print(
                f"Response: {response.text if 'response' in locals() else 'No response'}"
            )
            raise

    def get_pod(self, pod_id: str) -> Optional[Dict]:
        """Get details for a specific pod including GPU type and status"""
        query = f"""
        query Pod {{
          pod(input: {{podId: "{pod_id}"}}) {{
            id
            name
            desiredStatus
            machine {{
              gpuTypeId
            }}
            runtime {{
              ports {{
                ip
                isIpPublic
                privatePort
                publicPort
                type
              }}
              uptimeInSeconds
            }}
          }}
        }}
        """
        result = self._graphql_query(query)
        if "data" in result and result["data"].get("pod"):
            return result["data"]["pod"]
        return None

    def get_user_ssh_keys(self) -> Optional[str]:
        """Get user's SSH public keys from RunPod account"""
        query = """
        query {
          myself {
            pubKey
          }
        }
        """
        result = self._graphql_query(query)
        if "data" in result and result["data"].get("myself"):
            return result["data"]["myself"].get("pubKey")
        return None

    def create_pod(
        self,
        template_id: str,
        name: str,
        gpu_type_id: str,
        app_port: int,
        volume_gb: int,
        container_disk_gb: int,
        volume_mount: str,
    ) -> Optional[str]:
        """Create a new on-demand pod"""
        print(f"Creating pod with template {template_id}, GPU: {gpu_type_id}")
        print(f"   Volume: {volume_gb}GB, Container Disk: {container_disk_gb}GB")

        # Get SSH public keys from account
        ssh_keys = self.get_user_ssh_keys()
        if ssh_keys:
            print("   SSH keys retrieved from account")
        else:
            print("   WARNING: No SSH keys found in account")

        # Build env variables
        env_vars = []
        if ssh_keys:
            # Escape the SSH keys for JSON
            escaped_keys = ssh_keys.replace('"', '\\"').replace("\n", "\\n")
            env_vars.append(f'{{key: "PUBLIC_KEY", value: "{escaped_keys}"}}')

        env_string = f"env: [{', '.join(env_vars)}]" if env_vars else ""

        mutation = f"""
        mutation {{
          podFindAndDeployOnDemand(
            input: {{
              cloudType: SECURE
              gpuCount: 1
              gpuTypeId: "{gpu_type_id}"
              name: "{name}"
              templateId: "{template_id}"
              ports: "22/tcp,{app_port}/tcp"
              volumeInGb: {volume_gb}
              containerDiskInGb: {container_disk_gb}
              volumeMountPath: "{volume_mount}"
              {env_string}
            }}
          ) {{
            id
            name
            imageName
          }}
        }}
        """

        result = self._graphql_query(mutation)
        print(f"API Response: {result}")

        if "errors" in result:
            print(f"Errors creating pod: {result['errors']}")
            return None

        if "data" in result and result["data"].get("podFindAndDeployOnDemand"):
            pod_data = result["data"]["podFindAndDeployOnDemand"]
            return pod_data["id"]
        return None

    def wait_for_pod_ready(self, pod_id: str, timeout: int = 300) -> bool:
        """Wait for pod to be ready and have runtime with ports"""
        print(f"Waiting for pod {pod_id} to be ready (timeout: {timeout}s)...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            pod = self.get_pod(pod_id)
            if pod and pod.get("runtime") and pod["runtime"].get("ports"):
                print(f"Pod {pod_id} is ready!")
                return True

            elapsed = int(time.time() - start_time)
            print(f"  [{elapsed}s] Still waiting for pod to initialize...")
            time.sleep(10)

        print(f"Timeout waiting for pod {pod_id}")
        return False

    def stop_pod(self, pod_id: str) -> bool:
        """
        Stop a running pod without deleting it.
        The pod can be resumed later to avoid GPU search and initialization time.
        """
        print(f"Stopping pod {pod_id}...")

        mutation = f"""
        mutation {{
          podStop(input: {{podId: "{pod_id}"}}) {{
            id
            desiredStatus
          }}
        }}
        """

        result = self._graphql_query(mutation)

        if "errors" in result:
            print(f"Errors stopping pod: {result['errors']}")
            return False

        print(f"Pod {pod_id} stopped successfully (can be resumed later)")
        return True

    def resume_pod(self, pod_id: str, gpu_count: int = 1) -> bool:
        """
        Resume a stopped pod.
        Returns True if successful, False otherwise.
        """
        print(f"Resuming pod {pod_id}...")

        mutation = f"""
        mutation {{
          podResume(input: {{
            podId: "{pod_id}",
            gpuCount: {gpu_count}
          }}) {{
            id
            desiredStatus
            imageName
          }}
        }}
        """

        result = self._graphql_query(mutation)

        if "errors" in result:
            print(f"Errors resuming pod: {result['errors']}")
            return False

        print(f"Pod {pod_id} resumed successfully")
        return True

    def list_pods(self) -> list:
        """
        List all pods for the current user.
        Returns list of pod dictionaries with id, name, machine.gpuTypeId, and desiredStatus.
        """
        query = """
        query {
          myself {
            pods {
              id
              name
              desiredStatus
              machine {
                gpuTypeId
              }
              runtime {
                ports {
                  ip
                  publicPort
                  type
                }
              }
            }
          }
        }
        """

        result = self._graphql_query(query)

        if "data" in result and result["data"].get("myself"):
            return result["data"]["myself"].get("pods", [])
        return []

    def terminate_pod(self, pod_id: str) -> bool:
        """Terminate/delete a pod"""
        print(f"Terminating pod {pod_id}...")

        mutation = f"""
        mutation {{
          podTerminate(input: {{podId: "{pod_id}"}})
        }}
        """

        result = self._graphql_query(mutation)

        if "errors" in result:
            print(f"Errors terminating pod: {result['errors']}")
            return False

        print(f"Pod {pod_id} terminated successfully")
        return True


class SSHConnection:
    """Handle SSH connections to RunPod instances"""

    def __init__(self, host: str, port: int, username: str, timeout: int = 30):
        self.host = host
        self.port = port
        self.username = username
        self.timeout = timeout
        self.client = None

    def connect(self, pod_id: str, max_retries: int = 30) -> bool:
        """Connect to the SSH server with retries"""
        for attempt in range(max_retries):
            try:
                self.client = paramiko.SSHClient()
                self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                print(
                    f"  Attempting SSH connection (attempt {attempt + 1}/{max_retries})..."
                )
                self.client.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.username,
                    timeout=self.timeout,
                    look_for_keys=True,
                    allow_agent=True,
                )
                print(f"  Connected to {self.host}:{self.port}")
                return True
            except Exception as e:
                print(f"  SSH connection attempt {attempt + 1} failed, feel free to check the pod logs online if needed: https://console.runpod.io/pods?id={pod_id}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(15)  # Wait longer between retries
                else:
                    print("  All SSH connection attempts failed")
                    return False
        return False

    def execute_command(self, command: str, background: bool = False) -> tuple:
        """Execute a command and return stdout, stderr"""
        if not self.client:
            raise Exception("Not connected to SSH server")

        if background:
            # For background commands, just start them and return immediately
            print(f"\n  Executing command in background:\n  {command[:100]}...")
            channel = self.client.get_transport().open_session()
            channel.exec_command(command)
            time.sleep(2)  # Give it a moment to start
            return ("Background command started", "")
        else:
            print(f"\n  Executing command:\n  {command[:100]}...")
            _stdin, stdout, stderr = self.client.exec_command(command)
            stdout_str = stdout.read().decode()
            stderr_str = stderr.read().decode()
            return stdout_str, stderr_str

    def close(self):
        """Close the SSH connection"""
        if self.client:
            self.client.close()


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


def check_tmux_session_exists(ssh: SSHConnection, session_name: str) -> bool:
    """
    Check if a tmux session exists.

    Args:
        ssh: SSH connection to remote host
        session_name: Name of tmux session

    Returns:
        True if session exists, False otherwise
    """
    command = f"tmux has-session -t {session_name} 2>/dev/null && echo exists"
    stdout, _stderr = ssh.execute_command(command)
    return "exists" in stdout


def kill_tmux_session(ssh: SSHConnection, session_name: str) -> bool:
    """
    Kill a tmux session.

    Args:
        ssh: SSH connection to remote host
        session_name: Name of tmux session

    Returns:
        True if successful, False otherwise
    """
    command = f"tmux kill-session -t {session_name}"
    _stdout, stderr = ssh.execute_command(command)
    return stderr == ""


def create_tmux_session_with_logging(
    ssh: SSHConnection, session_name: str, command: str, log_file: str
) -> bool:
    """
    Create a new tmux session and configure it to log output to a file.

    Args:
        ssh: SSH connection to remote host
        session_name: Name for the tmux session
        command: Command to execute in the session
        log_file: Path to log file for output

    Returns:
        True if successful, False otherwise
    """
    # Create the tmux session
    escaped_command = command.replace("'", "'\\''")
    create_cmd = f"tmux new-session -d -s {session_name} bash -c '{escaped_command}'"
    _stdout, stderr = ssh.execute_command(create_cmd)

    if stderr:
        print(f"   Error creating tmux session: {stderr}")
        return False

    # Configure pipe-pane to log output
    pipe_cmd = f"tmux pipe-pane -t {session_name} -o 'cat >> {log_file}'"
    _stdout, stderr = ssh.execute_command(pipe_cmd)

    if stderr:
        print(f"   Warning: Could not configure logging: {stderr}")

    return True


def stream_tmux_output(ssh: SSHConnection, log_file: str):
    """
    Stream tmux log file output to terminal. Blocking until Ctrl+C.

    Args:
        ssh: SSH connection to remote host
        log_file: Path to log file to stream
    """
    print(f"\nStreaming output from {log_file} (press Ctrl+C to stop)...")
    print("=" * 80)

    command = f"tail -f {log_file}"
    channel = ssh.client.get_transport().open_session()
    channel.exec_command(command)

    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")

    try:
        while True:
            if channel.recv_ready():
                data = decoder.decode(channel.recv(1024), final=False)
                print(data, end="", flush=True)
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n" + "=" * 80)
        print("Stopped streaming output")
        channel.close()


def print_section(title: str):
    """Print a formatted section header"""
    print(f"\n{'=' * 80}")
    print(f"{title}")
    print("=" * 80)


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


def pause_pod():
    """Pause (stop) the latest pod without deleting it"""
    print_section("RunPod Pause")

    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        print("ERROR: RUNPOD_API_KEY not set in environment")
        sys.exit(1)

    pod_id = get_latest_pod_id()
    if not pod_id:
        print("ERROR: No pod found in .latest_pod file")
        print("Cannot determine which pod to pause")
        sys.exit(1)

    print(f"Found pod ID: {pod_id}")

    api_url = "https://api.runpod.io/graphql"
    client = RunPodClient(api_key, api_url)

    if client.stop_pod(pod_id):
        print(f"\nSuccessfully paused pod {pod_id}")
        print("Pod can be resumed later with the 'reuse' feature")
    else:
        print(f"\nFailed to pause pod {pod_id}")
        sys.exit(1)


def destroy_pod():
    """Shutdown the latest pod"""
    print_section("RunPod Shutdown")

    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        print("ERROR: RUNPOD_API_KEY not set in environment")
        sys.exit(1)

    pod_id = get_latest_pod_id()
    if not pod_id:
        print("ERROR: No pod found in .latest_pod file")
        print("Cannot determine which pod to shutdown")
        sys.exit(1)

    print(f"Found pod ID: {pod_id}")

    api_url = "https://api.runpod.io/graphql"
    client = RunPodClient(api_key, api_url)

    if client.terminate_pod(pod_id):
        print(f"\nSuccessfully shut down pod {pod_id}")
        Path(".latest_pod").unlink(missing_ok=True)
    else:
        print(f"\nFailed to shut down pod {pod_id}")
        sys.exit(1)


@hydra.main(version_base=None, config_path="config", config_name="config")
def main(cfg: DictConfig):
    """Main entry point for RunPod CLI Dashboard"""

    # Get API key from environment
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        print("ERROR: RUNPOD_API_KEY not set in environment")
        sys.exit(1)

    # Get or prompt for user identity
    user_name = get_or_prompt_user(cfg.get("user_name"))

    print_section("RunPod CLI Dashboard")
    print(f"User: {user_name}")

    # Initialize RunPod client
    client = RunPodClient(api_key, cfg.api_url)

    # Step 1: Get or create pod
    pod_id = cfg.target_pod_id

    if not pod_id or pod_id == "null":
        # Check if reuse is enabled and there's a latest pod
        if cfg.reuse:
            latest_pod_id = get_latest_pod_id()
            if latest_pod_id:
                print(f"\n1. Checking if latest pod {latest_pod_id} is available...")
                existing_pod = client.get_pod(latest_pod_id)

                if existing_pod:
                    if existing_pod.get("runtime"):
                        # Pod is running, reuse it
                        print(
                            f"   Latest pod {latest_pod_id} is available and running!"
                        )
                        print("   Reusing existing pod instead of creating a new one.")
                        pod_id = latest_pod_id
                    else:
                        # Pod exists but is stopped
                        print(f"   Latest pod {latest_pod_id} is stopped.")

                        # Check if GPU type matches
                        pod_gpu_type = existing_pod.get("machine", {}).get(
                            "gpuTypeId", ""
                        )
                        desired_gpu_type = cfg.gpu_type_id

                        if pod_gpu_type == desired_gpu_type:
                            # GPU matches, resume the pod
                            print(
                                f"   GPU type matches ({pod_gpu_type}). Resuming pod..."
                            )
                            if client.resume_pod(latest_pod_id):
                                print(f"   Pod {latest_pod_id} resumed successfully!")
                                pod_id = latest_pod_id
                                # Wait for pod to be ready
                                if not client.wait_for_pod_ready(
                                    pod_id, cfg.startup_wait
                                ):
                                    print(
                                        "ERROR: Pod failed to start in time after resume"
                                    )
                                    sys.exit(1)
                            else:
                                print(f"   Failed to resume pod {latest_pod_id}")
                                print(
                                    "   Will search for other stopped pods or create new one."
                                )
                        else:
                            # GPU mismatch
                            print(
                                f"   WARNING: Latest pod has GPU type '{pod_gpu_type}' but config specifies '{desired_gpu_type}'"
                            )
                            print(
                                "   Searching for stopped pods with matching GPU type..."
                            )

                            # Search all user's pods for a matching stopped pod
                            all_pods = client.list_pods()
                            pod_name_prefix = f"{user_name}-{cfg.pod_name}"

                            matching_stopped_pod = None
                            for pod in all_pods:
                                # Check if pod matches: name prefix, GPU type, and is stopped
                                if (
                                    pod.get("name", "").startswith(pod_name_prefix)
                                    and pod.get("machine", {}).get("gpuTypeId")
                                    == desired_gpu_type
                                    and not pod.get("runtime")
                                ):
                                    matching_stopped_pod = pod
                                    break

                            if matching_stopped_pod:
                                matched_pod_id = matching_stopped_pod["id"]
                                print(
                                    f"   Found stopped pod {matched_pod_id} with matching GPU type!"
                                )
                                print(f"   Resuming pod {matched_pod_id}...")

                                if client.resume_pod(matched_pod_id):
                                    print(
                                        f"   Pod {matched_pod_id} resumed successfully!"
                                    )
                                    pod_id = matched_pod_id
                                    save_latest_pod_id(pod_id)

                                    # Wait for pod to be ready
                                    if not client.wait_for_pod_ready(
                                        pod_id, cfg.startup_wait
                                    ):
                                        print(
                                            "ERROR: Pod failed to start in time after resume"
                                        )
                                        sys.exit(1)
                                else:
                                    print(f"   Failed to resume pod {matched_pod_id}")
                                    print("   Will create a new pod.")
                            else:
                                print(
                                    f"   No stopped pods found with GPU type '{desired_gpu_type}'"
                                )
                                print("   Will create a new pod.")
                else:
                    print(
                        f"   Latest pod {latest_pod_id} not found (may have been deleted)."
                    )
                    print("   Will create a new pod.")

        # Create new pod if we don't have one yet
        if not pod_id or pod_id == "null":
            # Prefix pod name with username
            pod_name = f"{user_name}-{cfg.pod_name}"

            print(f"\n1. Creating new pod with A40 GPU and template {cfg.template_id}")
            pod_id = client.create_pod(
                template_id=cfg.template_id,
                name=pod_name,
                gpu_type_id=cfg.gpu_type_id,
                app_port=cfg.app_port,
                volume_gb=cfg.volume_in_gb,
                container_disk_gb=cfg.container_disk_in_gb,
                volume_mount=cfg.volume_mount_path,
            )

            if not pod_id:
                print("ERROR: Failed to create pod")
                sys.exit(1)

            print(f"   Pod created successfully! ID: {pod_id}")

            # Save the new pod ID to .latest_pod file
            save_latest_pod_id(pod_id)

            # Wait for pod to be ready
            if not client.wait_for_pod_ready(pod_id, cfg.startup_wait):
                print("ERROR: Pod failed to start in time")
                sys.exit(1)
    else:
        print(f"\n1. Using existing pod: {pod_id}")

    # Step 2: Get pod details
    print("\n2. Fetching pod information...")
    pod = client.get_pod(pod_id)

    if not pod:
        print(f"ERROR: Pod {pod_id} not found")
        sys.exit(1)

    print(f"   Pod Name: {pod['name']}")
    print(f"   Pod ID: {pod['id']}")

    if not pod.get("runtime"):
        print("   ERROR: Pod is not running")
        sys.exit(1)

    # Extract connection information
    ports = pod["runtime"]["ports"]
    ssh_port = None
    app_port_info = None

    print("\n   Available Ports:")
    for port in ports:
        print(
            f"   - Type: {port['type']}, IP: {port['ip']}, Port: {port['publicPort']}, Public: {port['isIpPublic']}"
        )
        if port["type"] == "tcp" and port["privatePort"] == 22:
            ssh_port = port
        if port["type"] == "tcp" and port["privatePort"] == cfg.app_port:
            app_port_info = port

    print(f"   Uptime: {pod['runtime']['uptimeInSeconds']} seconds")

    # Step 3: SSH Connection and execute command
    if not ssh_port:
        print("\n3. ERROR: No SSH port found for this pod")
        sys.exit(1)

    print(f"\n3. Connecting to SSH: {ssh_port['ip']}:{ssh_port['publicPort']}")
    ssh = SSHConnection(
        host=ssh_port["ip"],
        port=ssh_port["publicPort"],
        username=cfg.ssh.username,
        timeout=cfg.ssh.timeout,
    )

    if not ssh.connect(pod_id):
        print("ERROR: Failed to connect via SSH")
        sys.exit(1)

    # Format tmux session name and log file with pod_id
    session_name = cfg.tmux_session_name.replace("{pod_id}", pod_id)
    log_file = cfg.tmux_log_file.replace("{pod_id}", pod_id)

    # Check if tmux session already exists and if HTTP server is running
    tmux_exists = check_tmux_session_exists(ssh, session_name)
    http_running = False
    if app_port_info:
        http_running = check_http_server_running(
            app_port_info["ip"], app_port_info["publicPort"]
        )

    print("\n4. Checking existing session and server status...")
    print(
        f"   TMux session '{session_name}': {'exists' if tmux_exists else 'not found'}"
    )
    print(f"   HTTP server: {'running' if http_running else 'not running'}")

    should_start_command = True

    if tmux_exists and http_running:
        if not cfg.restart_command:
            print("   Both session and server are running - skipping command execution")
            should_start_command = False
        else:
            print("   restart_command=true - killing existing tmux session")
            kill_tmux_session(ssh, session_name)

    if should_start_command:
        print(f"\n5. Starting HTTP server in tmux session '{session_name}'...")
        success = create_tmux_session_with_logging(
            ssh, session_name, cfg.remote_command, log_file
        )

        if not success:
            print("ERROR: Failed to create tmux session")
            ssh.close()
            sys.exit(1)

        print("   TMux session created successfully")
        print("   Waiting for HTTP server to initialize...")
        time.sleep(5)

    # Step 4: Get public URL and open in browser
    if not app_port_info:
        print(f"\nERROR: No TCP port found for app port {cfg.app_port}")
        ssh.close()
        sys.exit(1)

    # Use direct TCP connection: http://{ip}:{publicPort}/
    app_url = f"http://{app_port_info['ip']}:{app_port_info['publicPort']}/"
    print(f"\n6. Pod HTTP Endpoint: {app_url}")

    print(f"\n7. Opening {app_url} in browser...")
    try:
        webbrowser.open(app_url)
        print("   Browser opened successfully!")
    except Exception as e:
        print(f"   Failed to open browser: {e}")
        print(f"   Please manually open: {app_url}")

    # Step 5: Stream output if configured
    if cfg.stream_output:
        stream_tmux_output(ssh, log_file)

    ssh.close()

    print_section("Done!")
    print(f"\nPod ID: {pod_id}")
    print("Remember to stop/delete the pod when you're done to avoid charges!")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "destroy":
            destroy_pod()
            exit(0)
        elif sys.argv[1] in ["pause", "stop"]:
            pause_pod()
            exit(0)
    main()
