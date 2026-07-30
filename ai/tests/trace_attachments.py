"""附件解析全链路 Trace — 从文件入口到 LogSubAgent 分析，每一步可视化"""
import sys, asyncio, os, time, io
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / "ai" / ".env")

LOG_ZIP = Path(__file__).parent / "test_logs" / "logs_20260728_135123.zip"
SEP = "=" * 70


async def main():
    print(SEP)
    print("附件解析全链路 Trace")
    print(SEP)

    # ================================================================
    # Step 0: 入口
    # ================================================================
    print("\n[Step 0] 附件列表入口")
    print(f"  测试文件: {LOG_ZIP}")
    print(f"  文件大小: {LOG_ZIP.stat().st_size / 1024 / 1024:.1f} MB")

    attachments = [{"filename": "logs_20260728_135123.zip", "path": str(LOG_ZIP.resolve()), "url": ""}]
    for a in attachments:
        print(f"    - {a['filename']}")

    # ================================================================
    # Step 1: 文件类型判断
    # ================================================================
    print(f"\n[Step 1] 文件类型判断 (_ext)")
    from ai.agents.AiTaskPlatform.attachments.parser import (
        _ext, _IMAGE_EXTS, _LOG_EXTS, _DOC_EXTS, _STRUCT_EXTS, _ARCHIVE_EXTS
    )
    for a in attachments:
        fname = a["filename"]; ext = _ext(fname, "")
        print(f"  {fname}: ext={ext!r}  archive={ext in _ARCHIVE_EXTS}  log={ext in _LOG_EXTS}  doc={ext in _DOC_EXTS}")

    # ================================================================
    # Step 2: 压缩包解压 → 列出内部文件 (对齐 pipeline.py 新逻辑)
    # ================================================================
    print(f"\n[Step 2] 压缩包解压 & 日志路径提取")
    import tempfile, zipfile, tarfile, gzip, shutil

    log_paths = []
    _tmp_dirs = []

    for a in attachments:
        path = a["path"]; name = a.get("filename", "").lower()
        if not os.path.isfile(path):
            print(f"  跳过 {name} (非本地文件)")
            continue
        if name.endswith((".zip", ".tar", ".tgz", ".gz")):
            try:
                tmp_dir = tempfile.mkdtemp(prefix="trace_")
                _tmp_dirs.append(tmp_dir)
                print(f"  解压 {name} ({os.path.getsize(path) / 1024 / 1024:.0f} MB)")

                if name.endswith(".zip"):
                    with zipfile.ZipFile(path) as zf:
                        for info in zf.infolist()[:50]:
                            if info.is_dir():
                                continue
                            zf.extract(info, tmp_dir)
                            inner = os.path.join(tmp_dir, info.filename)
                            iname = info.filename.lower()
                            ext = _ext(info.filename, "")
                            is_log = ext in _LOG_EXTS or ".log." in iname
                            tag = "[LOG]" if is_log else "     "
                            size_mb = info.file_size / (1024 * 1024)
                            print(f"    {tag} {info.filename} ({size_mb:.0f} MB)")
                            if is_log:
                                log_paths.append(inner)
                else:
                    bio = io.BytesIO(open(path, "rb").read())
                    if name.endswith((".tgz", ".gz")):
                        bio = io.BytesIO(gzip.decompress(bio.read()))
                    with tarfile.open(fileobj=bio, mode="r:*") as tf:
                        for member in tf.getmembers()[:50]:
                            if member.isdir():
                                continue
                            tf.extract(member, tmp_dir)
                            inner = os.path.join(tmp_dir, member.name)
                            iname = member.name.lower()
                            ext = _ext(member.name, "")
                            is_log = ext in _LOG_EXTS or ".log." in iname
                            tag = "[LOG]" if is_log else "     "
                            size_mb = member.size / (1024 * 1024)
                            print(f"    {tag} {member.name} ({size_mb:.0f} MB)")
                            if is_log:
                                log_paths.append(inner)
            except Exception as e:
                print(f"   解压失败: {e}")
        elif name.endswith((".log", ".txt", ".csv")) or ".log." in name:
            log_paths.append(path)
            print(f"  直接日志: {name}")

    print(f"\n  log_paths 共 {len(log_paths)} 个:")
    for lp in log_paths:
        sz = os.path.getsize(lp) / (1024 * 1024)
        print(f"    {lp} ({sz:.0f} MB)")

    # ================================================================
    # Step 3: parse_attachments 全量解析
    # ================================================================
    print(f"\n[Step 3] parse_attachments 全量解析")
    from ai.agents.AiTaskPlatform.attachments.parser import parse_attachments
    t0 = time.perf_counter()
    result = await parse_attachments(attachments)
    elapsed = (time.perf_counter() - t0) * 1000
    print(f"  耗时: {elapsed:.0f}ms")
    print(f"  has_logs={result.has_logs}  has_screenshots={result.has_screenshots}")
    print(f"  log_summary ({len(result.log_summary)} 字):")
    for line in result.log_summary.replace("\r", "").split("\n")[:8]:
        print(f"    {line[:150]}")

    # ================================================================
    # Step 4: 非日志附件
    # ================================================================
    print(f"\n[Step 4] 管道拆分")
    _PIPED = (".log", ".txt", ".csv", ".zip", ".tar", ".tgz", ".gz")
    log_group = [a for a in attachments if a.get("filename", "").lower().endswith(_PIPED) or ".log." in a.get("filename", "").lower()]
    non_log_group = [a for a in attachments if a not in log_group]
    print(f"  日志组 ({len(log_group)} 件): {[a['filename'] for a in log_group]}")
    print(f"  非日志组 ({len(non_log_group)} 件): {[a['filename'] for a in non_log_group]}")
    if non_log_group:
        t0 = time.perf_counter()
        r2 = await parse_attachments(non_log_group)
        print(f"  parse_attachments: {len(r2.log_summary)} 字, has_logs={r2.has_logs}")
    else:
        print(f"  无非日志附件，跳过")

    # ================================================================
    # Step 5: 图片
    # ================================================================
    print(f"\n[Step 5] 图片附件")
    imgs = [a for a in attachments if _ext(a.get("filename", ""), "") in _IMAGE_EXTS]
    if imgs:
        for a in imgs:
            print(f"  [IMG] {a['filename']}")
    else:
        print(f"  无图片附件")

    # ================================================================
    # Step 6: LogSubAgent (用提取后的日志路径，不是 zip)
    # ================================================================
    print(f"\n[Step 6] LogSubAgent 分析")

    if log_paths:
        log_file = log_paths[0]
        fsize = os.path.getsize(log_file) / (1024 * 1024)
        print(f"  输入: {log_file} ({fsize:.0f} MB)")
        from ai.agents.AiTaskPlatform.log_analyzer.sub_agent import LogSubAgent

        task_ctx = {
            "title": "[Trace] 日志自动分析",
            "description": "用户上传日志压缩包，期望自动排查",
            "problem_summary": "排查日志中的异常和错误",
            "hypotheses": ["路径规划异常", "AGV通信故障"],
            "ruled_out": ["网络断开"],
            "robot_type": "", "fault_code": "", "collected_info": {},
        }
        t0 = time.perf_counter()
        try:
            sub = LogSubAgent(log_file)
            lr = await sub.analyze(task_ctx, user_question=f"日志分析，重点排查: {task_ctx['problem_summary']}")
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"  耗时: {elapsed:.0f}ms  轮数: {lr.queries_made}  证据: {len(lr.evidence)}  兜底: {lr.fallback_used}")
            if lr.conclusion:
                print(f"\n  --- 结论 ---")
                for line in lr.conclusion[:600].replace("\r", "").split("\n")[:10]:
                    print(f"    {line}")
            if lr.evidence:
                print(f"\n  --- 关键证据 (前 5) ---")
                for e in lr.evidence[:5]:
                    print(f"    L{e['line']} | {e['summary'][:120]}")
            print(f"\n  --- Prompt 注入 (前 800 字) ---")
            for line in lr.to_prompt_text()[:800].replace("\r", "").split("\n")[:15]:
                print(f"   {line}")
        except Exception as e:
            print(f"  失败: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
    else:
        print(f"  无日志文件，跳过 LogSubAgent")

    # 清理临时目录
    for td in _tmp_dirs:
        shutil.rmtree(td, ignore_errors=True)

    print(f"\n{SEP}")
    print("全链路 Trace 完成")
    print(SEP)


if __name__ == "__main__":
    asyncio.run(main())
