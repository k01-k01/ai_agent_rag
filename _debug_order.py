import requests, json

# 获取第一个对话
resp = requests.get("http://localhost:8000/api/conversations")
convs = resp.json()["conversations"]
if not convs:
    print("No conversations found")
    exit()

conv_id = convs[0]["id"]
print(f"Checking conversation: {conv_id} - {convs[0]['title']}")

# 获取对话详情
resp = requests.get(f"http://localhost:8000/api/conversations/{conv_id}")
data = resp.json()

print(f"\nMessages ({len(data['messages'])}):")
for i, m in enumerate(data["messages"]):
    print(f"  [{i}] role={m['role']:10s} | id={m['id'][:8]}... | timestamp={m['timestamp']}")
