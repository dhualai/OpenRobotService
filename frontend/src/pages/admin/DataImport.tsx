// 数据导入页 - 从 BackgroundService DataImport 迁移
// 说明：本页承载三类数据导入 ——
//   1) 文件导入：DAS 数据包文件（.bz2 / .json），解析后按天切分入库 CollectionData；
//   2) JSON导入：搬运效率汇总 {summary, robots}，直接写入 ProjectTransportEfficiency；
//   3) 二维码导入：微信内扫码（wx.scanQRCode）读取数据平台导出的数据二维码
//      （QRV1 单码 / QRV2 分片，gzip+Base64），后端解码后按当天切分入库 CollectionData。
//      非微信环境（PC 联调）回退为手动粘贴扫码文本。
// 项目、日期选择是整页共用的。
// 样式参考 macaron data 页：surface-card 项目选择条 + 卡片内分段切换 + 虚线文件入口。
import { useState, useEffect, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { Toast, Loading, Popup } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { normalizeList } from '@/shared/utils/list';
import { useAuthStore, PERMISSION_VIEW_ALL } from '@/stores/auth';
import { initWechatJsSdk } from '@/shared/utils/wechatJsSdk';
import { MacChevronRight, MacCheck, MacSearch, MacPlus } from '@/shared/components/macaronIcons';

// wx.scanQRCode 的局部类型声明：共享 wechatJsSdk.ts 的 WxSdk 未含 scanQRCode，
// 本页按需扩展（不改共享基建文件），配合 initWechatJsSdk(['scanQRCode']) 完成签名。
interface WxScanApi {
  scanQRCode?: (opts: {
    needResult: 1;
    scanType: string[];
    success: (res: { resultStr?: string }) => void;
    fail?: (res?: unknown) => void;
    cancel?: () => void;
  }) => void;
}

// 二维码分片：QRV2|{片号}/{总片数}| + 片数据（README 建议手动翻页逐片扫码）
interface QrFragment {
  index: number;
  total: number;
  text: string;
}

// 与 /projects/、/projects/me 实际返回字段对齐（ProjectResponse）：项目标识是 project_code（与 id 同值），
// 没有 code 字段 —— 之前用 project.code 取标识，永远取不到值会静默回退成 project.name（中文项目名），
// 导致导入数据落库的 project key 与「项目详情页-搬运效率分析」按 project_code 查询的 key 对不上，同项目的数据传不过去。
interface Project { id?: string; project_code?: string; name: string; }

// 文件导入结果 —— 后端 /data/upload-file 返回，用于展示导入成功的条目
interface ImportChunk {
  start_time: string;
  end_time: string;
  groups?: string[];
}

interface FileImportResult {
  success?: boolean;
  message?: string;
  filename?: string;
  file_size?: string;
  project?: string;
  indicator?: string;
  chunk_count?: number;
  chunks?: ImportChunk[];
}

const todayStr = (): string => {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

/** 解析扫码文本的头部标识：QRV1 单码 / QRV2 分片 / 未知内容 */
function parseQrHead(text: string):
  | { kind: 'QRV1' }
  | { kind: 'QRV2'; index: number; total: number }
  | { kind: 'unknown' } {
  const trimmed = text.trim();
  if (trimmed.startsWith('QRV1|')) return { kind: 'QRV1' };
  if (trimmed.startsWith('QRV2|')) {
    const [, head] = trimmed.split('|', 2);
    const [idxStr, totalStr] = head.split('/');
    const index = Number(idxStr);
    const total = Number(totalStr);
    if (Number.isInteger(index) && Number.isInteger(total) && index >= 1 && total >= 1) {
      return { kind: 'QRV2', index, total };
    }
  }
  return { kind: 'unknown' };
}

interface ProjectPickerFieldProps {
  projects: Project[];
  value: Project | null;
  onChange: (p: Project) => void;
}

function ProjectPickerField({ projects, value, onChange }: ProjectPickerFieldProps) {
  const [visible, setVisible] = useState(false);
  const [search, setSearch] = useState('');

  const filtered = search.trim()
    ? projects.filter((p) => {
        const kw = search.trim().toLowerCase();
        return p.name.toLowerCase().includes(kw) || (p.project_code || '').toLowerCase().includes(kw);
      })
    : projects;

  return (
    <>
      <button type="button" className="mac-selector" onClick={() => setVisible(true)}>
        <span className="mac-selector__body">
          {value ? (
            <>
              <span className="mac-selector__name">{value.name}</span>
              {(value.project_code || value.id) && (
                <span className="mac-selector__meta">#{value.project_code || value.id}</span>
              )}
            </>
          ) : (
            <span className="mac-selector__placeholder">请选择项目</span>
          )}
        </span>
        <span className="mac-selector__chevron"><MacChevronRight size={16} /></span>
      </button>

      <Popup visible={visible} onClose={() => setVisible(false)} placement="bottom" showOverlay>
        <div className="mac-sheet" style={{ maxHeight: '70vh', overflow: 'auto' }}>
          <h4 className="mac-sheet__title">选择项目</h4>
          <div className="mac-search" style={{ marginBottom: 12 }}>
            <MacSearch size={16} />
            <input
              className="mac-search__input"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="输入项目名称关键词模糊查找"
            />
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {filtered.map((p) => {
              const active = value?.name === p.name;
              return (
                <button
                  key={p.id || p.name}
                  type="button"
                  className={`mac-pick-item ${active ? 'is-active' : ''}`}
                  onClick={() => { onChange(p); setSearch(''); setVisible(false); }}
                >
                  <span className="mac-pick-item__name">{p.name}</span>
                  {p.project_code && <span className="mac-pick-item__code">#{p.project_code}</span>}
                  {active && (
                    <span className="mac-pick-item__check"><MacCheck size={16} /></span>
                  )}
                </button>
              );
            })}
            {filtered.length === 0 && (
              <div className="mac-empty">未找到匹配的项目</div>
            )}
          </div>
        </div>
      </Popup>
    </>
  );
}

export default function DataImport() {
  const [tab, setTab] = useState<'file' | 'json' | 'qr'>('file');
  const [loading, setLoading] = useState(false);
  const request = createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin');
  const { hasPermission } = useAuthStore();
  const canViewAll = hasPermission(PERMISSION_VIEW_ALL);
  const navigate = useNavigate();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [projects, setProjects] = useState<Project[]>([]);

  useEffect(() => {
    request(canViewAll ? '/projects/' : '/projects/me')
      .then((data) => setProjects(normalizeList<Project>(data)))
      .catch(() => { /* 静默失败，切到各 Tab 时不强制要求项目列表已加载 */ });
  }, [canViewAll]);

  // 整页共用：项目，两个 Tab（文件导入 / JSON导入）都是提交搬运效率数据的方式
  const [project, setProject] = useState<Project | null>(null);

  const [jsonText, setJsonText] = useState('');

  // JSON导入结果：与文件导入一致，展示成功/失败反馈（此前只有 Toast，刷新或切页后无迹可循）
  const [jsonResult, setJsonResult] = useState<{ summary?: unknown; robots?: unknown[] } | null>(null);
  const [jsonError, setJsonError] = useState<string | null>(null);

  // 文件导入结果：成功时展示条目，失败时展示错误提示
  const [fileResult, setFileResult] = useState<FileImportResult | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);

  // 二维码导入：分片收集状态（QRV2 多码时逐片扫码）、扫描/导入中标志与结果反馈
  const [qrFragments, setQrFragments] = useState<QrFragment[]>([]);
  const [qrScanning, setQrScanning] = useState(false);
  const [qrResult, setQrResult] = useState<FileImportResult | null>(null);
  const [qrError, setQrError] = useState<string | null>(null);
  // 非微信环境（PC 联调）的手动粘贴兑底：展开开关 + 粘贴文本（可多行，每行一片）
  const [qrManualOpen, setQrManualOpen] = useState(false);
  const [qrManualText, setQrManualText] = useState('');

  const handleFileUpload = async (file: File) => {
    if (!project) {
      Toast({ message: '请先选择项目', theme: 'warning' });
      return;
    }
    setLoading(true);
    setFileResult(null);
    setFileError(null);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('project', project.project_code || project.id || project.name);
      const data = await request<FileImportResult>('/data/upload-file', {
        method: 'POST',
        body: formData,
      });
      if (data.success === false) {
        setFileError(data.message || '导入失败');
      } else {
        setFileResult(data);
        Toast({ message: data.message || '数据包解析导入成功', theme: 'success' });
      }
    } catch (err) {
      console.error('[DataImport] 上传失败:', err);
      setFileError(err instanceof Error ? err.message : '未知错误');
      Toast({ message: `导入失败: ${err instanceof Error ? err.message : '未知错误'}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  const handleJsonImport = async () => {
    if (!project) { Toast({ message: '请先选择项目', theme: 'warning' }); return; }
    if (!jsonText.trim()) { Toast({ message: '请输入JSON数据', theme: 'warning' }); return; }

    let parsed: { summary?: Record<string, unknown>; robots?: Record<string, unknown>[] };
    try {
      parsed = JSON.parse(jsonText);
    } catch {
      Toast({ message: 'JSON格式有误，请检查后重试', theme: 'error' });
      return;
    }

    setLoading(true);
    setJsonResult(null);
    setJsonError(null);
    try {
      const data = await request<{ message: string; summary?: unknown; robots?: unknown[] }>('/transport-efficiency/import/json', {
        method: 'POST',
        body: JSON.stringify({
          project_code: project.project_code || project.id || project.name,
          report_date: todayStr(),
          summary: parsed.summary || {},
          robots: parsed.robots || [],
        }),
      });
      setJsonResult({ summary: data.summary, robots: data.robots });
      Toast({ message: data.message || '导入成功', theme: 'success' });
    } catch (err) {
      const msg = err instanceof Error ? err.message : '未知错误';
      setJsonError(msg);
      Toast({ message: `导入失败: ${msg}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  // ---------- 二维码导入 ----------

  /** 提交二维码内容给后端：payload（单码/已拼接）与 fragments（分片列表）二选一 */
  const submitQrImport = async (payload: string | null, fragments: string[] | null) => {
    if (!project) {
      Toast({ message: '请先选择项目', theme: 'warning' });
      return;
    }
    setLoading(true);
    setQrResult(null);
    setQrError(null);
    try {
      const data = await request<FileImportResult>('/data/import-qrcode', {
        method: 'POST',
        body: JSON.stringify({
          project: project.project_code || project.id || project.name,
          payload,
          fragments,
        }),
      });
      if (data.success === false) {
        setQrError(data.message || '导入失败');
        Toast({ message: data.message || '导入失败', theme: 'error' });
      } else {
        setQrResult(data);
        setQrFragments([]);
        Toast({ message: data.message || '二维码数据导入成功', theme: 'success' });
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : '未知错误';
      setQrError(msg);
      Toast({ message: `导入失败: ${msg}`, theme: 'error' });
    } finally {
      setLoading(false);
    }
  };

  /**
   * 处理一次扫码得到的文本：
   * - QRV1 单码 → 直接提交
   * - QRV2 分片 → 查重/校验总数后加入收集队列；集齐自动提交，否则提示扫下一片
   * - 其他 → 报错提示
   */
  const ingestQrText = async (text: string) => {
    const head = parseQrHead(text);
    if (head.kind === 'QRV1') {
      setQrFragments([]);
      await submitQrImport(text.trim(), null);
      return;
    }
    if (head.kind === 'QRV2') {
      const { index, total } = head;
      // 与已收集分片的总数不一致（可能混扫了另一组码）→ 以最新为准重新收集
      if (qrFragments.length && qrFragments[0].total !== total) {
        setQrFragments([{ index, total, text: text.trim() }]);
        Toast({ message: `检测到新的分片组（共 ${total} 片），已重新开始收集`, theme: 'warning' });
        return;
      }
      if (qrFragments.some((f) => f.index === index)) {
        Toast({ message: `第 ${index} 片已收集，请扫描下一张二维码`, theme: 'warning' });
        return;
      }
      const next = [...qrFragments, { index, total, text: text.trim() }].sort((a, b) => a.index - b.index);
      setQrFragments(next);
      if (next.length >= total) {
        // 集齐：按片号排序后的文本列表交给后端拼接
        await submitQrImport(null, next.map((f) => f.text));
      } else {
        Toast({ message: `已收集第 ${index}/${total} 片，请翻到下一张二维码继续扫描`, theme: 'success' });
      }
      return;
    }
    Toast({ message: '不是数据平台导出的数据二维码', theme: 'error' });
  };

  /** 微信 JS-SDK 调起相机扫码，返回扫码文本（非微信/未启用时抛错，由调用方提示兑底） */
  const scanQrViaWechat = async (): Promise<string> => {
    const ok = await initWechatJsSdk(['scanQRCode']);
    const wx = window.wx as (Window['wx'] & WxScanApi) | undefined;
    if (!ok || !wx?.scanQRCode) {
      throw new Error('当前环境无法调起微信扫码，请使用下方「手动粘贴二维码内容」');
    }
    return new Promise<string>((resolve, reject) => {
      wx.scanQRCode!({
        needResult: 1,
        scanType: ['qrCode'],
        success: (res) => resolve(res?.resultStr || ''),
        fail: () => reject(new Error('扫码失败，请重试')),
        cancel: () => reject(new Error('已取消扫码')),
      });
    });
  };

  const handleQrScan = async () => {
    if (!project) {
      Toast({ message: '请先选择项目', theme: 'warning' });
      return;
    }
    setQrScanning(true);
    try {
      const text = await scanQrViaWechat();
      if (!text.trim()) throw new Error('未获取到二维码内容');
      await ingestQrText(text);
    } catch (err) {
      const msg = err instanceof Error ? err.message : '未知错误';
      setQrError(msg);
      Toast({ message: msg, theme: 'error' });
    } finally {
      setQrScanning(false);
    }
  };

  /** 手动粘贴导入：单行视为完整码；多行按分片列表提交（每行一片） */
  const handleQrManualSubmit = async () => {
    if (!project) {
      Toast({ message: '请先选择项目', theme: 'warning' });
      return;
    }
    const lines = qrManualText
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!lines.length) {
      Toast({ message: '请粘贴二维码内容', theme: 'warning' });
      return;
    }
    if (lines.length === 1 && lines[0].startsWith('QRV1|')) {
      await submitQrImport(lines[0], null);
    } else {
      await submitQrImport(null, lines);
    }
  };

  return (
    <div className="mac-page">
      <ProjectPickerField
        projects={projects}
        value={project}
        onChange={(p) => {
          // 切换项目后清空上一个项目的导入结果展示，避免误认为是当前项目的导入反馈
          setProject(p);
          setFileResult(null);
          setFileError(null);
          setJsonResult(null);
          setJsonError(null);
          setQrFragments([]);
          setQrResult(null);
          setQrError(null);
        }}
      />

      <div className="mac-card" style={{ marginTop: 12 }}>
        {/* 分段切换（文件导入 / JSON导入 / 二维码导入） */}
        <div className="mac-seg">
          <button
            type="button"
            className={`mac-seg__btn ${tab === 'file' ? 'is-active' : ''}`}
            onClick={() => setTab('file')}
          >
            文件导入
          </button>
          <button
            type="button"
            className={`mac-seg__btn ${tab === 'json' ? 'is-active' : ''}`}
            onClick={() => setTab('json')}
          >
            JSON导入
          </button>
          <button
            type="button"
            className={`mac-seg__btn ${tab === 'qr' ? 'is-active' : ''}`}
            onClick={() => setTab('qr')}
          >
            二维码导入
          </button>
        </div>

        {tab === 'file' ? (
          <div style={{ padding: '20px 16px' }}>
            <input
              ref={fileInputRef}
              type="file"
              accept=".json,.bz2"
              style={{ display: 'none' }}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleFileUpload(f);
                e.target.value = '';
              }}
            />
            <button
              type="button"
              className="mac-file-drop"
              onClick={() => fileInputRef.current?.click()}
            >
              <MacPlus size={24} />
            </button>
            <p className="mac-note" style={{ marginTop: 20 }}>
              上传 数据包文件（.bz2 或 .json，含 GroupEfficiency 等指标数据），选定项目后上传
            </p>

            {/* 导入失败提示 */}
            {fileError && (
              <div className="mac-feedback mac-feedback--err" style={{ marginTop: 16 }}>
                <div style={{ fontWeight: 600 }}>✗ 导入失败</div>
                <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{fileError}</div>
              </div>
            )}

            {/* 导入成功：信息卡片 + 导入条目列表（与二维码导入共用 ImportResultCard） */}
            {fileResult && fileResult.success !== false && (
              <ImportResultCard
                result={fileResult}
                projectName={project?.name}
                onViewAnalysis={() => project?.id && navigate(`/admin/project-detail/${project.id}/transport-efficiency`)}
              />
            )}
          </div>
        ) : tab === 'json' ? (
          <div style={{ padding: '20px 16px' }}>
            <textarea
              className="mac-textarea"
              value={jsonText}
              placeholder='{"summary": {...}, "robots": [...]}'
              onChange={(e) => setJsonText(e.target.value)}
            />
            <p className="mac-note" style={{ marginTop: 12 }}>
              粘贴含 GroupEfficiency 等指标数据的 JSON，选定项目后导入
            </p>
            <button
              type="button"
              className="mac-btn mac-btn--primary mac-btn--block"
              style={{ marginTop: 16 }}
              onClick={handleJsonImport}
            >
              导入JSON数据
            </button>

            {/* 导入失败提示 */}
            {jsonError && (
              <div className="mac-feedback mac-feedback--err" style={{ marginTop: 16 }}>
                <div style={{ fontWeight: 600 }}>✗ 导入失败</div>
                <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{jsonError}</div>
              </div>
            )}

            {/* 导入成功提示 + 跳转搬运效率分析 */}
            {jsonResult && (
              <div style={{ marginTop: 16 }}>
                <div className="mac-feedback mac-feedback--ok">
                  ✓ 导入成功（项目：{project?.name || '-'}，日期：{todayStr()}）
                </div>
                {project?.id && (
                  <button
                    type="button"
                    className="mac-btn mac-btn--outline mac-btn--block"
                    style={{ marginTop: 12 }}
                    onClick={() => navigate(`/admin/project-detail/${project.id}/transport-efficiency`)}
                  >
                    查看该项目搬运效率分析 ›
                  </button>
                )}
              </div>
            )}
          </div>
        ) : (
          <div style={{ padding: '20px 16px' }}>
            <p className="mac-note">
              扫描数据平台导出的数据二维码（含 GroupEfficiency 等指标），选定项目后导入。
              多张分片二维码（QRV2）需按提示逐张扫描，收集齐后自动导入。
            </p>
            <button
              type="button"
              className="mac-btn mac-btn--primary mac-btn--block"
              style={{ marginTop: 16 }}
              disabled={qrScanning || loading}
              onClick={handleQrScan}
            >
              {qrScanning ? '正在调起相机…' : '扫描二维码'}
            </button>

            {/* 分片收集进度：已扫片号 + 重新开始 */}
            {qrFragments.length > 0 && (
              <div className="mac-card mac-card--pad" style={{ marginTop: 12 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--mac-fg)' }}>分片收集进度</span>
                  <button type="button" className="mac-btn mac-btn--outline" onClick={() => setQrFragments([])}>
                    重新开始
                  </button>
                </div>
                <p className="mac-note" style={{ marginTop: 8 }}>
                  已收集 {qrFragments.length}/{qrFragments[0].total} 片（第{' '}
                  {qrFragments.map((f) => f.index).join('、')} 片），请翻到下一张二维码继续扫描
                </p>
              </div>
            )}

            {/* 非微信环境（PC 联调）兑底：手动粘贴扫码文本 */}
            <div style={{ marginTop: 16 }}>
              <button
                type="button"
                className="mac-btn mac-btn--outline mac-btn--block"
                onClick={() => setQrManualOpen((v) => !v)}
              >
                {qrManualOpen ? '收起手动粘贴' : '无法扫码？手动粘贴二维码内容'}
              </button>
              {qrManualOpen && (
                <>
                  <textarea
                    className="mac-textarea"
                    style={{ marginTop: 12 }}
                    value={qrManualText}
                    placeholder="粘贴扫码得到的文本：单码（QRV1|…）或分片（每行一片）"
                    onChange={(e) => setQrManualText(e.target.value)}
                  />
                  <button
                    type="button"
                    className="mac-btn mac-btn--outline mac-btn--block"
                    style={{ marginTop: 12 }}
                    disabled={!qrManualText.trim() || loading}
                    onClick={handleQrManualSubmit}
                  >
                    导入粘贴内容
                  </button>
                </>
              )}
            </div>

            {/* 导入失败提示 */}
            {qrError && (
              <div className="mac-feedback mac-feedback--err" style={{ marginTop: 16 }}>
                <div style={{ fontWeight: 600 }}>✗ 导入失败</div>
                <div style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>{qrError}</div>
              </div>
            )}

            {/* 导入成功：与文件导入同一结果卡片（二维码无文件名/大小，字段留空不展示） */}
            {qrResult && qrResult.success !== false && (
              <ImportResultCard
                result={qrResult}
                projectName={project?.name}
                onViewAnalysis={() => project?.id && navigate(`/admin/project-detail/${project.id}/transport-efficiency`)}
              />
            )}
          </div>
        )}
      </div>
      {loading && <Loading text="导入中..." />}
    </div>
  );
}

// 导入成功结果卡片：文件导入与二维码导入共用（二维码无文件名/大小，对应行不展示）
function ImportResultCard({
  result,
  projectName,
  onViewAnalysis,
}: {
  result: FileImportResult;
  projectName?: string;
  onViewAnalysis?: () => void;
}) {
  return (
    <div style={{ marginTop: 16 }}>
      <div className="mac-feedback mac-feedback--ok">
        ✓ {result.message || '导入成功'}
      </div>

      {/* 信息卡片（参考 ProjectMetricsList 的 header-info） */}
      <div className="mac-item" style={{ marginTop: 12 }}>
        <InfoLine label="项目名称" value={projectName || result.project || '-'} />
        <InfoLine label="指标标签" value={result.indicator || '-'} />
        {result.filename && <InfoLine label="文件名称" value={result.filename} />}
        {result.file_size && <InfoLine label="文件大小" value={result.file_size} />}
        <InfoLine label="导入条目数" value={String(result.chunk_count ?? 0)} />
      </div>

      {/* 导入的数据条目列表 */}
      {result.chunks && result.chunks.length > 0 && (
        <div className="mac-card mac-card--pad" style={{ marginTop: 12 }}>
          <div style={{ fontSize: 14, fontWeight: 600, marginBottom: 10, color: 'var(--mac-fg)' }}>
            导入的数据条目
          </div>
          {result.chunks.map((c, idx) => (
            <div
              key={idx}
              style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '8px 0', fontSize: 13, gap: 8,
                borderBottom: idx < result.chunks!.length - 1 ? '1px solid rgba(232,234,234,0.5)' : 'none',
              }}
            >
              <span style={{ color: 'var(--mac-fg)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {c.start_time} ~ {c.end_time}
              </span>
              <span style={{ color: 'var(--mac-muted-fg)', flexShrink: 0 }}>
                {c.groups && c.groups.length ? c.groups.join('、') : '—'}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* 跳转到同一项目的搬运效率分析页，验证数据已生效 */}
      {onViewAnalysis && (
        <button
          type="button"
          className="mac-btn mac-btn--outline mac-btn--block"
          style={{ marginTop: 12 }}
          onClick={onViewAnalysis}
        >
          查看该项目搬运效率分析 ›
        </button>
      )}
    </div>
  );
}

// 信息卡片的一行：左侧灰色标签 + 右侧数值（参考 ProjectMetricsList 的 header-info）
function InfoLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="mac-labelvalue" style={{ padding: '5px 0' }}>
      <span className="mac-labelvalue__label">{label}</span>
      <span className="mac-labelvalue__value" style={{ color: 'var(--mac-fg)', fontWeight: 500, textAlign: 'right' }}>
        {value}
      </span>
    </div>
  );
}
