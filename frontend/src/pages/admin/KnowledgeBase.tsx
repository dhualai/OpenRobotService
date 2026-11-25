// 知识库浏览（ORS-716）—— 后台「数据资源 → 知识库」。
// 手机端为主：目录逐层进入、点文件全屏预览、图片自适应宽度。

import { useCallback, useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  fetchKbFile,
  fetchKbTree,
  kbMediaUrl,
  resolveKbMedia,
  type KbDoc,
  type KbTree,
} from '@/api/kb';

const KB_CSS = `
.kb-page { padding: 16px 14px 40px; }
.kb-crumb { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; font-size: 13px; color: #6b7280; margin-bottom: 12px; }
.kb-crumb button { background: none; border: none; padding: 2px 4px; font-size: 13px; color: #2563eb; cursor: pointer; }
.kb-crumb span.sep { color: #cbd5e1; }
.kb-item { display: flex; align-items: center; gap: 10px; padding: 14px 14px; background: #fff; border-radius: 12px; margin-bottom: 8px; cursor: pointer; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
.kb-item:active { background: #f8fafc; }
.kb-item .ic { font-size: 18px; flex: 0 0 22px; text-align: center; }
.kb-item .nm { flex: 1; font-size: 15px; color: #111827; word-break: break-all; }
.kb-item .mt { flex: 0 0 auto; font-size: 12px; color: #9ca3af; }
.kb-empty { text-align: center; color: #9ca3af; font-size: 14px; padding: 40px 0; }
.kb-doc-head { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; }
.kb-doc-head button { background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 6px 12px; font-size: 14px; color: #2563eb; cursor: pointer; }
.kb-doc-head .t { flex: 1; font-size: 14px; color: #374151; word-break: break-all; }
.kb-md { background: #fff; border-radius: 12px; padding: 14px 16px; font-size: 15px; line-height: 1.75; color: #1f2937; overflow-wrap: break-word; }
.kb-md h1 { font-size: 20px; margin: 4px 0 14px; }
.kb-md h2 { font-size: 17px; margin: 20px 0 10px; padding-bottom: 4px; border-bottom: 1px solid #f1f5f9; }
.kb-md h3 { font-size: 16px; margin: 16px 0 8px; }
.kb-md p { margin: 8px 0; }
.kb-md img { max-width: 100%; height: auto; border-radius: 8px; display: block; margin: 10px 0; background: #f8fafc; }
.kb-md table { display: block; width: 100%; overflow-x: auto; border-collapse: collapse; font-size: 13px; margin: 10px 0; }
.kb-md th, .kb-md td { border: 1px solid #e5e7eb; padding: 6px 8px; text-align: left; white-space: nowrap; }
.kb-md th { background: #f8fafc; font-weight: 600; }
.kb-md code { background: #f1f5f9; padding: 1px 5px; border-radius: 4px; font-size: 13px; }
.kb-md pre { background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 8px; overflow-x: auto; font-size: 13px; }
.kb-md pre code { background: none; color: inherit; padding: 0; }
.kb-md blockquote { margin: 10px 0; padding: 6px 12px; border-left: 3px solid #cbd5e1; color: #64748b; background: #f8fafc; }
.kb-md ul, .kb-md ol { padding-left: 22px; }
`;

export default function KnowledgeBase() {
  const [path, setPath] = useState('');
  const [tree, setTree] = useState<KbTree | null>(null);
  const [doc, setDoc] = useState<KbDoc | null>(null);
  const [loading, setLoading] = useState(false);

  const loadTree = useCallback(async (p: string) => {
    setLoading(true);
    setTree(await fetchKbTree(p));
    setLoading(false);
  }, []);

  useEffect(() => { void loadTree(path); }, [path, loadTree]);

  const openFile = async (p: string) => {
    setLoading(true);
    const d = await fetchKbFile(p);
    setLoading(false);
    if (d) {
      setDoc(d);
      window.scrollTo({ top: 0 });
    }
  };

  const go = (p: string) => { setDoc(null); setPath(p); };

  if (doc) {
    return (
      <div className="kb-page">
        <style>{KB_CSS}</style>
        <div className="kb-doc-head">
          <button onClick={() => setDoc(null)}>← 返回</button>
          <span className="t">{doc.label || doc.name}</span>
        </div>
        <div className="kb-md">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              img: ({ src, alt }) => (
                <img
                  src={kbMediaUrl(resolveKbMedia(doc.path, String(src || '')))}
                  alt={alt || ''}
                  loading="lazy"
                />
              ),
              a: ({ href, children }) => (
                <a href={href} target="_blank" rel="noreferrer">{children}</a>
              ),
            }}
          >
            {doc.content}
          </ReactMarkdown>
        </div>
      </div>
    );
  }

  return (
    <div className="kb-page">
      <style>{KB_CSS}</style>
      <div className="kb-crumb">
        {(tree?.crumbs || [{ label: '知识库', path: '' }]).map((c, i, arr) => (
          <span key={c.path || 'root'}>
            {i < arr.length - 1 ? (
              <button onClick={() => go(c.path)}>{c.label}</button>
            ) : (
              <span style={{ color: '#111827', fontWeight: 600 }}>{c.label}</span>
            )}
            {i < arr.length - 1 && <span className="sep"> / </span>}
          </span>
        ))}
      </div>

      {loading && <div className="kb-empty">加载中…</div>}
      {!loading && tree && (
        <>
          {tree.dirs.map((d) => (
            <div key={d.path} className="kb-item" onClick={() => go(d.path)}>
              <span className="ic">📁</span>
              <span className="nm">{d.label}</span>
              <span className="mt">{d.md_count} 篇</span>
            </div>
          ))}
          {tree.files.map((f) => (
            <div key={f.path} className="kb-item" onClick={() => void openFile(f.path)}>
              <span className="ic">📄</span>
              <span className="nm">{f.label}</span>
              <span className="mt">{Math.max(1, Math.round(f.size / 1024))} KB</span>
            </div>
          ))}
          {!tree.dirs.length && !tree.files.length && (
            <div className="kb-empty">这个目录下没有内容</div>
          )}
        </>
      )}
      {!loading && !tree && <div className="kb-empty">知识库读取失败</div>}
    </div>
  );
}
