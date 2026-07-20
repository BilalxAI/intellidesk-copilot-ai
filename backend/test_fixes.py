import requests
import json

BASE_URL = "http://localhost:8000"
CONV_ID = "test-full-chain"

# Test 1: Normal response - start conversation
print("=== TEST 1: Initial Teams Issue ===")
resp = requests.post(f"{BASE_URL}/chat", json={
    "message": "my Teams is not working",
    "conversation_id": CONV_ID
})
data = resp.json()
print(f'Category: {data.get("category")}')
print(f'Response (first 200 chars):\n{data.get("response")[:200]}')
print()

# Test 2: Now ask for guided steps in SAME conversation
print("=== TEST 2: Guided Mode (should be ONE step, category preserved) ===")
resp2 = requests.post(f"{BASE_URL}/chat", json={
    "message": "ok give me the solution one step at a time",
    "conversation_id": CONV_ID
})
data2 = resp2.json()
print(f'Category: {data2.get("category")}')
print(f'Response:\n{data2.get("response")}')
print()

# Test 3: Follow-up in guided mode should give NEXT step
print("=== TEST 3: Guided Follow-up (should be NEXT step) ===")
resp3 = requests.post(f"{BASE_URL}/chat", json={
    "message": "failed that did not work",
    "conversation_id": CONV_ID
})
data3 = resp3.json()
print(f'Category: {data3.get("category")}')
print(f'Response:\n{data3.get("response")}')