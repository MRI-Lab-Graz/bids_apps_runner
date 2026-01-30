import requests

repo = "nipreps/fmriprep"
url = f"https://registry.hub.docker.com/v2/repositories/{repo}/tags?page_size=100&ordering=last_updated"
try:
    response = requests.get(url, timeout=10)
    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        tags = [t["name"] for t in data.get("results", [])]
        print(f"Tags found: {len(tags)}")
        print(f"First 5 tags: {tags[:5]}")
    else:
        print(f"Error: {response.text}")
except Exception as e:
    print(f"Exception: {e}")
