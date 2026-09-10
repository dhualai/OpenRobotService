# -*- coding: utf-8 -*-
"""工单知识卡的「固有资料」存续：知识库换集合不丢。

工单沉淀卡（sub_domain=ticket_resolutions）只活在 Qdrant 里、不在 kb/ 源目录——
知识库重入库换 company 集合时整批丢下（0910 实锤：0904 换集合丢了 0903 放行的
60 张 approved，检索自此命中不到）。本模块把卡片升级为固有资料：

- 源文件 <数据根>/kb/ticket_resolutions_cards.jsonl：每行 {id, vector, payload}，
  向量原样保存（dense 数组 + sparse {indices,values}），不依赖重嵌入
- ingest_all 入库完 company 域后自动调 carry_over_cards：源文件 ∪ 全部旧
  company_* 集合的活卡 → 按 approved 优先去重 → upsert 进新集合 → 回写源文件
- review_resolutions --apply 同步源文件（approved 更新行 / 删除则移除行）
- worker 无需改动：新 pending 卡在下一次换集合时被 carry_over 收编

手动修（不等人库）：python -m ai.core.ticket_card_store [--target 集合名]
"""
import argparse
import json
import os


def _source_path():
    from ai.config import _KB_DIR
    return _KB_DIR.parent / "kb" / "ticket_resolutions_cards.jsonl"


def load_source() -> dict:
    src = _source_path()
    cards = {}
    if src.exists():
        for line in src.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                cards[d["id"]] = d
    return cards


def save_source(cards: dict) -> None:
    src = _source_path()
    src.parent.mkdir(parents=True, exist_ok=True)
    with open(src, "w", encoding="utf-8") as fh:
        for d in cards.values():
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")


def _client():
    from ai.config import get_ai_config
    from qdrant_client import QdrantClient
    cfg = get_ai_config()
    if cfg.qdrant_local_path:
        from ai.ingestion.base import BaseIngester
        return BaseIngester._make_qdrant_client(cfg)
    return QdrantClient(host=os.getenv("QDRANT_HOST", "localhost"),
                        port=int(os.getenv("QDRANT_PORT", "6333")),
                        check_compatibility=False)


def _scroll_cards(qc, col):
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    flt = Filter(must=[FieldCondition(key="sub_domain",
                                      match=MatchValue(value="ticket_resolutions"))])
    pts, off = [], None
    while True:
        batch, off = qc.scroll(col, scroll_filter=flt, limit=64, offset=off,
                               with_payload=True, with_vectors=True)
        pts.extend(batch)
        if off is None:
            break
    return pts


def _vec_to_json(v):
    from qdrant_client.models import SparseVector
    if isinstance(v, SparseVector):
        return {"indices": list(v.indices), "values": list(v.values)}
    return list(v)


def _vec_from_json(v):
    from qdrant_client.models import SparseVector
    if isinstance(v, dict):
        return SparseVector(indices=v["indices"], values=v["values"])
    return v


def _prio(payload):
    """合并优先级：approved > pending；同状态比 reviewed_at 新。"""
    return (payload.get("review_status") == "approved",
            payload.get("reviewed_at") or "")


def carry_over_cards(target_col: str = "", qc=None, write_source: bool = True) -> dict:
    """源文件 ∪ 全部旧 company_* 集合的活卡 → upsert 进目标集合并回写源文件。"""
    from ai.config import get_active_collection_for
    target = target_col or get_active_collection_for("company")
    if not target:
        raise RuntimeError("company 域没有 active 集合指针")
    qc = qc or _client()
    merged = load_source()
    src_n = len(merged)
    old_cols = [c.name for c in qc.get_collections().collections
                if c.name.startswith("company_") and c.name != target]
    col_n = 0
    for col in old_cols:
        for p in _scroll_cards(qc, col):
            col_n += 1
            d = {"id": str(p.id),
                 "vector": {k: _vec_to_json(v) for k, v in (p.vector or {}).items()},
                 "payload": p.payload or {}}
            old = merged.get(d["id"])
            if old is None or _prio(d["payload"]) >= _prio(old["payload"]):
                merged[d["id"]] = d
    if merged:
        from qdrant_client.models import PointStruct
        qc.upsert(target, points=[
            PointStruct(id=d["id"],
                        vector={k: _vec_from_json(v) for k, v in d["vector"].items()},
                        payload=d["payload"])
            for d in merged.values()], wait=True)
    if write_source:
        save_source(merged)
    n_app = sum(1 for d in merged.values()
                if d["payload"].get("review_status") == "approved")
    print(f"[ticket-cards] 源文件 {src_n} ∪ 旧集合 {col_n} 张（{len(old_cols)} 个集合）"
          f"→ 去重 {len(merged)} 张已 upsert 进 {target}"
          f"（approved {n_app}｜pending {len(merged) - n_app}）")
    return {"total": len(merged), "approved": n_app, "collection": target}


def main():
    ap = argparse.ArgumentParser(description="工单知识卡搬运（换集合不丢）")
    ap.add_argument("--target", default="", help="目标集合名（缺省=当前 active company）")
    args = ap.parse_args()
    qc = _client()
    try:
        carry_over_cards(args.target, qc=qc)
    finally:
        qc.close()


if __name__ == "__main__":
    main()
