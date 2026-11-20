// 三类预览（将填写 / 将覆盖 / 未匹配到节点）的共用实现：勾选确认 → 逐节点落库。
//
// 「文件导入」（AI 识别上传的文档）与「同步信息」（企业微信台账的本地镜像）两个入口共用这一份：
// 两边差别只在预览数据从哪来，勾选确认与落库行为必须完全一致——覆盖是改已有内容、
// 新建是动树结构，任何一边放松都会在 403 上中途断掉，而前面写进去的值回滚不了。
//
// 三组（与需求一致）：
//   将填写      节点当前为空，勾选后直接填入
//   将覆盖      节点已有内容，勾选后才会覆盖；每行显示「原内容 → 新内容」
//   未匹配到节点 勾选后作为新节点创建；每行显示建议归属
//
// 「没归属的条目怎么办」两个入口口径不同（allowFallbackRoot）：
//   文件导入 —— 按需建一个「导入信息」根节点把它们装起来（设计稿的兜底）；
//   台账同步 —— **不新建任何根节点**（用户口径「不要新增根节点，而是和各节点及其下拉选项
//   相匹配」）：没有归属的条目在预览里置灰、默认不勾，只作为「台账里有、树里还没有」的提醒。
//
// 默认勾选由入口决定（defaultCheckedAll）：文件导入只勾「将填写」（识别可能有偏差，先填空的
// 最保险）；台账同步要一步到位，默认三组全勾（用户口径「节点默认全选」）。
//
// 权限：前两组只是写值（任何登录用户都能写已存在节点的值），第三组要新建节点、
// 属于改信息树结构——canEditTree（本项目成员或 admin）为假时整组置灰不可勾，
// 避免勾了之后整批在 403 上中途断掉（前面已写入的值得不到回滚）。
import { useState } from 'react';
import { Toast } from 'tdesign-mobile-react';
import type { ApiParseMatchedItem, ApiParseNewItem } from '@/api/infoNodes';
import {
  createInfoNode,
  setInfoNodeValue,
  type ProjectInfoNode,
  type ProjectInfoSelectValue,
} from '@/shared/utils/projectInfoTree';
import { isKnownVehicleModel, VEHICLE_MODEL_CODES } from '@/shared/utils/vehicleModels';

/** 未匹配条目没有建议归属时的兜底根节点（按需创建；只有文件导入会走到这里） */
const FALLBACK_ROOT_TITLE = '导入信息';

/** 台账同步下「没归属的条目」为什么不能勾（与文件导入的兜底根节点相对） */
const NO_TARGET_NOTE = '树里没有对应的节点，也没有相近的归类位置——同步不新建一级标签，可到编辑页增补这类字段后再同步';

/** 三类预览数据；文件识别与台账同步的后端返回都是这个形状（两边各自还带别的字段） */
export interface ImportPreviewData {
  fill: ApiParseMatchedItem[];
  overwrite: ApiParseMatchedItem[];
  unmatched: ApiParseNewItem[];
}

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
  /** 落不了库的原因（有值即置灰不勾）：台账同步下没有归属的条目 */
  blocked?: string;
}

const GROUP_META: { key: ImportGroup; label: string; hint: string }[] = [
  { key: 'fill', label: '将填写', hint: '节点当前为空，勾选后直接填入' },
  { key: 'overwrite', label: '将覆盖', hint: '节点已有内容，勾选后才会覆盖' },
  { key: 'unmatched', label: '未匹配到节点', hint: '勾选后作为新节点创建' },
];

/** 台账同步的第三组说明：没有归属的条目不建节点，与上面那句合起来读 */
const UNMATCHED_HINT_NO_ROOT = '勾选后建在建议归属的节点下；没有归属的只作提醒，不会新建一级标签';

/** 预览数据 + 本地节点 → 渲染行；匹配条目在本地的节点不存在（被其他协作者删/改）时降级为未匹配 */
function buildRows(
  result: ImportPreviewData | null,
  nodes: ProjectInfoNode[],
  allowFallbackRoot: boolean,
): ImportRow[] {
  if (!result) return [];
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const rows: ImportRow[] = [];

  const pushFresh = (fresh: ApiParseNewItem, key: string) => {
    // 没有建议归属的条目：文件导入有「导入信息」兜底根可去，台账同步哪儿也不建 → 置灰不勾
    const blocked = !allowFallbackRoot && !fresh.suggested_parent_id ? NO_TARGET_NOTE : undefined;
    rows.push({ key, group: 'unmatched', fresh, blocked });
  };

  const pushMatched = (group: 'fill' | 'overwrite', item: ApiParseMatchedItem, index: number) => {
    const node = byId.get(item.node_id);
    if (!node || (node.content_type !== 'text' && node.content_type !== 'select')) {
      pushFresh({
        title: item.title, value: item.value,
        suggested_parent_id: null, suggested_parent_path: item.path,
      }, `${group}-${index}`);
      return;
    }
    rows.push({ key: `${group}-${index}`, group, matched: item, node });
  };

  result.fill.forEach((item, index) => pushMatched('fill', item, index));
  result.overwrite.forEach((item, index) => pushMatched('overwrite', item, index));
  result.unmatched.forEach((item, index) => pushFresh(item, `unmatched-${index}`));
  return rows;
}

/** 默认勾选：只勾「将填写」（节点本来就空，直接填风险最小）；全勾模式下三组都上，
 * 但「未匹配到节点」里落不了库的那些（没归属、或没有改树权限）仍然不勾——
 * 它们是置灰的，勾上只会让按钮数字骗人 */
function defaultChecked(rows: ImportRow[], all: boolean, canEditTree: boolean): Set<string> {
  return new Set(
    rows
      .filter((row) => row.blocked === undefined && (all
        ? !(row.group === 'unmatched' && !canEditTree)
        : row.group === 'fill'))
      .map((row) => row.key),
  );
}

export default function ProjectInfoImportPreview({
  result, projectId, nodes, canEditTree, confirmText = '确认导入', defaultCheckedAll = false,
  allowFallbackRoot = true, onApplied, onClose,
}: {
  /** 待预览的三组数据；null 表示还没拿到（不渲染内容行） */
  result: ImportPreviewData | null;
  projectId: string;
  /** 当前项目的全部信息节点（扁平，含匹配目标与归属节点） */
  nodes: ProjectInfoNode[];
  /** 能不能改这棵树（本项目成员或 admin）：决定「未匹配到节点」那组能不能真的建节点 */
  canEditTree: boolean;
  /** 确认按钮文案（两个入口各自的说法：确认导入 / 确认同步） */
  confirmText?: string;
  /** 打开时三组是否默认全勾（默认只勾「将填写」；台账同步要一步到位，传 true） */
  defaultCheckedAll?: boolean;
  /** 没有建议归属的条目能不能落到「导入信息」兜底根节点上（文件导入 true；台账同步 false） */
  allowFallbackRoot?: boolean;
  /** 落库后回调（调用方重新拉树）；随后会关闭弹层 */
  onApplied: () => void;
  /** 取消 / 落库完成后关闭（调用方顺手重置自己的预览数据） */
  onClose: () => void;
}) {
  const rows = buildRows(result, nodes, allowFallbackRoot);
  const [checked, setChecked] = useState<Set<string>>(
    () => defaultChecked(rows, defaultCheckedAll, canEditTree),
  );
  const [saving, setSaving] = useState(false);
  // 换了预览数据（重新识别 / 重新同步）就按默认勾选重来；树被协作者改动不重置，
  // 免得清掉用户已经勾好的选择。渲染期调整 state（React 官方「props 变化时调整」写法），
  // 不用 effect——那样会先拿旧勾选渲染一帧。
  const [seenResult, setSeenResult] = useState(result);
  if (seenResult !== result) {
    setSeenResult(result);
    setChecked(defaultChecked(buildRows(result, nodes, allowFallbackRoot), defaultCheckedAll, canEditTree));
  }

  const toggle = (key: string) => setChecked((current) => {
    const next = new Set(current);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  const errMsg = (err: unknown, fallback: string) =>
    err instanceof Error && err.message ? err.message : fallback;

  const applyImport = async () => {
    // 「未匹配到节点」要新建节点（结构类接口）：不是本项目的人时即便混进了勾选也跳过，
    // 否则整批会在中途 403 中断，前面已写入的值又回滚不了；落不了库的行（blocked）同理
    const picked = rows.filter(
      (row) => checked.has(row.key) && !row.blocked && (canEditTree || row.group !== 'unmatched'),
    );
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
        // 新建走「本项目增补」那条路（canEditTree=true → POST /projects/{id}，只动本项目，
        // 不动全局模板）；canEditTree 为假时根本到不了这里——那组在预览里已置灰不可勾。
        let parentId: string | null = fresh.suggested_parent_id ?? null;
        if (!parentId) {
          if (!allowFallbackRoot) continue;   // 台账同步：没有归属的不建（行上已置灰，这里兜底）
          if (!fallbackRootId) {
            fallbackRootId = nodes.find((node) => node.parent_id === null && node.title === FALLBACK_ROOT_TITLE)?.id
              ?? (await createInfoNode(projectId, null, nextSort(null), FALLBACK_ROOT_TITLE, canEditTree)).id;
          }
          parentId = fallbackRootId;
        }
        // 车型型号（车型目录里的一款）落成**下拉节点**而不是「标题=型号」的文本节点：
        // 车型是选出来的值，做成下拉后各项目能各自选、也能在编辑页里改选。
        const isModel = isKnownVehicleModel(fresh.title);
        const createdNode = await createInfoNode(
          projectId, parentId, nextSort(parentId), fresh.title.slice(0, 80), canEditTree,
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
          const qtyNode = await createInfoNode(projectId, createdNode.id, nextSort(createdNode.id), '数量', canEditTree);
          await setInfoNodeValue(qtyNode, fresh.quantity, projectId);
        }
        created += 1;
      }
      Toast({ message: `已填写 ${filled} 项，覆盖 ${overwritten} 项，新增 ${created} 项`, theme: 'success' });
      onApplied();
      onClose();
    } catch (err) {
      Toast({ message: `导入保存失败：${errMsg(err, '请稍后重试')}`, theme: 'error' });
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
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
              <p className="mac-import__group-hint">
                {group.key === 'unmatched' && !canEditTree
                  ? '新建节点要改信息树结构，只有该项目的人员（或管理员）能导；这一组请交给他们'
                  : group.key === 'unmatched' && !allowFallbackRoot ? UNMATCHED_HINT_NO_ROOT : group.hint}
              </p>
              {items.map((row) => (
                <label key={row.key} className="mac-import__row">
                  <input
                    type="checkbox"
                    checked={checked.has(row.key)}
                    disabled={row.blocked !== undefined || (row.group === 'unmatched' && !canEditTree)}
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
                        {row.blocked
                          ? row.blocked
                          : `建议归属：${row.fresh?.suggested_parent_path || `${FALLBACK_ROOT_TITLE}（将自动创建）`}`}
                        {row.fresh?.quantity ? ` · 数量 ${row.fresh.quantity}（落车型子节点）` : ''}
                      </span>
                    )}
                    {/* 后端给的「为什么没匹配上」说明（台账同步专用：如同名下拉装不下这个值） */}
                    {row.fresh?.note && (
                      <span className="mac-import__note mac-import__note--warn">{row.fresh.note}</span>
                    )}
                  </span>
                </label>
              ))}
            </div>
          );
        })}
        {rows.length === 0 && <div className="mac-info__state">没有需要处理的信息</div>}
      </div>
      <div className="mac-import__actions">
        <button type="button" className="mac-btn mac-btn--outline" disabled={saving} onClick={onClose}>取消</button>
        <button
          type="button"
          className="mac-btn mac-btn--primary"
          disabled={saving || !rows.length}
          onClick={() => void applyImport()}
        >
          {saving ? '正在保存…' : `${confirmText}（${checked.size}）`}
        </button>
      </div>
    </>
  );
}
