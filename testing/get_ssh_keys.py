#!/usr/bin/env python3
"""Get SSH keys from RunPod account"""

import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ.get("RUNPOD_API_KEY")

# Try to get user info including SSH keys
query = """
query {
  myself {
    id
    email
    pubKey
  }
}
"""

response = requests.post(
    f"https://api.runpod.io/graphql?api_key={api_key}",
    json={"query": query},
    headers={"content-type": "application/json"},
)

print(json.dumps(response.json(), indent=2))
