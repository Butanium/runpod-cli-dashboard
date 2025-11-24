#!/usr/bin/env python3
"""Quick script to terminate a pod"""

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ.get("RUNPOD_API_KEY")
pod_id = "y4apjlcbfjj737"

mutation = f"""
mutation {{
  podTerminate(input: {{podId: "{pod_id}"}})
}}
"""

response = requests.post(
    f"https://api.runpod.io/graphql?api_key={api_key}",
    json={"query": mutation},
    headers={"content-type": "application/json"},
)

print(f"Terminating pod {pod_id}...")
print(response.json())
