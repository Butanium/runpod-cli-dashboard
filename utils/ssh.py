"""SSH connection and tmux management utilities"""

import os
import re
import time
import codecs
import paramiko
from pathlib import Path


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
    # Use -i flag for interactive shell so .bashrc doesn't exit early
    escaped_command = command.replace("'", "'\\''")
    create_cmd = f"tmux new-session -d -s {session_name} bash -i -c '{escaped_command}'"
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


def configure_git(ssh: SSHConnection, git_name: str, git_email: str) -> bool:
    """
    Configure git user.name and user.email on the remote pod.

    Args:
        ssh: SSH connection to remote host
        git_name: Git user name to configure
        git_email: Git user email to configure

    Returns:
        True if successful, False otherwise
    """
    commands = [
        f'git config --global user.name "{git_name}"',
        f'git config --global user.email "{git_email}"'
    ]
    
    for command in commands:
        _stdout, stderr = ssh.execute_command(command)
        if stderr:
            print(f"   Warning: Git config command failed: {stderr}")
            return False
    
    return True


def update_ssh_config(pod_name: str, host: str, port: int, username: str = "root") -> bool:
    """
    Add or update an SSH config entry for a pod.

    Args:
        pod_name: Name to use as the Host alias in SSH config
        host: Hostname or IP address
        port: SSH port number
        username: SSH username (default: root)

    Returns:
        True if successful, False otherwise
    """
    ssh_config_path = Path.home() / ".ssh" / "config"

    # Ensure .ssh directory exists
    ssh_dir = ssh_config_path.parent
    ssh_dir.mkdir(mode=0o700, exist_ok=True)

    # Create the new config entry
    new_entry = f"""Host {pod_name}
    HostName {host}
    User {username}
    Port {port}
    ForwardAgent yes
    StrictHostKeyChecking no
    UserKnownHostsFile=/dev/null
"""

    try:
        # Read existing config if it exists
        existing_config = ""
        if ssh_config_path.exists():
            existing_config = ssh_config_path.read_text()

        # Check if entry already exists for this pod name
        # Pattern matches "Host {pod_name}" followed by config until next "Host " or end
        pattern = rf"Host {re.escape(pod_name)}\s*\n(?:[ \t]+[^\n]+\n)*"

        if re.search(pattern, existing_config):
            # Replace existing entry
            updated_config = re.sub(pattern, new_entry, existing_config)
        else:
            # Append new entry
            if existing_config and not existing_config.endswith("\n"):
                existing_config += "\n"
            updated_config = existing_config + "\n" + new_entry

        # Write updated config
        ssh_config_path.write_text(updated_config)

        # Set proper permissions on Unix systems
        if os.name != "nt":
            os.chmod(ssh_config_path, 0o600)

        return True

    except Exception as e:
        print(f"   Warning: Failed to update SSH config: {e}")
        return False
