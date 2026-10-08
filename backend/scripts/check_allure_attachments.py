import json, pathlib
files = sorted(
    pathlib.Path("allure-results").glob("*result.json"),
    key=lambda f: f.stat().st_mtime, reverse=True,
)
count = 0
for f in files:
    r = json.loads(f.read_text("utf-8"))
    if "status_transition" in r.get("fullName", ""):
        atts = r.get("attachments", [])
        print(f"  {r['name']}: {len(atts)} attachments")
        for a in atts:
            src = pathlib.Path("allure-results") / a["source"]
            content = src.read_text("utf-8")[:100] if src.exists() else "[missing]"
            print(f"    - {a['name']}: {content}...")
        count += 1
print(f"Total transition tests with attachments: {count}")
