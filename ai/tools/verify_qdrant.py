"""Verify Qdrant KB — small batch scrolls"""
import sys
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

col = "team_20260806_133840"
client = QdrantClient(path="D:/Code/OpenRobotService/ai/kb/qdrant")

info = client.get_collection(col)
print(f"Collection: {col}, points: {info.points_count}")

sds = {}
off = None
batch = 50
while True:
    pts, off = client.scroll(
        collection_name=col, limit=batch, offset=off,
        with_payload=True, with_vectors=False,
    )
    if not pts:
        break
    for p in pts:
        sd = p.payload.get("sub_domain", "(none)")
        sds[sd] = sds.get(sd, 0) + 1

print("\n=== sub_domain distribution ===")
for sd, n in sorted(sds.items()):
    print(f"  [{sd:30s}] {n:4d} chunks")
print(f"  Total: {sum(sds.values())}")

print("\n=== Payload filter test ===")
for sd in sorted(sds.keys()):
    pts, _ = client.scroll(
        collection_name=col,
        scroll_filter=Filter(must=[
            FieldCondition(key="sub_domain", match=MatchValue(value=sd))
        ]),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    if pts:
        p = pts[0].payload
        cl = len(p.get("content", ""))
        t = p.get("title", "")[:60]
        print(f"  [OK] {sd}: len={cl}, title={t}")
    else:
        print(f"  [FAIL] {sd}: NO RESULTS!")

print("\n=== Content verification ===")
empty = 0
off = None
while True:
    pts, off = client.scroll(
        collection_name=col, limit=batch, offset=off,
        with_payload=True, with_vectors=False,
    )
    if not pts:
        break
    for p in pts:
        if not p.payload.get("content", "").strip():
            empty += 1

total = sum(sds.values())
print(f"Empty: {empty}/{total}")
print(f"All non-empty: {empty == 0}")

print("\n=== Sample from each sub_domain ===")
for sd in sorted(sds.keys()):
    pts, _ = client.scroll(
        collection_name=col,
        scroll_filter=Filter(must=[
            FieldCondition(key="sub_domain", match=MatchValue(value=sd))
        ]),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    if pts:
        text = pts[0].payload.get("content", "")[:100].replace("\n", " ")
        print(f"  [{sd}] {text}...")

client.close()
print("\nDone.")
