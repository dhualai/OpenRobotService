"""简化检索验证 — 直接查 Qdrant，不依赖项目 imports"""
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

LOCAL_PATH = "D:/Code/OpenRobotService/ai/kb/qdrant"
TEAM_COL = "team_20260806_133840"

client = QdrantClient(path=LOCAL_PATH)

print(f"Team 集合: {TEAM_COL}")

# 统计 sub_domain 分布
sub_domains = {}
offset = None
while True:
    points, offset = client.scroll(
        collection_name=TEAM_COL,
        limit=500, offset=offset,
        with_payload=True, with_vectors=False,
    )
    if not points:
        break
    for p in points:
        sd = p.payload.get("sub_domain", "")
        if sd not in sub_domains:
            sub_domains[sd] = []
        sub_domains[sd].append(p)

print(f"\n{'=' * 60}")
print("sub_domain chunk 分布:")
print(f"{'=' * 60}")
for sd, pts in sorted(sub_domains.items()):
    print(f"  [{sd:35s}] {len(pts):4d} chunks")

total = sum(len(v) for v in sub_domains.values())
print(f"\n  合计: {total} chunks")

# payload filter 精确验证
print(f"\n{'=' * 60}")
print("payload filter 验证 (每类取一条样例):")
print(f"{'=' * 60}")
all_ok = True
for sd in sorted(sub_domains.keys()):
    pts, _ = client.scroll(
        collection_name=TEAM_COL,
        scroll_filter=Filter(must=[FieldCondition(key="sub_domain", match=MatchValue(value=sd))]),
        limit=1,
        with_payload=True,
        with_vectors=False,
    )
    if pts:
        p = pts[0].payload
        title = p.get("title", "")[:60]
        source = p.get("source", "")[:60]
        content = p.get("content", "")[:80].replace("\n", " ")
        print(f"  [OK] {sd}")
        print(f"       title: {title}")
        print(f"       source: {source}")
        print(f"       content: {content}...")
    else:
        print(f"  [FAIL] {sd}: NO RESULTS!")
        all_ok = False
    print()

# 内容完整性检查
print(f"{'=' * 60}")
print("内容完整性检查:")
print(f"{'=' * 60}")
empty_count = 0
for sd, pts in sub_domains.items():
    empties = [p for p in pts if not p.payload.get("content", "").strip()]
    if empties:
        print(f"  [WARN] {sd}: {len(empties)} 个空内容 chunk")
        empty_count += len(empties)
if empty_count == 0:
    print("  全部 chunk 内容非空 [OK]")
else:
    print(f"  共计 {empty_count} 个空 chunk")

# 各类型代表性内容
print(f"\n{'=' * 60}")
print("各 sub_domain 代表性内容采样:")
print(f"{'=' * 60}")
for sd in sorted(sub_domains.keys()):
    pts = sub_domains[sd]
    mid = pts[len(pts)//2].payload
    text = mid.get("content", "")[:120].replace("\n", " ")
    print(f"  [{sd}] {text}...")
    print()

client.close()
print(f"\n{'=' * 60}")
print(f"总结: {len(sub_domains)} 个 sub_domains, {total} chunks, filter全通过: {all_ok}")
print(f"{'=' * 60}")
