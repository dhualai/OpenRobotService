/**
 * 提单弹窗内「问题共享文档设置」卡（需求 1 + 2）。
 *
 * 数据源：所绑定项目的信息树（GET /api/admin/info-nodes/projects/{id}，与「项目信息管理」
 * 同一棵树）。所有展示与统计都在前端完成，后端不需要新接口。
 *
 * 1. 一级标签 chips：勾选哪些标签的项目背景信息要带进文档；
 *    缺省过半（可填节点空值 > 50%）的标签右上角出感叹号（`projectInfoTree.computeInfoCompleteness`），
 *    标签池下方常驻一行口径说明（感叹号是什么，见 mac-info__legend，与项目信息管理页一致）。
 * 2. 缺信息提示条 + 处理按钮：
 *    ① 补充信息 / ② 提单给他人补充 —— 选定项目后常驻（不再只在缺信息时出现）；
 *    ③ 暂时跳过 —— 点了本次（本组件实例）不再提示、按钮也不再显示，刷新/重进页面恢复；
 *    缺信息提示条本身仍只在「勾选标签里有缺省过半」时出现。
 * 3. 标签勾选每次打开都默认全不选（不做记忆），标签行上方提供全选 / 全部取消。
 * 4. 文档正文：系统段（项目背景信息，随勾选实时重算）+ 分隔线 + 补充段（用户自己写的不被覆盖），
 *    见 shared/utils/shareDoc.ts；编辑入口复用既有的 SpecDocField（上传 / 在线编写）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Popup, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { createTicket } from '@/api/ticket';
import type { ProblemDocSourceItem } from '@/api/specDoc';
import type { UserItem } from '@/api/users';
import UserSelect from '@/shared/components/UserSelect';
import AiProblemDocGenerator from '@/shared/components/AiProblemDocGenerator';
import SpecDocField, { type SpecDocDraft } from '@/shared/components/SpecDocField';
import ProjectInfoEditDrawer from '@/shared/components/ProjectInfoEditDrawer';
import { computeInfoCompleteness, loadInfoNodes, type ProjectInfoNode } from '@/shared/utils/projectInfoTree';
import {
  buildProjectBackgroundMarkdown,
  collectMissingInfoNodes,
  mergeShareDoc,
  missingSelectedTags,
  replaceUserSection,
  SHARE_DOC_SECTION_TEMPLATE,
  splitShareDoc,
} from '@/shared/utils/shareDoc';
import '@/shared/styles/shareDoc.css';

export interface TicketShareDocSettingProps {
  /** 草稿里选定的项目（project.id）；为空时本卡显示空态 */
  projectId: string;
  projectName?: string;
  /** 共享文档草稿（与 SpecDocField 同一份，提交时随 overrides.spec_doc 透传） */
  value: SpecDocDraft | null;
  onChange: (value: SpecDocDraft | null) => void;
  disabled?: boolean;
  /**
   * 「AI 生成问题文档」的素材（提单页传本次会话消息；讨论区场景传评论）。
   * 不传 / 为空时不显示生成入口。
   */
  sourceItems?: ProblemDocSourceItem[];
}

/** 项目成员行（GET /projects/{id}/members 的最小字段） */
interface ProjectMemberRow {
  user_id?: string | null;
}

/** 补充工单的正文（纯文本清单，提单人与补充人两边都能照着看） */
function buildSupplementDescription(
  projectName: string,
  missing: Array<{ path: string }>,
  editUrl: string,
): string {
  const list = missing.length
    ? missing.map((item, index) => `${index + 1}. ${item.path}`).join('\n')
    : '（暂无可列的节点，请到项目信息管理里核对）';
  return [
    '【项目信息补充】提单时发现该项目的信息有缺失，请帮忙补充，补齐后其他人提单即可自动带全背景信息。',
    '',
    `项目：${projectName}`,
    '',
    `待补充信息（${missing.length} 项）：`,
    list,
    '',
    `入口：项目详情 → 「编辑项目信息」${editUrl}`,
  ].join('\n');
}

/** 信息树的一级标签（根节点），按 sort_order 排好序 */
function rootNodes(list: ProjectInfoNode[]): ProjectInfoNode[] {
  return list
    .filter((node) => node.parent_id === null)
    .slice()
    .sort((a, b) => a.sort_order - b.sort_order);
}

export default function TicketShareDocSetting({
  projectId,
  projectName = '',
  value,
  onChange,
  disabled = false,
  sourceItems = [],
}: TicketShareDocSettingProps) {
  const request = useMemo(() => createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin'), []);

  const [nodes, setNodes] = useState<ProjectInfoNode[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [skipMissing, setSkipMissing] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [assignOpen, setAssignOpen] = useState(false);
  const [assignee, setAssignee] = useState<UserItem | null>(null);
  const [memberIds, setMemberIds] = useState<string[]>([]);
  const [sending, setSending] = useState(false);

  // 读项目信息树（切换项目时重读；失败静默为空态，不阻断提单主流程）
  useEffect(() => {
    if (!projectId) {
      setNodes([]);
      setSelected(new Set());
      return;
    }
    let cancelled = false;
    setLoading(true);
    loadInfoNodes(projectId)
      .then((list) => {
        if (cancelled) return;
        // 每次打开提单弹窗都从「全不选」开始（不做记忆）：和 setNodes 落在同一次提交里，
        // 保证标签首次渲染出来时勾选就是空的 —— 不留「树先渲染、初始化后跑」的窗口，
        // 否则用户（或 CI 用例）在这个窗口点「全选」会被随后的初始化清掉
        // （deploy-split gate 曾因这条竞态偶发变红，run #37595347855）。
        setSelected(new Set());
        setNodes(list);
      })
      .catch(() => {
        if (cancelled) return;
        setSelected(new Set());
        setNodes([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  const roots = useMemo(() => rootNodes(nodes), [nodes]);

  const toggleTag = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const selectAllTags = useCallback(() => {
    setSelected(new Set(roots.map((root) => root.id)));
  }, [roots]);

  const clearAllTags = useCallback(() => setSelected(new Set()), []);

  /** 系统段：勾选标签的项目背景信息（只写有值的节点） */
  const system = useMemo(
    () => (nodes.length ? buildProjectBackgroundMarkdown(projectName, nodes, selected) : ''),
    [nodes, projectName, selected],
  );

  /** 勾选标签里缺省过半的（提示条用：只提醒准备带入文档的那几个） */
  const missing = useMemo(() => missingSelectedTags(nodes, selected), [nodes, selected]);
  /** 全部一级标签的缺省过半判定（chips 角标常显，与是否勾选无关） */
  const completeness = useMemo(() => computeInfoCompleteness(nodes), [nodes]);

  // 系统段一变（改勾选 / 抽屉里补了信息）就重算整篇：补充段原样保留。
  // value / onChange 放 ref，effect 只依赖系统段，避免「写入 → 重渲染 → 再写入」的回环。
  const valueRef = useRef(value);
  valueRef.current = value;
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  /** 补充段是否已有内容（AI 生成前要提示「这部分会被替换」） */
  const hasUserContent = useMemo(
    () => splitShareDoc(value?.content ?? '').user.trim().length > 0,
    [value?.content],
  );

  /** AI 生成的结果：只替换分隔线以下的补充段，系统段（项目背景信息）原样保留 */
  const applyGenerated = useCallback((markdown: string) => {
    onChangeRef.current({
      content: replaceUserSection(valueRef.current?.content ?? '', markdown),
      source: valueRef.current?.source || 'ai_summary',
      source_files: valueRef.current?.source_files ?? [],
    });
  }, []);
  useEffect(() => {
    if (!system || !selected.size) return;
    const current = valueRef.current?.content ?? '';
    // 文档还是空的：补充段先铺一份结构化骨架（问题描述 / 前因后果 / 涉及人员），只铺这一次
    const base = current || SHARE_DOC_SECTION_TEMPLATE;
    const next = mergeShareDoc(base, system);
    if (next === current) return;
    onChangeRef.current({
      content: next,
      source: valueRef.current?.source ?? 'inline',
      source_files: valueRef.current?.source_files ?? [],
    });
  }, [system, selected]);

  /** 抽屉里改了信息树 → 用最新节点实时重算文档与缺失提醒 */
  const handleTreeChange = useCallback((list: ProjectInfoNode[]) => {
    if (list.length) setNodes(list);
  }, []);

  // 打开选人弹层时拉项目成员（置顶用）；非管理员可能无权限，失败就按全量列表展示
  useEffect(() => {
    if (!assignOpen || !projectId) return;
    let cancelled = false;
    request<ProjectMemberRow[]>(`/projects/${encodeURIComponent(projectId)}/members`)
      .then((list) => {
        if (cancelled) return;
        const ids = (Array.isArray(list) ? list : [])
          .map((row) => String(row?.user_id ?? ''))
          .filter(Boolean);
        setMemberIds([...new Set(ids)]);
      })
      .catch(() => {
        if (!cancelled) setMemberIds([]);
      });
    return () => {
      cancelled = true;
    };
  }, [assignOpen, projectId, request]);

  /** ② 提单给他人补充：建一张 support 工单，挂上待补充节点清单 */
  const sendSupplementTicket = async () => {
    if (!assignee) {
      Toast({ message: '请选择要请谁补充', theme: 'warning' });
      return;
    }
    const missingNodes = collectMissingInfoNodes(nodes, selected);
    const editUrl = `/app/admin/project-detail/${encodeURIComponent(projectId)}/edit`;
    setSending(true);
    try {
      await createTicket({
        title: `【信息补充】${projectName || projectId} 项目信息待补充`,
        description: buildSupplementDescription(projectName || projectId, missingNodes, editUrl),
        ticket_type: 'support',
        priority: 'medium',
        project_id: projectId,
        project_name: projectName,
        assigned_to: assignee.id,
        metadata_info: {
          info_supplement: {
            project_id: projectId,
            project_name: projectName,
            tags: [...selected],
            node_count: missingNodes.length,
            nodes: missingNodes.map((item) => ({ id: item.id, path: item.path, root: item.rootTitle })),
          },
        },
      });
      Toast({
        message: `已发送信息补充工单给 ${assignee.name || assignee.username}`,
        theme: 'success',
      });
      setAssignOpen(false);
      setAssignee(null);
      // 已经交给别人了，本次提单不再拦着提醒
      setSkipMissing(true);
    } catch (err) {
      Toast({
        message: err instanceof Error && err.message ? err.message : '发送失败，请稍后重试',
        theme: 'error',
      });
    } finally {
      setSending(false);
    }
  };

  const showWarn = !skipMissing && missing.length > 0 && !loading;
  /** 标签区渲染条件（选定项目且信息树已拿到）：操作按钮与全选/全部取消的常驻判据 */
  const showTags = Boolean(projectId) && !loading && roots.length > 0;

  return (
    <section className="share-doc">
      <h4 className="share-doc__title">问题共享文档设置</h4>
      <p className="share-doc__hint">勾选要带入文档的项目背景信息（来自所绑定项目的信息标签）。</p>

      {/* 全选 / 全部取消：标签默认全不选，给一键操作（版式对齐项目信息管理页的 poolhead） */}
      {showTags ? (
        <div className="mac-info__poolhead">
          <span className="mac-info__poolhead-label">问题标签</span>
          <div className="mac-info__poolhead-ops">
            <button type="button" className="mac-info__poolbtn" disabled={disabled} onClick={selectAllTags}>
              全选
            </button>
            <button type="button" className="mac-info__poolbtn" disabled={disabled} onClick={clearAllTags}>
              全部取消
            </button>
          </div>
        </div>
      ) : null}

      {!projectId ? (
        <p className="share-doc__empty">请先选择项目，再设置共享文档。</p>
      ) : loading ? (
        <p className="share-doc__empty">正在读取项目信息…</p>
      ) : roots.length === 0 ? (
        <p className="share-doc__empty">该项目还没有信息标签，可到「项目信息管理」里维护。</p>
      ) : (
        <div className="mac-tagpool share-doc__tags">
          {roots.map((root) => {
            const incomplete = completeness.get(root.id)?.mostlyEmpty ?? false;
            const active = selected.has(root.id);
            return (
              <button
                key={root.id}
                type="button"
                className={`mac-tagpool__chip${active ? ' is-active' : ''}`}
                aria-pressed={active}
                disabled={disabled}
                onClick={() => toggleTag(root.id)}
              >
                {root.title}
                {incomplete ? (
                  <span className="mac-tagpool__warn" aria-label="信息缺失">
                    !
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      )}

      {/* 感叹号口径说明：常驻一行图例（与项目信息管理页同一套说明，提醒用户 ! 的含义） */}
      {showTags ? (
        <p className="mac-info__legend">
          <span className="mac-info__legend-item">
            <span className="mac-tagpool__warn mac-tagpool__warn--static" aria-hidden="true">
              !
            </span>
            标签旁的 ! = 该标签下过半信息未填写，可能影响问题定位
          </span>
        </p>
      ) : null}

      {showWarn ? (
        <div className="share-doc__warn">
          <span className="share-doc__warn-icon" aria-hidden="true">
            !
          </span>
          <span>
            当前问题缺少有效信息，可能影响问题定位（{missing.map((item) => item.title).join('、')}）
          </span>
        </div>
      ) : null}

      {/* 处理按钮：补充信息 / 提单给他人补充 常驻；暂时跳过点了本次不再显示（刷新/重进页面恢复） */}
      {showTags ? (
        <div className="share-doc__actions">
          <button
            type="button"
            className="share-doc__btn share-doc__btn--primary"
            disabled={disabled}
            onClick={() => setDrawerOpen(true)}
          >
            补充信息
          </button>
          <button
            type="button"
            className="share-doc__btn"
            disabled={disabled}
            onClick={() => setAssignOpen(true)}
          >
            提单给他人补充
          </button>
          {!skipMissing ? (
            <button
              type="button"
              className="share-doc__btn share-doc__btn--ghost"
              disabled={disabled}
              onClick={() => setSkipMissing(true)}
            >
              暂时跳过
            </button>
          ) : null}
        </div>
      ) : null}

      <div className="share-doc__doc">
        <p className="share-doc__label">问题共享文档（选填）</p>
        {sourceItems.length > 0 ? (
          <div className="share-doc__actions">
            <AiProblemDocGenerator
              items={sourceItems}
              projectName={projectName}
              scene="conversation"
              hasUserContent={hasUserContent}
              disabled={disabled}
              onApply={applyGenerated}
            />
          </div>
        ) : null}
        <SpecDocField value={value} onChange={onChange} disabled={disabled} />
        <p className="share-doc__tip">
          项目背景信息随勾选自动更新；分隔线以下的补充内容不会被覆盖。
        </p>
      </div>

      {/* ① 补充信息：右侧侧滑抽屉直接编辑项目信息 */}
      <ProjectInfoEditDrawer
        visible={drawerOpen}
        projectId={projectId}
        projectName={projectName}
        onClose={() => setDrawerOpen(false)}
        onTreeChange={handleTreeChange}
      />

      {/* ② 提单给他人补充：选人（项目成员靠前）→ 建 support 工单 */}
      <Popup
        visible={assignOpen}
        onClose={() => setAssignOpen(false)}
        placement="bottom"
        showOverlay
        closeOnOverlayClick={false}
        style={{ zIndex: 13010 }}
      >
        <div className="share-doc__sheet">
          <h4 className="share-doc__sheet-title">提单给他人补充</h4>
          <p className="share-doc__sheet-desc">
            向知道该项目情况的同事发一张信息补充工单（支持项目内成员优先，可搜索全量人员）。
          </p>
          <p className="share-doc__sheet-desc">
            待补充 {collectMissingInfoNodes(nodes, selected).length} 项
          </p>
          <UserSelect
            value={assignee?.id ?? null}
            onChange={(user) => setAssignee(user)}
            placeholder="选择要请谁补充"
            title="选择补充人"
            pinUserIds={memberIds}
          />
          <div className="share-doc__sheet-actions">
            <button
              type="button"
              className="share-doc__btn"
              onClick={() => {
                setAssignOpen(false);
                setAssignee(null);
              }}
            >
              取消
            </button>
            <button
              type="button"
              className="share-doc__btn share-doc__btn--primary"
              disabled={sending || !assignee}
              onClick={() => void sendSupplementTicket()}
            >
              {sending ? '发送中…' : '发送补充工单'}
            </button>
          </div>
        </div>
      </Popup>
    </section>
  );
}
