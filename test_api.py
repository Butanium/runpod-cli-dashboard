import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ.get("RUNPOD_API_KEY")

if not api_key:
    print("ERROR: RUNPOD_API_KEY environment variable not set")
    exit(1)

print(f"API Key found: {api_key[:10]}...")

# Query all pods first
query = """
query Pods {
  myself {
    pods {
      id
      name
      runtime {
        ports {
          ip
          isIpPublic
          privatePort
          publicPort
          type
        }
        uptimeInSeconds
      }
    }
  }
}
"""

response = requests.post(
    f"https://api.runpod.io/graphql?api_key={api_key}",
    json={"query": query},
    headers={"content-type": "application/json"},
)

print("\nAll Pods:")
result = response.json()
print(json.dumps(result, indent=2))

# Extract SSH info for the specific pod
if "data" in result and result["data"]["myself"]["pods"]:
    target_pod_id = "0xnkvqe9cprj8e"
    for pod in result["data"]["myself"]["pods"]:
        if pod["id"] == target_pod_id:
            print(f"\n\n=== Pod {target_pod_id} Details ===")
            print(f"Name: {pod['name']}")
            if pod["runtime"] and pod["runtime"]["ports"]:
                for port in pod["runtime"]["ports"]:
                    if port["type"] == "tcp":
                        print(f"\nSSH Connection:")
                        print(f"  ssh root@{port['ip']} -p {port['publicPort']}")
                    elif port["type"] == "http":
                        print(f"\nHTTP URL:")
                        print(f"  http://{port['ip']}:{port['publicPort']}")
