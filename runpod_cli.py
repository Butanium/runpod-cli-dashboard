#!/usr/bin/env python3
"""
RunPod CLI Dashboard
Creates a pod, connects via SSH, launches HTTP server, and opens in browser
"""

import os
import sys
import time
import webbrowser
from dotenv import load_dotenv
import hydra
from omegaconf import DictConfig

from utils.api import RunPodClient, pause_pod, destroy_pod
from utils.ssh import (
    SSHConnection,
    check_tmux_session_exists,
    kill_tmux_session,
    create_tmux_session_with_logging,
    stream_tmux_output,
)
from utils.config import get_or_prompt_user, save_latest_pod_id, get_latest_pod_id
from utils.utils import print_section, check_http_server_running

# Load environment variables
load_dotenv()


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
