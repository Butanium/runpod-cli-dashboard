#!/usr/bin/env python3
"""Explore RunPod API for log access"""

import os
import requests
import json
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ.get("RUNPOD_API_KEY")
pod_id = "9h76091kipkjdb"

# Try to query for logs-related fields
query = f"""
query {{
  pod(input: {{podId: "{pod_id}"}}) {{
    id
    name
    runtime {{
      uptimeInSeconds
      ports {{
        ip
        publicPort
        privatePort
        type
      }}
    }}
  }}
}}
"""

response = requests.post(
    f"https://api.runpod.io/graphql?api_key={api_key}",
    json={"query": query},
    headers={"content-type": "application/json"},
)

print("Basic pod query:")
print(json.dumps(response.json(), indent=2))

# Try introspection to see what fields are available on Pod type
introspection_query = """
query {
  __type(name: "Pod") {
    fields {
      name
      description
      type {
        name
        kind
      }
    }
  }
}
"""

print("\n\nIntrospection on Pod type:")
response2 = requests.post(
    f"https://api.runpod.io/graphql?api_key={api_key}",
    json={"query": introspection_query},
    headers={"content-type": "application/json"},
)

result = response2.json()
print(json.dumps(result, indent=2))

if "data" in result and result["data"].get("__type"):
    fields = result["data"]["__type"]["fields"]
    print("\nAvailable Pod fields:")
    for field in fields:
        print(f"  - {field['name']}: {field.get('description', 'No description')}")
else:
    print("Could not get Pod type fields")
