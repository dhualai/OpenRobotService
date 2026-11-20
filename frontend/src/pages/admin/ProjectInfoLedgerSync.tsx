// 台账同步弹层 —— 项目信息树编辑页「同步」入口。
//
// 和「文件导入」是一件事的两种信息源：那边读上传的文档，这边读**企业微信台账在本地库里的镜像**
// （project 表，平时由 wecom adapter 从智能表格同步进来；后端 info_node_ledger_sync_service
// 把台账各列反向还原成「列名 → 值」）。后端再拿现有信息节点比对（列名 + 值：值正好是某个
// 下拉节点的可选项时也认，见 info_node_ledger_sync_service._pin_option_values），回三类：将填写 /
// 将覆盖（节点已有内容且与台账不一致 = 矛盾）/ 未匹配到节点（台账有这一列、树里没有 = 缺少的节点）。
//
// 本弹层只负责「打开即拉预览」与来源说明；预览列表与落库都交给 ProjectInfoImportPreview，
// 与文件导入共用同一套行为（勾选确认后逐节点走既有 CRUD，不落库的东西一律不写），
// 差别只在 allowFallbackRoot=false：同步**不新建一级标签**，没有归属的条目只作提醒。
//
// 拉不到（项目不存在 / 项目还没有信息节点）时不猜也不静默：弹层里写明原因并给「重试」，
// 比一闪而过的 Toast 更容易看清——用户要据此决定是先去编辑页建节点，还是找运维看数据。
import { useEffect, useState } from 'react';
import { Popup } from 'tdesign-mobile-react';
import { MacRefreshCw } from '@/shared/components/macaronIcons';
import { fetchLedgerSyncPreviewApi, type ApiLedgerSyncResult } from '@/api/infoNodes';
import type { ProjectInfoNode } from '@/shared/utils/projectInfoTree';
import ProjectInfoImportPreview from './ProjectInfoImportPreview';

export default function ProjectInfoLedgerSync({ visible, onClose, projectId, nodes, canEditTree, onApplied }: {
  visible: boolean;
  onClose: () => void;
  projectId: string;
  /** 当前项目的全部信息节点（扁平，含匹配目标与归属节点） */
  nodes: ProjectInfoNode[];
  /** 能不能改这棵树（本项目成员或 admin）：决定「未匹配到节点」那组能不能真的建节点 */
  canEditTree: boolean;
  /** 落库后回调（调用方重新拉树） */
  onApplied: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ApiLedgerSyncResult | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await fetchLedgerSyncPreviewApi(projectId));
    } catch (err) {
      setError(err instanceof Error && err.message ? err.message : '台账同步失败，请稍后重试');
    } finally {
      setLoading(false);
    }
  };

  // 打开就拉（同步是「读台账 + 本地比对」，没有要用户先选的东西）；关掉时丢掉结果，
  // 下次打开重新拉——台账是别人在改的，旧预览不能留。
  useEffect(() => {
    if (!visible) {
      setResult(null);
      setError(null);
      return;
    }
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible, projectId]);

  const close = () => {
    if (loading) return;
    onClose();
  };

  return (
    <Popup visible={visible} onClose={close} placement="bottom" showOverlay>
      <div className="mac-sheet">
        <h4 className="mac-sheet__title">同步信息</h4>
        <p className="mac-import__hint">
          基础信息比对：把本项目在台账里的基础信息与现有节点逐项核对，不一致的列在「将覆盖」，
          台账有、节点没有的列在「未匹配到节点」。列名与节点名对不上、但值正好是某个下拉的一项时，
          也归到那个节点上（比如「项目生命周期」的值对上「时间线 / 大节点」）。勾选哪条才写哪条。
        </p>

        {loading && <div className="mac-info__state">正在读取台账…</div>}

        {error && (
          <>
            <div className="mac-import__warn" role="alert">{error}</div>
            <div className="mac-import__actions">
              <button type="button" className="mac-btn mac-btn--outline" onClick={close}>取消</button>
              <button type="button" className="mac-btn mac-btn--primary" onClick={() => void load()}>
                <MacRefreshCw size={13} />重试
              </button>
            </div>
          </>
        )}

        {result && (
          <>
            <p className="mac-import__meta">
              台账数据来自本地库
              {result.ledger_updated_at ? ` · 台账更新于 ${result.ledger_updated_at}` : ''}
              {' · '}参与比对的字段 {result.field_count} 个（镜像共 {result.mirror_field_total} 列）
            </p>
            <ProjectInfoImportPreview
              result={result}
              projectId={projectId}
              nodes={nodes}
              canEditTree={canEditTree}
              confirmText="确认同步"
              defaultCheckedAll
              allowFallbackRoot={false}
              onApplied={onApplied}
              onClose={onClose}
            />
          </>
        )}
      </div>
    </Popup>
  );
}
