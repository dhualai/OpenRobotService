// 文件导入（AI 识别）弹层 —— 项目信息树编辑页「文件导入」入口。
// 对照设计稿 components/tree/NodeImportDialog.tsx：选择文件 → 正在识别 → 三组预览勾选确认。
// 实现差异：正文抽取与大模型识别都在后端（POST /info-nodes/projects/{id}/parse-file，
// 摇人同款 DeepSeek flash），前端不引 mammoth/xlsx；本弹层只预览，确认后逐节点走既有 CRUD 落库。
//
// 三组（与需求一致）：
//   将填写      节点当前为空，勾选后直接填入（默认勾选）
//   将覆盖      节点已有内容，勾选后才会覆盖；每行显示「原内容 → 新内容」
//   未匹配到节点 勾选后作为新节点创建；每行显示建议归属（没有归属时用「导入信息」兜底）
import { useMemo, useRef, useState } from 'react';
import { Popup, Toast } from 'tdesign-mobile-react';
import { MacSparkles, MacUpload } from '@/shared/components/macaronIcons';
import {
  parseImportFileApi,
  type ApiImportParseResult,
  type ApiParseMatchedItem,
  type ApiParseNewItem,
} from '@/api/infoNodes';
import {
  createInfoNode,
  setInfoNodeValue,
  type ProjectInfoNode,
  type ProjectInfoSelectValue,
} from '@/shared/utils/projectInfoTree';
import { isKnownVehicleModel, VEHICLE_MODEL_CODES } from '@/shared/utils/vehicleModels';

/** 未匹配条目没有建议归属时的兜底根节点（按需创建，与设计稿一致） */
const FALLBACK_ROOT_TITLE = '导入信息';

type ImportGroup = 'fill' | 'overwrite' | 'unmatched';

interface ImportRow {
  key: string;
  group: ImportGroup;
  /** 匹配到现有节点的条目（fill / overwrite） */
  matched?: ApiParseMatchedItem;
  /** 未匹配到节点的条目 */
  fresh?: ApiParseNewItem;
  /** 匹配条目对应的本地节点；本地树里找不到时整行降级为「未匹配」 */
  node?: ProjectInfoNode;
}

const GROUP_META: { key: ImportGroup; label: string; hint: string }[] = [
  { key: 'fill', label: '将填写', hint: '节点当前为空，勾选后直接填入' },
  { key: 'overwrite', label: '将覆盖', hint: '节点已有内容，勾选后才会覆盖' },
  { key: 'unmatched', label: '未匹配到节点', hint: '勾选后作为新节点创建' },
];

/** 预览数据 + 本地节点 → 渲染行；匹配条目在本地的节点不存在（被其他协作者删/改）时降级为未匹配 */
function buildRows(result: ApiImportParseResult | null, nodes: ProjectInfoNode[]): ImportRow[] {
  if (!result) return [];
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const rows: ImportRow[] = [];

  const pushMatched = (group: 'fill' | 'overwrite', item: ApiParseMatchedItem, index: number) => {
    const node = byId.get(item.node_id);
    if (!node || (node.content_type !== 'text' && node.content_type !== 'select')) {
      rows.push({
        key: `${group}-${index}`,
        group: 'unmatched',
        fresh: { title: item.title, value: item.value, suggested_parent_id: null, suggested_parent_path: item.path },
      });
      return;
    }
    rows.push({ key: `${group}-${index}`, group, matched: item, node });
  };

  result.fill.forEach((item, index) => pushMatched('fill', item, index));
  result.overwrite.forEach((item, index) => pushMatched('overwrite', item, index));
  result.unmatched.forEach((item, index) => rows.push({ key: `unmatched-${index}`, group: 'unmatched', fresh: item }));
  return rows;
}

export default function ProjectInfoFileImport({ visible, onClose, projectId, nodes, onApplied }: {
  visible: boolean;
  onClose: () => void;
  projectId: string;
  /** 当前项目的全部信息节点（扁平，含刚解析出的匹配目标与归属节点） */
  nodes: ProjectInfoNode[];
  /** 导入落库后回调（调用方重新拉树） */
  onApplied: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [parsing, setParsing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [result, setResult] = useState<ApiImportParseResult | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());

  const rows = useMemo(() => buildRows(result, nodes), [result, nodes]);

  const reset = () => {
    setFile(null);
    setResult(null);
    setChecked(new Set());
  };

  const close = () => {
    if (parsing || saving) return;
    reset();
    onClose();
  };

  const toggle = (key: string) => setChecked((current) => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  const errMsg = (err: unknown, fallback: string) =>
    err instanceof Error && err.message ? err.message : fallback;

  const handleFile = async (picked: File) => {
    setFile(picked);
    setResult(null);
    setChecked(new Set());
    setParsing(true);
    try {
      const parsed = await parseImportFileApi(projectId, picked);
      setResult(parsed);
      const nextRows = buildRows(parsed, nodes);
      // 默认勾选「将填写」（节点本来就空，直接填风险最小）；覆盖与新建需用户主动勾选
      setChecked(new Set(nextRows.filter((row) => row.group === 'fill').map((row) => row.key)));
      if (!nextRows.length) {
        Toast({ message: '没有识别到可导入的信息', theme: 'warning' });
      }
    } catch (err) {
      Toast({ message: errMsg(err, '文件识别失败，请稍后重试'), theme: 'error' });
    } finally {
      setParsing(false);
    }
  };

  const applyImport = async () => {
    const picked = rows.filter((row) => checked.has(row.key));
    if (!picked.length) {
      Toast({ message: '请先勾选要导入的信息', theme: 'warning' });
      return;
    }
    setSaving(true);
    let filled = 0;
    let overwritten = 0;
    let created = 0;
    // 新建节点的同级排序：本地节点数 + 本次已新建数
    const sortCounters = new Map<string | null, number>();
    const nextSort = (parentId: string | null) => {
      const base = sortCounters.get(parentId) ?? nodes.filter((node) => node.parent_id === parentId).length;
      sortCounters.set(parentId, base + 1);
      return base;
    };
    let fallbackRootId: string | null = null;
    try {
      for (const row of picked) {
        if (row.matched && row.node) {
          const node = row.node;
          // 填值走值写入接口（普通用户也能用；节点定义不动）
          if (node.content_type === 'select') {
            const options = (node.value as ProjectInfoSelectValue | null)?.options ?? [];
            await setInfoNodeValue(node, { selected: row.matched.value, options }, projectId);
          } else {
            await setInfoNodeValue(node, row.matched.value, projectId);
          }
          if (row.group === 'overwrite') overwritten += 1; else filled += 1;
          continue;
        }
        const fresh = row.fresh;
        if (!fresh) continue;
        // 未匹配条目：挂到建议归属节点；没有归属时用「导入信息」根节点兜底（按需创建一次）。
        // 导入是管理员操作，新节点按管理员的「增补」入口建（不动全局模板）
        let parentId: string | null = fresh.suggested_parent_id ?? null;
        if (!parentId) {
          if (!fallbackRootId) {
            fallbackRootId = nodes.find((node) => node.parent_id === null && node.title === FALLBACK_ROOT_TITLE)?.id
              ?? (await createInfoNode(projectId, null, nextSort(null), FALLBACK_ROOT_TITLE, true)).id;
          }
          parentId = fallbackRootId;
        }
        // 车型型号（车型目录里的一款）落成**下拉节点**而不是「标题=型号」的文本节点：
        // 车型是选出来的值，做成下拉后各项目能各自选、也能在编辑页里改选。
        const isModel = isKnownVehicleModel(fresh.title);
        const createdNode = await createInfoNode(
          projectId, parentId, nextSort(parentId), fresh.title.slice(0, 80), true,
          isModel ? 'select' : 'text',
        );
        await setInfoNodeValue(
          createdNode,
          isModel ? { selected: fresh.title, options: [...VEHICLE_MODEL_CODES] } : fresh.value,
          projectId,
        );
        // 车型条目自带数量：给新建的车型节点补一个「数量」子节点 —— 与匹配到既有
        // 车型节点时「数量落子节点」保持同一形状（后端 match_items 同样处理）
        if (isModel && fresh.quantity) {
          const qtyNode = await createInfoNode(projectId, createdNode.id, nextSort(createdNode.id), '数量', true);
          await setInfoNodeValue(qtyNode, fresh.quantity, projectId);
        }
        created += 1;
      }
      Toast({ message: `已填写 ${filled} 项，覆盖 ${overwritten} 项，新增 ${created} 项`, theme: 'success' });
      reset();
      onApplied();
      onClose();
    } catch (err) {
      Toast({ message: `导入保存失败：${errMsg(err, '请稍后重试')}`, theme: 'error' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Popup visible={visible} onClose={close} placement="bottom" showOverlay>
      <div className="mac-sheet">
        <h4 className="mac-sheet__title">文件导入</h4>
        <p className="mac-import__hint">
          支持 Word（.docx）、Markdown（.md）、文本（.txt/.csv）、Excel（.xlsx），由 AI 识别后先预览再确认。
        </p>
        <input
          ref={fileRef}
          type="file"
          accept=".docx,.md,.markdown,.txt,.csv,.xlsx"
          hidden
          onChange={(event) => {
            const selected = event.target.files?.[0];
            event.target.value = '';
            if (selected) void handleFile(selected);
          }}
        />
        <button
          type="button"
          className="mac-btn mac-btn--outline mac-btn--block mac-import__pick"
          disabled={parsing || saving}
          onClick={() => fileRef.current?.click()}
        >
          {parsing ? <MacSparkles size={13} /> : <MacUpload size={13} />}
          {parsing ? '正在识别…' : file?.name || '选择文件'}
        </button>

        {result && (
          <>
            <p className="mac-import__meta">
              识别文件：{result.file_name} · 模型：{result.model}
              {result.truncated ? ' · 内容过长已截断' : ''}
            </p>
            {result.name_mismatch && (
              <div className="mac-import__warn" role="alert">
                文件中识别到的项目名是「{result.file_project_name}」，与当前项目「{result.project_name}」不一致，
                可能导错了文件。请核对文件内容后选择「取消」，或确认无误继续导入。
              </div>
            )}
            <div className="mac-import__list">
              {GROUP_META.map((group) => {
                const items = rows.filter((row) => row.group === group.key);
                if (!items.length) return null;
                return (
                  <div key={group.key} className="mac-import__group">
                    <div className="mac-import__group-head">
                      <span className="mac-import__group-title">{group.label}</span>
                      <span className="mac-import__group-count">（{items.length}）</span>
                    </div>
                    <p className="mac-import__group-hint">{group.hint}</p>
                    {items.map((row) => (
                      <label key={row.key} className="mac-import__row">
                        <input
                          type="checkbox"
                          checked={checked.has(row.key)}
                          onChange={() => toggle(row.key)}
                          aria-label={`选择 ${row.matched ? row.matched.path : row.fresh?.title ?? ''}`}
                        />
                        <span className="mac-import__body">
                          <span className="mac-import__path">{row.matched ? row.matched.path : row.fresh?.title}</span>
                          {row.group === 'overwrite' && row.matched ? (
                            <span className="mac-import__value">
                              <span className="mac-import__old">原内容：{row.matched.current || '（空）'} → </span>
                              <span className="mac-import__new">{row.matched.value}</span>
                            </span>
                          ) : (
                            <span className="mac-import__value">{row.matched ? row.matched.value : row.fresh?.value}</span>
                          )}
                          {row.group === 'unmatched' && (
                            <span className="mac-import__note">
                              建议归属：{row.fresh?.suggested_parent_path || `${FALLBACK_ROOT_TITLE}（将自动创建）`}
                              {row.fresh?.quantity ? ` · 数量 ${row.fresh.quantity}（落车型子节点）` : ''}
                            </span>
                          )}
                        </span>
                      </label>
                    ))}
                  </div>
                );
              })}
              {rows.length === 0 && <div className="mac-info__state">没有识别到可导入的信息</div>}
            </div>
            <div className="mac-import__actions">
              <button type="button" className="mac-btn mac-btn--outline" disabled={saving} onClick={close}>取消</button>
              <button
                type="button"
                className="mac-btn mac-btn--primary"
                disabled={saving || !rows.length}
                onClick={() => void applyImport()}
              >
                {saving ? '正在保存…' : `确认导入（${checked.size}）`}
              </button>
            </div>
          </>
        )}
      </div>
    </Popup>
  );
}
