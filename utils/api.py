"""RunPod API client and command handlers"""

import os
import sys
import time
from pathlib import Path
from typing import Dict, Optional
import difflib
import re
import requests

from utils.config import get_latest_pod_id
from utils.utils import print_section


def _escape_gql_string(value: str) -> str:
    # GraphQL string literal escaping for our usage in f-strings.
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _normalize_for_match(s: str) -> str:
    # Normalize for human-ish fuzzy matching: casefold, drop punctuation to spaces.
    s = s.casefold()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _suggest_gpu_types(given: str, valid_ids: list[str], k: int = 5) -> list[str]:
    assert isinstance(given, str)
    assert isinstance(valid_ids, list)
    assert all(isinstance(x, str) for x in valid_ids)

    given_n = _normalize_for_match(given)
    valid_norm = {_normalize_for_match(x): x for x in valid_ids}

    # If normalization matches exactly, that's a strong "did you mean".
    if given_n in valid_norm:
        return [valid_norm[given_n]]

    # Otherwise fall back to similarity scoring on normalized strings.
    candidates = list(valid_norm.keys())
    close = difflib.get_close_matches(given_n, candidates, n=k, cutoff=0.0)
    return [valid_norm[c] for c in close[:k]]


def _merge_env_kv_list(
    template_env: list[dict], overrides: dict[str, str]
) -> list[dict]:
    """
    Merge template env list with overrides/additions.
    - preserves template order
    - overrides existing keys
    - appends new keys at the end
    """
    assert isinstance(template_env, list)
    assert all(isinstance(x, dict) for x in template_env)
    assert isinstance(overrides, dict)

    out: list[dict] = []
    seen: set[str] = set()

    for item in template_env:
        key = item["key"]
        value = item.get("value", "")
        assert isinstance(key, str)
        assert isinstance(value, str)
        if key in overrides:
            value = overrides[key]
        out.append({"key": key, "value": value})
        seen.add(key)

    for key, value in overrides.items():
        if key not in seen:
            out.append({"key": key, "value": value})

    return out


class RunPodClient:
    """Client for interacting with RunPod API"""

    def __init__(self, api_key: str, api_url: str):
        self.api_key = api_key
        self.api_url = api_url
        self._gpu_types_cache: list[dict] | None = None

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

    def get_gpu_types(self) -> list[dict]:
        """
        Return the list of available RunPod GPU types.
        This is used to validate gpu_type values early with a helpful error.
        """
        if self._gpu_types_cache is not None:
            return self._gpu_types_cache

        query = """
        query {
          gpuTypes {
            id
            displayName
            memoryInGb
          }
        }
        """
        result = self._graphql_query(query)
        gpu_types = result["data"]["gpuTypes"]
        assert isinstance(gpu_types, list)
        self._gpu_types_cache = gpu_types
        return gpu_types

    def get_template_env_kv(self, template_id: str) -> list[dict]:
        """
        Return template env as a list of {key, value} dicts.
        """
        template_id_escaped = _escape_gql_string(template_id)
        query = f"""
        query {{
          podTemplate(id: "{template_id_escaped}") {{
            env {{
              key
              value
            }}
          }}
        }}
        """
        result = self._graphql_query(query)
        data = result["data"]["podTemplate"]
        env = (data or {}).get("env") or []
        assert isinstance(env, list)
        for item in env:
            assert set(item.keys()) >= {"key", "value"}
        return env

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
        gpu_type: str,
        ngpus: int,
        app_port: int,
        volume_gb: int,
        container_disk_gb: int,
        volume_mount: str,
        cloud_type: str | None = None,
        hf_token: str | None = None,
    ) -> Optional[str]:
        """Create a new on-demand pod"""
        valid_gpu_ids = [g["id"] for g in self.get_gpu_types()]
        if gpu_type not in valid_gpu_ids:
            suggestions = _suggest_gpu_types(gpu_type, valid_gpu_ids, k=5)
            print(f"ERROR: Unknown gpu_type: {gpu_type!r}")
            if suggestions:
                print(f"Did you mean: {suggestions[0]!r}")
                if len(suggestions) > 1:
                    print("Other close matches:")
                    for s in suggestions[1:]:
                        print(f"  - {s}")
            print("\nValid gpu_type values are:")
            for gid in valid_gpu_ids:
                print(f"  - {gid}")
            return None

        print(
            f"Creating pod with template {template_id}, GPU: {gpu_type}, Count: {ngpus}"
        )
        print(f"   Volume: {volume_gb}GB, Container Disk: {container_disk_gb}GB")

        # Get SSH public keys from account
        ssh_keys = self.get_user_ssh_keys()
        if ssh_keys:
            print("   SSH keys retrieved from account")
        else:
            print("   WARNING: No SSH keys found in account")

        # Build env variables
        template_env = self.get_template_env_kv(template_id)
        overrides = {}

        if ssh_keys:
            overrides["PUBLIC_KEY"] = ssh_keys

        if hf_token:
            overrides["HF_TOKEN"] = hf_token

        if overrides:
            # IMPORTANT: RunPod treats `env` in deploy input as a full replacement,
            # so to be additive we must merge with the template env first.
            merged_env = _merge_env_kv_list(
                template_env=template_env,
                overrides=overrides,
            )
            env_vars = [
                f'{{key: "{_escape_gql_string(item["key"])}", value: "{_escape_gql_string(item["value"])}"}}'
                for item in merged_env
            ]
            env_string = f"env: [{', '.join(env_vars)}]"
            print(f"   Env variables: {[item['key'] for item in merged_env]}")
        else:
            env_string = ""

        cloud_type_string = ""
        if cloud_type is not None:
            assert cloud_type in {"SECURE", "COMMUNITY"}, cloud_type
            cloud_type_string = f"cloudType: {cloud_type}"

        mutation = f"""
        mutation {{
          podFindAndDeployOnDemand(
            input: {{
              {cloud_type_string}
              gpuCount: {ngpus}
              gpuTypeId: "{gpu_type}"
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
