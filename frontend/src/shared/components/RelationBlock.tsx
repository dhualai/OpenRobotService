/**
 * RelationBlock — 工单「关联工单」区块（纯 CSS 画布版）
 *
 * 手动树形布局 + absolute positioning + SVG 连接线
 *   - subtask:   父 → 子，灰色实线，子节点阶梯式向右下缩进
 *   - predecessor: 前置 → 主工单，红色虚线 + 箭头，前置在主工单正上方
 *   - duplicate:  双向紫色虚线
 *   - 折叠:      UP 折叠 predecessor + 全关联，DOWN 折叠 subtask + 全关联
 */
import React, { useState, useEffect, useMemo, useCallback, useRef, useLayoutEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Tag, Popup, Dialog, Input, Button } from 'tdesign-mobile-react';
import { DatePicker } from 'antd';
import dayjs from 'dayjs';
import { Link2, Plus, AlertTriangle, Copy } from 'lucide-react';
import AppButton from '@/shared/components/AppButton';
import UserSelect from '@/shared/components/UserSelect';
import type { UserItem } from '@/api/users';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import {
  listRelations,
  createRelation,
  deleteRelation,
  createTicket,
  getRelationTree,
  type RelationType,
  type BlockedErrorDetail,
  type RelationTreeNode,
  type RelationTreeResponse,
  type TicketType,
} from '@/api/ticket';
import { PRIORITY_DISPLAY_MAP, TICKET_TYPE_DISPLAY_MAP } from '@/shared/constants/ticket';

const request = createRequest(API_CONFIG.TASKS.BASE_URL, '工单服务');

const RELATION_TYPE_LABEL: Record<RelationType, string> = {
  predecessor: '前置工单',
  duplicate: '重复工单',
  subtask: '子任务',
};

const RELATION_TYPE_DESC: Record<RelationType, string> = {
  predecessor: '前置工单完成后才能完成/关闭当前工单',
  duplicate: '标记为重复工单，不阻塞流转',
  subtask: '父工单关闭前需所有子任务已完成',
};

/** 处理阶段截止时间快捷选项（天）：与 ChatPanel 确认工单弹窗保持一致 */
const STEP_QUICK_OPTIONS: { value: number; label: string }[] = [
  { value: 1, label: '1天' },
  { value: 3, label: '3天' },
  { value: 5, label: '5天' },
  { value: 7, label: '7天' },
  { value: 14, label: '14天' },
];

/** 未完成判定 */
function isUnfinished(status: string, type: RelationType): boolean {
  const s = status?.toLowerCase();
  if (type === 'predecessor') return s !== 'resolved' && s !== 'closed';
  if (type === 'subtask') return s !== 'resolved' && s !== 'closed' && s !== 'canceled';
  return false;
}

/** 状态徽章颜色 */
function statusColor(status: string): string {
  const s = status?.toLowerCase();
  if (s === 'resolved' || s === 'closed') return 'rgba(34, 197, 94, 0.12)';
  if (s === 'canceled') return 'rgba(107, 114, 128, 0.12)';
  if (s === 'in_progress') return 'rgba(59, 130, 246, 0.12)';
  return 'rgba(100, 116, 139, 0.12)';
}

function statusTextColor(status: string): string {
  const s = status?.toLowerCase();
  if (s === 'resolved' || s === 'closed') return '#22c55e';
  if (s === 'canceled') return '#6b7280';
  if (s === 'in_progress') return '#3b82f6';
  return '#64748b';
}

// ──────────────────────────────────────────────────────────────
// 常量
// ──────────────────────────────────────────────────────────────
const NODE_WIDTH = 200;
const NODE_HEIGHT = 56;
const INDENT = NODE_WIDTH + 90;   // 子节点与父节点水平间距
const ROW_GAP = 20;               // 兄弟节点垂直间距
const PRED_GAP = 24;              // 前置/重复与主工单的垂直间距
const MARGIN_X = 24;
const MARGIN_Y = 24;

// ──────────────────────────────────────────────────────────────
// 关系映射构建 + 折叠隐藏计算
// ──────────────────────────────────────────────────────────────

export interface RelationMaps {
  nodeMap: Map<number, RelationTreeNode>;
  subtaskChildren: Map<number, number[]>;
  subtaskParent: Map<number, number>;
  predecessorOf: Map<number, number[]>;
  predecessorDeps: Map<number, number[]>;
  duplicateWith: Map<number, number[]>;
}

export interface HiddenInfo {
  hiddenIds: Set<string>;
  hiddenCountMap: Map<string, number>;
}

function buildRelationMaps(tree: RelationTreeResponse): RelationMaps {
  const nodeMap = new Map<number, RelationTreeNode>();
  const subtaskChildren = new Map<number, number[]>();
  const subtaskParent = new Map<number, number>();
  const predecessorOf = new Map<number, number[]>();
  const predecessorDeps = new Map<number, number[]>();
  const duplicateWith = new Map<number, number[]>();

  for (const n of tree.nodes) nodeMap.set(n.id, n);

  for (const e of tree.edges) {
    if (e.relation_type === 'subtask') {
      if (!subtaskChildren.has(e.source)) subtaskChildren.set(e.source, []);
      subtaskChildren.get(e.source)!.push(e.target);
      subtaskParent.set(e.target, e.source);
    } else if (e.relation_type === 'predecessor') {
      if (!predecessorOf.has(e.source)) predecessorOf.set(e.source, []);
      predecessorOf.get(e.source)!.push(e.target);
      if (!predecessorDeps.has(e.target)) predecessorDeps.set(e.target, []);
      predecessorDeps.get(e.target)!.push(e.source);
    } else if (e.relation_type === 'duplicate') {
      if (!duplicateWith.has(e.source)) duplicateWith.set(e.source, []);
      duplicateWith.get(e.source)!.push(e.target);
      if (!duplicateWith.has(e.target)) duplicateWith.set(e.target, []);
      duplicateWith.get(e.target)!.push(e.source);
    }
  }

  for (const arr of subtaskChildren.values()) arr.sort((a, b) => a - b);
  for (const arr of predecessorOf.values()) arr.sort((a, b) => a - b);
  for (const arr of predecessorDeps.values()) arr.sort((a, b) => a - b);
  for (const arr of duplicateWith.values()) arr.sort((a, b) => a - b);

  return { nodeMap, subtaskChildren, subtaskParent, predecessorOf, predecessorDeps, duplicateWith };
}

function computeHidden(tree: RelationTreeResponse, collapsed: Set<string>): HiddenInfo {
  const maps = buildRelationMaps(tree);
  const hiddenIds = new Set<string>();
  const hiddenCountMap = new Map<string, number>();

  for (const key of collapsed) {
    const idx = key.indexOf(':');
    if (idx < 0) continue;
    const dir = key.slice(0, idx);
    const idStr = key.slice(idx + 1);
    if ((dir !== 'up' && dir !== 'down' && dir !== 'dup') || !idStr) continue;
    const id = Number(idStr);
    let count = 0;

    const visited = new Set<number>();
    function hideAll(nid: number) {
      if (visited.has(nid)) return;
      visited.add(nid);
      for (const k of maps.subtaskChildren.get(nid) || []) {
        if (!hiddenIds.has(String(k))) { count++; hiddenIds.add(String(k)); }
        hideAll(k);
      }
      for (const p of maps.predecessorOf.get(nid) || []) {
        if (!hiddenIds.has(String(p))) { count++; hiddenIds.add(String(p)); }
        hideAll(p);
      }
      for (const dup of maps.duplicateWith.get(nid) || []) {
        if (!hiddenIds.has(String(dup))) { count++; hiddenIds.add(String(dup)); }
        hideAll(dup);
      }
    }

    if (dir === 'up') {
      const preds = maps.predecessorOf.get(id) || [];
      for (const p of preds) {
        if (!hiddenIds.has(String(p))) { count++; hiddenIds.add(String(p)); }
        hideAll(p);
      }
    } else if (dir === 'down') {
      for (const k of maps.subtaskChildren.get(id) || []) {
        if (!hiddenIds.has(String(k))) { count++; hiddenIds.add(String(k)); }
        hideAll(k);
      }
    } else {  // dup — 只 hide duplicate 节点本身，不递归
      for (const dup of maps.duplicateWith.get(id) || []) {
        if (!hiddenIds.has(String(dup))) { count++; hiddenIds.add(String(dup)); }
      }
    }
    hiddenCountMap.set(`${dir}:${idStr}`, count);
  }

  return { hiddenIds, hiddenCountMap };
}

// ──────────────────────────────────────────────────────────────
// 手动树形布局（复用旧逻辑，输出坐标 + 画布尺寸）
// ──────────────────────────────────────────────────────────────

export interface LayoutNode {
  id: number;
  x: number;
  y: number;
}

export interface LayoutEdge {
  id: string;
  type: 'subtask' | 'predecessor' | 'duplicate';
  source: number;
  target: number;
}

export interface LayoutResult {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  width: number;
  height: number;
}

function computeLayout(
  tree: RelationTreeResponse,
  visibleIds: Set<string>,
): LayoutResult {
  const maps = buildRelationMaps(tree);
  const predEdges: Array<{ mainId: number; predId: number }> = [];
  for (const e of tree.edges) {
    if (e.relation_type === 'predecessor') {
      predEdges.push({ mainId: e.source, predId: e.target });
    }
  }

  // DFS subtask 树 — 同 Y 水平延伸，兄弟上下堆叠
  const positionMap = new Map<number, { x: number; y: number; depth: number }>();
  const placedIds = new Set<number>();

  function dfsSubtask(nodeId: number, depth: number, currentY: number) {
    const x = MARGIN_X + depth * INDENT;
    positionMap.set(nodeId, { x, y: currentY, depth });
    placedIds.add(nodeId);
    const kids = maps.subtaskChildren.get(nodeId) || [];
    let nextY = currentY;
    for (const kid of kids) {
      dfsSubtask(kid, depth + 1, nextY);
      nextY += NODE_HEIGHT + ROW_GAP;
    }
  }

  const rootId = tree.root_id;
  dfsSubtask(rootId, 0, MARGIN_Y);

  // 放置 predecessor — 先 push 兄弟间距，再同 X 列插在主工单正上方
  const PRED_VERTICAL = NODE_HEIGHT + PRED_GAP;
  // 收集每个 main 的前置数和重复数（duplicate 在 main 下方也会占空间，需要 push 后续兄弟）
  const dupCounts = new Map<number, number>();
  for (const e of tree.edges) {
    if (e.relation_type === 'duplicate') {
      // duplicate 的"已放置方"可能在后续决定，但先统计所有可能的 mainId
      // duplicate 是双向的，我们把两个端点都算一份（取较小 ID 做 key 避免重复）
      const key = Math.min(e.source, e.target);
      dupCounts.set(key, (dupCounts.get(key) || 0) + 1);
    }
  }
  const parentExtraPush = new Map<number, number>();
  for (const { mainId } of predEdges) {
    const mainPos = positionMap.get(mainId);
    if (!mainPos) continue;
    const parentOfMain = maps.subtaskParent.get(mainId);
    if (parentOfMain) {
      parentExtraPush.set(parentOfMain, (parentExtraPush.get(parentOfMain) || 0) + PRED_VERTICAL);
    }
  }
  // duplicate push: 每个 subtask 子节点如果有 duplicate，它的后续兄弟也要往下让空间
  for (const [parentId, kidIds] of maps.subtaskChildren) {
    let dupPush = 0;
    for (const kidId of kidIds) {
      const dc = dupCounts.get(kidId) || 0;
      if (dc > 0) dupPush += dc * PRED_VERTICAL;
    }
    if (dupPush > 0) {
      parentExtraPush.set(parentId, (parentExtraPush.get(parentId) || 0) + dupPush);
    }
  }

  for (const [parentId, totalPush] of parentExtraPush) {
    const sibIds = maps.subtaskChildren.get(parentId) || [];
    const sibPositions = sibIds
      .map(id => ({ id, pos: positionMap.get(id)! }))
      .filter(s => s.pos)
      .sort((a, b) => a.pos.y - b.pos.y);
    if (sibPositions.length === 0) continue;

    const mainIdsWithPred = new Set(
      predEdges.filter(e => maps.subtaskParent.get(e.mainId) === parentId).map(e => e.mainId)
    );

    // 计算每个 sibling 有多少 duplicate 边（放在 sibling 下方）
    const dupCountPerSib = new Map<number, number>();
    for (const e of tree.edges) {
      if (e.relation_type !== 'duplicate') continue;
      for (const sid of sibIds) {
        if (e.source === sid || e.target === sid) {
          dupCountPerSib.set(sid, (dupCountPerSib.get(sid) || 0) + 1);
        }
      }
    }

    const cumulativePush = new Array(sibPositions.length).fill(0);
    let accumulated = 0;
    for (let i = 0; i < sibPositions.length; i++) {
      const sid = sibPositions[i].id;
      // predecessor: main 上方，main 和后续兄弟都要往下让 → 先加再记
      if (mainIdsWithPred.has(sid)) {
        accumulated += PRED_VERTICAL;
      }
      cumulativePush[i] = accumulated;  // pred push 影响当前节点
      // duplicate: main 下方，只有后续兄弟让 → 记完当前再加
      const dc = dupCountPerSib.get(sid) || 0;
      if (dc > 0) {
        accumulated += dc * PRED_VERTICAL;
      }
    }

    // 递归 push 一个节点及其所有 subtask 后代
    function pushSubtree(nodeId: number, amount: number) {
      const pos = positionMap.get(nodeId);
      if (pos) pos.y += amount;
      for (const kid of maps.subtaskChildren.get(nodeId) || []) {
        pushSubtree(kid, amount);
      }
    }

    for (let i = 0; i < sibPositions.length; i++) {
      pushSubtree(sibPositions[i].id, cumulativePush[i]);
    }
  }

  // 放 P
  for (const { mainId, predId } of predEdges) {
    const mainPos = positionMap.get(mainId);
    if (mainPos) {
      const ax = mainPos.x;
      const ay = mainPos.y - NODE_HEIGHT - PRED_GAP;
      const depth = mainPos.depth;  // predecessor 继承 mainId 的 depth
      if (positionMap.has(predId)) {
        positionMap.get(predId)!.x = ax;
        positionMap.get(predId)!.y = ay;
        positionMap.get(predId)!.depth = depth;
      } else {
        positionMap.set(predId, { x: ax, y: ay, depth });
        placedIds.add(predId);
      }
    }
  }

  // 放 duplicate — 同 X 正下方，继承 depth
  const dupEdges: Array<{ aId: number; bId: number }> = [];
  const _dupSeen = new Set<string>();
  for (const e of tree.edges) {
    if (e.relation_type !== 'duplicate') continue;
    const key = [Math.min(e.source, e.target), Math.max(e.source, e.target)].join('-');
    if (_dupSeen.has(key)) continue;
    _dupSeen.add(key);
    dupEdges.push({ aId: e.source, bId: e.target });
  }
  const dupOffsetMap = new Map<number, number>();
  for (const { aId, bId } of dupEdges) {
    let placedId: number, unplacedId: number;
    const aPlaced = positionMap.has(aId);
    const bPlaced = positionMap.has(bId);
    if (aPlaced && !bPlaced) { placedId = aId; unplacedId = bId; }
    else if (bPlaced && !aPlaced) { placedId = bId; unplacedId = aId; }
    else if (aPlaced && bPlaced) continue;
    else { continue; }

    const srcPos = positionMap.get(placedId)!;
    const offset = (dupOffsetMap.get(placedId) || 0);
    // 第一个 dup: src.y + H + GAP；后续: prev.y + H + GAP
    const prevY = offset === 0
      ? srcPos.y
      : srcPos.y + offset * (NODE_HEIGHT + PRED_GAP);
    const dupY = prevY + NODE_HEIGHT + PRED_GAP;
    dupOffsetMap.set(placedId, offset + 1);
    positionMap.set(unplacedId, { x: srcPos.x, y: dupY, depth: srcPos.depth });
    placedIds.add(unplacedId);
  }

  // 对新放置的 predecessor 节点，递归展开它们的 subtask 子树
  function expandSubtree(nodeId: number, depth: number, y: number) {
    const x = MARGIN_X + depth * INDENT;
    const existing = positionMap.get(nodeId);
    if (existing) {
      // 已存在（predecessor 或孤立节点放的），修正 x/depth，保留 y
      existing.x = x;
      existing.depth = depth;
    } else {
      positionMap.set(nodeId, { x, y, depth });
      placedIds.add(nodeId);
    }
    const kids = maps.subtaskChildren.get(nodeId) || [];
    let nextY = existing ? existing.y : y;
    for (const kid of kids) {
      expandSubtree(kid, depth + 1, nextY);
      nextY += NODE_HEIGHT + ROW_GAP;
    }
  }
  // 收集所有还没 DFS 过的、有 subtask 子节点的节点
  const initialPlaced = new Set(positionMap.keys());
  for (const nodeId of initialPlaced) {
    const kids = maps.subtaskChildren.get(nodeId) || [];
    for (const kid of kids) {
      if (!positionMap.has(kid)) {
        const pos = positionMap.get(nodeId)!;
        expandSubtree(kid, pos.depth + 1, pos.y);
      }
    }
  }

  // 剩余孤立节点
  const allPlacedIds = new Set(positionMap.keys());
  for (const n of tree.nodes) {
    if (allPlacedIds.has(n.id)) continue;
    // 检查它是否有已定位的邻居
    let placed: { x: number; y: number } | null = null;
    for (const p of maps.predecessorOf.get(n.id) || []) {
      if (positionMap.has(p)) {
        const pp = positionMap.get(p)!;
        placed = { x: pp.x + NODE_WIDTH + PRED_GAP, y: pp.y };
        break;
      }
    }
    if (!placed) {
      for (const d of maps.predecessorDeps.get(n.id) || []) {
        if (positionMap.has(d)) {
          const dp = positionMap.get(d)!;
          placed = { x: dp.x, y: dp.y - NODE_HEIGHT - PRED_GAP };
          break;
        }
      }
    }
    if (placed) {
      positionMap.set(n.id, { x: placed.x, y: placed.y, depth: -1 });
    } else {
      // 完全孤立 — 放在最右侧
      let maxX = MARGIN_X;
      for (const pos of positionMap.values()) maxX = Math.max(maxX, pos.x + NODE_WIDTH);
      const yCounter = positionMap.size;
      positionMap.set(n.id, { x: maxX + 40, y: MARGIN_Y + yCounter * (NODE_HEIGHT + ROW_GAP), depth: -1 });
    }
  }

  // 计算画布尺寸（只算可见节点）
  let maxX = 0, maxY = 0;
  const visibleLayoutNodes: LayoutNode[] = [];
  for (const [id, pos] of positionMap) {
    if (!visibleIds.has(String(id))) continue;
    visibleLayoutNodes.push({ id, x: pos.x, y: pos.y });
    maxX = Math.max(maxX, pos.x + NODE_WIDTH);
    maxY = Math.max(maxY, pos.y + NODE_HEIGHT);
  }

  // 构建可见边
  const edges: LayoutEdge[] = [];
  const visibleSet = visibleIds;
  for (const e of tree.edges) {
    const sStr = String(e.source);
    const tStr = String(e.target);
    if (!visibleSet.has(sStr) || !visibleSet.has(tStr)) continue;
    let type: LayoutEdge['type'] = 'subtask';
    let id = '';
    if (e.relation_type === 'subtask') {
      type = 'subtask';
      id = `sub-${e.source}-${e.target}`;
    } else if (e.relation_type === 'predecessor') {
      type = 'predecessor';
      id = `pred-${e.source}-${e.target}`;
      // 翻转：React Flow 约定 source=前置(A), target=主工单(B)
      // 这样箭头从 pred(上) 向下指向 main(下)，方向正确
      edges.push({ id, type, source: e.target, target: e.source });
      continue;
    } else if (e.relation_type === 'duplicate') {
      type = 'duplicate';
      // 去重：已存在反向则跳过
      const exists = edges.some(ed =>
        ed.type === 'duplicate' &&
        ((ed.source === e.source && ed.target === e.target) ||
         (ed.source === e.target && ed.target === e.source))
      );
      if (exists) continue;
      id = `dup-${e.source}-${e.target}`;
    }
    edges.push({ id, type, source: e.source, target: e.target });
  }

  return {
    nodes: visibleLayoutNodes,
    edges,
    width: Math.max(maxX + MARGIN_X, NODE_WIDTH * 2),
    height: Math.max(maxY + MARGIN_Y, NODE_HEIGHT * 2),
  };
}

// ──────────────────────────────────────────────────────────────
// 节点卡片组件
// ──────────────────────────────────────────────────────────────

interface TaskCardProps {
  node: RelationTreeNode;
  x: number;
  y: number;
  isCurrent: boolean;
  blocked: boolean;
  duplicateCount: number;
  onNavigate: (id: number) => void;
  hasUp?: boolean;
  hasDown?: boolean;
  hasDup?: boolean;
  isUpCollapsed?: boolean;
  isDownCollapsed?: boolean;
  isDupCollapsed?: boolean;
  upHiddenCount?: number;
  downHiddenCount?: number;
  dupHiddenCount?: number;
  onToggleUp?: () => void;
  onToggleDown?: () => void;
  onToggleDup?: () => void;
}

function TaskCard({
  node, x, y, isCurrent, blocked, duplicateCount, onNavigate,
  hasUp, hasDown, hasDup, isUpCollapsed, isDownCollapsed, isDupCollapsed,
  upHiddenCount, downHiddenCount, dupHiddenCount, onToggleUp, onToggleDown, onToggleDup,
}: TaskCardProps) {
  const borderColor = blocked ? '#ef4444' : (isCurrent ? '#3b82f6' : '#e2e8f0');
  const bgColor = blocked ? 'rgba(239, 68, 68, 0.06)' : (isCurrent ? 'rgba(59, 130, 246, 0.04)' : 'white');

  return (
    <div
      onClick={() => onNavigate(node.id)}
      style={{
        position: 'absolute',
        left: x, top: y,
        width: NODE_WIDTH, height: NODE_HEIGHT,
        background: bgColor,
        border: `1.5px solid ${borderColor}`,
        borderRadius: 8,
        padding: '6px 10px',
        cursor: 'pointer',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        gap: 3,
        transition: 'box-shadow 0.15s',
        boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
        fontSize: 12,
        zIndex: 1,
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)'; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.boxShadow = '0 1px 3px rgba(0,0,0,0.06)'; }}
    >
      {/* 顶部行 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <span style={{ color: '#64748b', fontSize: 11, fontWeight: 500 }}>#{node.id}</span>
        {isCurrent && (
          <span style={{
            fontSize: 10, padding: '1px 6px', borderRadius: 999,
            background: '#3b82f6', color: 'white', fontWeight: 500,
          }}>当前</span>
        )}
        {blocked && <AlertTriangle size={11} color="#ef4444" />}
        {duplicateCount > 0 && (
          <span style={{
            display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
            width: 16, height: 16, borderRadius: 3,
            background: 'rgba(168, 85, 247, 0.12)', color: '#a855f7',
          }} title={`重复工单: ${duplicateCount}`}>
            <Copy size={10} />
          </span>
        )}
      </div>

      {/* 底部行 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{
          flex: 1, fontSize: 12, fontWeight: isCurrent ? 600 : 400,
          color: blocked ? '#ef4444' : '#1e293b',
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {node.title}
        </span>
        <span style={{
          fontSize: 10, padding: '0 5px', borderRadius: 999,
          background: statusColor(node.status), color: statusTextColor(node.status),
          fontWeight: 500, flexShrink: 0,
        }}>
          {node.status}
        </span>
      </div>

      {/* UP 按钮（前置方向，顶部） */}
      {hasUp && (
        <button
          onClick={(e) => { e.stopPropagation(); e.preventDefault(); onToggleUp?.(); }}
          title={isUpCollapsed ? `展开 ${upHiddenCount ?? 0} 个前置工单` : `折叠前置工单`}
          style={{
            position: 'absolute', top: -8, left: '50%', transform: 'translateX(-50%)',
            width: 16, height: 16, borderRadius: '50%',
            border: '1.5px solid #ef4444',
            background: isUpCollapsed ? '#fee2e2' : 'white',
            color: '#ef4444', fontSize: 9, fontWeight: 700,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            cursor: 'pointer', padding: 0,
            boxShadow: '0 1px 2px rgba(0,0,0,0.1)', zIndex: 5,
          }}
        >
          {isUpCollapsed ? (upHiddenCount ?? '−') : '−'}
        </button>
      )}

      {/* DOWN 按钮（subtask 方向，右侧） */}
      {hasDown && (
        <button
          onClick={(e) => { e.stopPropagation(); e.preventDefault(); onToggleDown?.(); }}
          title={isDownCollapsed ? `展开 ${downHiddenCount ?? 0} 个子任务` : `折叠子任务`}
          style={{
            position: 'absolute', right: -8, top: '50%', transform: 'translateY(-50%)',
            width: 16, height: 16, borderRadius: '50%',
            border: '1.5px solid #3b82f6',
            background: isDownCollapsed ? '#dbeafe' : 'white',
            color: '#3b82f6', fontSize: 9, fontWeight: 700,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            cursor: 'pointer', padding: 0,
            boxShadow: '0 1px 2px rgba(0,0,0,0.1)', zIndex: 5,
          }}
        >
          {isDownCollapsed ? (downHiddenCount ?? '−') : '−'}
        </button>
      )}

      {/* DUP 按钮（duplicate 方向，底部） */}
      {hasDup && (
        <button
          onClick={(e) => { e.stopPropagation(); e.preventDefault(); onToggleDup?.(); }}
          title={isDupCollapsed ? `展开 ${dupHiddenCount ?? 0} 个重复工单` : `折叠重复工单`}
          style={{
            position: 'absolute', bottom: -8, left: '50%', transform: 'translateX(-50%)',
            width: 16, height: 16, borderRadius: '50%',
            border: '1.5px solid #a855f7',
            background: isDupCollapsed ? '#f3e8ff' : 'white',
            color: '#a855f7', fontSize: 9, fontWeight: 700,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            cursor: 'pointer', padding: 0,
            boxShadow: '0 1px 2px rgba(0,0,0,0.1)', zIndex: 5,
          }}
        >
          {isDupCollapsed ? (dupHiddenCount ?? '−') : '−'}
        </button>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────
// 连接线 SVG
// ──────────────────────────────────────────────────────────────

function EdgeSvg({ layout }: { layout: LayoutResult }) {
  const src = (id: number) => layout.nodes.find(n => n.id === id);

  const markers: React.ReactNode[] = [
    <marker key="g" id="arr-g" viewBox="0 0 10 10" refX="10" refY="5"
            markerWidth="8" markerHeight="8" orient="auto">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
    </marker>,
    <marker key="r" id="arr-r" viewBox="0 0 10 10" refX="10" refY="5"
            markerWidth="8" markerHeight="8" orient="auto">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#ef4444" />
    </marker>,
    <marker key="p" id="arr-p" viewBox="0 0 10 10" refX="10" refY="5"
            markerWidth="7" markerHeight="7" orient="auto">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#a855f7" />
    </marker>,
    // 反向 markerStart 用（duplicate 双向）
    <marker key="p-r" id="arr-p-r" viewBox="0 0 10 10" refX="0" refY="5"
            markerWidth="7" markerHeight="7" orient="auto">
      <path d="M 10 0 L 0 5 L 10 10 z" fill="#a855f7" />
    </marker>,
  ];

  const paths: React.ReactNode[] = [];
  const labels: React.ReactNode[] = [];

  for (const edge of layout.edges) {
    const s = src(edge.source);
    const t = src(edge.target);
    if (!s || !t) continue;

    let d = '';
    let stroke = '#94a3b8';
    let dashes: string | undefined;
    let mEnd: string | undefined = 'url(#arr-g)';
    let mStart: string | undefined;
    let label = '';
    let labelColor = '#64748b';
    let midX = 0, midY = 0;

    if (edge.type === 'subtask') {
      const x1 = s.x + NODE_WIDTH;
      const y1 = s.y + NODE_HEIGHT / 2;
      const x2 = t.x;
      const y2 = t.y + NODE_HEIGHT / 2;
      midX = (x1 + x2) / 2;
      midY = y1;  // 折线中点在水平段
      d = `M ${x1} ${y1} L ${midX} ${y1} L ${midX} ${y2} L ${x2} ${y2}`;
      label = '子任务';
      labelColor = '#64748b';
    } else if (edge.type === 'predecessor') {
      const x1 = s.x + NODE_WIDTH / 2;
      const y1 = s.y + NODE_HEIGHT;
      const x2 = t.x + NODE_WIDTH / 2;
      const y2 = t.y;
      midX = (x1 + x2) / 2;
      midY = (y1 + y2) / 2;
      d = `M ${x1} ${y1} L ${x2} ${y2}`;
      stroke = '#ef4444';
      dashes = '4 2';
      mEnd = 'url(#arr-r)';
      label = '前置';
      labelColor = '#ef4444';
    } else {
      const cx1 = s.x + NODE_WIDTH / 2, cy1 = s.y + NODE_HEIGHT / 2;
      const cx2 = t.x + NODE_WIDTH / 2, cy2 = t.y + NODE_HEIGHT / 2;
      const dx = cx2 - cx1, dy = cy2 - cy1;
      const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
      const ux = dx / dist, uy = dy / dist;
      const ex1 = cx1 + ux * NODE_WIDTH / 2;
      const ey1 = cy1 + uy * NODE_HEIGHT / 2;
      const ex2 = cx2 - ux * NODE_WIDTH / 2;
      const ey2 = cy2 - uy * NODE_HEIGHT / 2;
      midX = (ex1 + ex2) / 2;
      midY = (ey1 + ey2) / 2;
      d = `M ${ex1} ${ey1} L ${ex2} ${ey2}`;
      stroke = '#a855f7';
      dashes = '3 3';
      mEnd = 'url(#arr-p)';
      mStart = 'url(#arr-p-r)';
      label = '重复';
      labelColor = '#a855f7';
    }

    paths.push(
      <path key={`p-${edge.id}`} d={d} fill="none" stroke={stroke} strokeWidth={1.5}
            strokeDasharray={dashes} markerEnd={mEnd} markerStart={mStart} />
    );

    // label: 带白底的文字，稍微偏离线一点
    if (label) {
      labels.push(
        <g key={`l-${edge.id}`}>
          <rect
            x={midX - 18} y={midY - 9}
            width={36} height={16} rx={4}
            fill="white" stroke="transparent"
          />
          <text
            x={midX} y={midY + 3}
            textAnchor="middle"
            fontSize={10}
            fontWeight={500}
            fill={labelColor}
            style={{ pointerEvents: 'none' }}
          >
            {label}
          </text>
        </g>
      );
    }
  }

  return (
    <svg style={{ position: 'absolute', inset: 0, width: layout.width, height: layout.height,
                   pointerEvents: 'none', zIndex: 0 }}>
      <defs>{markers}</defs>
      {paths}
      {labels}
    </svg>
  );
}

// ──────────────────────────────────────────────────────────────
// RelationBlock 主组件
// ──────────────────────────────────────────────────────────────

export interface RelationBlockProps {
  taskId: number;
  projectName?: string;
  projectId?: string;
  customer?: string;
  /** 父工单 ticket_type — 子任务继承 */
  ticketType?: string;
  /** 父工单 priority — 子任务默认继承 */
  parentPriority?: string;
  /** 父工单 deadline_at (ISO) — 子任务默认继承 */
  parentDeadlineAt?: string | null;
  canOperate: boolean;
  blockedError?: BlockedErrorDetail | null;
}

export default function RelationBlock({
  taskId, projectName, projectId, customer, ticketType, parentPriority, parentDeadlineAt, canOperate, blockedError,
}: RelationBlockProps) {
  const navigate = useNavigate();

  const [tree, setTree] = useState<RelationTreeResponse | null>(null);
  const [directRelations, setDirectRelations] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [showCreateSubtask, setShowCreateSubtask] = useState(false);
  const [addingType, setAddingType] = useState<RelationType>('predecessor');
  const [searchKeyword, setSearchKeyword] = useState('');
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [searching, setSearching] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [emptyHint, setEmptyHint] = useState<string>('');
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputWrapRef = useRef<HTMLDivElement | null>(null);
  const [flipUp, setFlipUp] = useState(false);

  // 下拉空间检测：下方不够 240px 就向上翻
  useLayoutEffect(() => {
    if (!showDropdown) { setFlipUp(false); return; }
    const el = inputWrapRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    setFlipUp(spaceBelow < 240);
  }, [showDropdown, searchResults.length, searching, emptyHint]);
  const [creatingSubtaskTitle, setCreatingSubtaskTitle] = useState('');
  const [creatingSubtaskDesc, setCreatingSubtaskDesc] = useState('');
  /** 子工单类型，默认继承父工单，可改 */
  const [creatingSubtaskTicketType, setCreatingSubtaskTicketType] = useState<string>(ticketType || 'problem');
  const [creatingSubtaskPriority, setCreatingSubtaskPriority] = useState<string>(parentPriority || 'medium');
  /** 指定处理人（选填）：选了直接派，没选走 AI 派单 */
  const [creatingSubtaskAssignee, setCreatingSubtaskAssignee] = useState<UserItem | null>(null);
  /** 整体截止时间（deadline_at，继承父工单，独立于阶段截止时间） */
  const [creatingSubtaskDeadline, setCreatingSubtaskDeadline] = useState<string | null>(parentDeadlineAt ?? null);
  /** 阶段截止时间（curr_step_endtime，独立管理，默认 +7 天） */
  const [creatingSubtaskStepEndtime, setCreatingSubtaskStepEndtime] = useState<string>(dayjs().add(7, 'day').second(0).millisecond(0).toISOString());
  const [subtaskStepTemplate, setSubtaskStepTemplate] = useState<Array<{ id: number; step_name: string; sequence: number }>>([]);
  const [creatingSubtaskStepId, setCreatingSubtaskStepId] = useState<number | null>(null);
  const [stepsLoading, setStepsLoading] = useState(false);
  const [creatingSubtaskLoading, setCreatingSubtaskLoading] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [showCanvas, setShowCanvas] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [treeData, relData] = await Promise.all([
        getRelationTree(taskId),
        listRelations(taskId),
      ]);
      setTree(treeData);
      setDirectRelations(relData || []);
    } catch (e) {
      console.error('加载关联数据失败', e);
    } finally {
      setLoading(false);
    }
  }, [taskId]);

  useEffect(() => { loadData(); }, [loadData]);

  const { maps, hiddenInfo, layout } = useMemo(() => {
    if (!tree) {
      return {
        maps: null as RelationMaps | null,
        hiddenInfo: { hiddenIds: new Set<string>(), hiddenCountMap: new Map() },
        layout: null as LayoutResult | null,
      };
    }
    const m = buildRelationMaps(tree);
    const h = computeHidden(tree, collapsed);
    const l = computeLayout(tree, new Set(tree.nodes.filter(n => !h.hiddenIds.has(String(n.id))).map(n => String(n.id))));
    return { maps: m, hiddenInfo: h, layout: l };
  }, [tree, collapsed]);

  // 按工单类型拉 TaskStep 模板：
  //   GET /api/tasks/{taskId}/steps?type={ticketType}
  //   后端 /steps 接口已支持可选 type 查询参数，传入时优先按该 type 查，
  //   不传 type 则按 taskId 反查父工单 ticket_type（旧行为，向下兼容）。
  const loadSubtaskSteps = useCallback(async (type: string) => {
    if (!type) return;
    setStepsLoading(true);
    try {
      const res = await request<{ code: number; data: { steps: Array<{ id: number; step_name: string; sequence: number }> } }>(`/${taskId}/steps?type=${encodeURIComponent(type)}`);
      const steps = (res?.data?.steps || []).slice().sort((a, b) => a.sequence - b.sequence);
      setSubtaskStepTemplate(steps);
      setCreatingSubtaskStepId(steps.length > 0 ? steps[0].id : null);
    } catch {
      setSubtaskStepTemplate([]);
      setCreatingSubtaskStepId(null);
    } finally {
      setStepsLoading(false);
    }
  }, [taskId]);

  // 子任务弹窗打开时：重置表单默认值 + 拉当前 ticketType 的 TaskStep 模板
  useEffect(() => {
    if (!showCreateSubtask) return;
    // 每次打开都从父工单默认值重置（避免上次编辑残留）
    setCreatingSubtaskTitle('');
    setCreatingSubtaskDesc('');
    setCreatingSubtaskTicketType(ticketType || 'problem');
    setCreatingSubtaskPriority(parentPriority || 'medium');
    setCreatingSubtaskAssignee(null);
    setCreatingSubtaskDeadline(parentDeadlineAt ?? null);
    setCreatingSubtaskStepEndtime(dayjs().add(7, 'day').second(0).millisecond(0).toISOString());
    setCreatingSubtaskStepId(null);
  }, [showCreateSubtask, ticketType, parentPriority, parentDeadlineAt]);

  // ticketType 变更（首次打开重置 or 用户手动切换）→ 重拉该类型的处理阶段
  useEffect(() => {
    if (!showCreateSubtask) return;
    void loadSubtaskSteps(creatingSubtaskTicketType);
  }, [showCreateSubtask, creatingSubtaskTicketType, loadSubtaskSteps]);

  /** 阶段截止时间 dayjs 值，供 antd DatePicker 使用 */
  const subtaskStepEndtimeValue = useMemo(() => {
    if (!creatingSubtaskStepEndtime) return null;
    const d = dayjs(creatingSubtaskStepEndtime);
    return d.isValid() ? d : null;
  }, [creatingSubtaskStepEndtime]);

  const handleDeleteRelation = async (relationId: number) => {
    Dialog.confirm!({
      title: '确认删除',
      content: '确定删除该关联吗？',
      confirmBtn: { content: '删除', theme: 'danger' },
      onConfirm: async () => {
        try {
          await deleteRelation(taskId, relationId);
          Dialog.alert!({ content: '删除成功' });
          loadData();
        } catch (e: any) {
          Dialog.alert!({ content: `删除失败: ${e?.message || ''}` });
        }
      },
    });
  };

  // 自动搜索（debounce 300ms）
  // 规则：#数字 或 纯数字 → 精确 ID 检索；其他 → 标题/描述模糊搜索
  const handleKeywordChange = (v: string) => {
    setSearchKeyword(v);
    if (searchTimer.current) clearTimeout(searchTimer.current);
    if (!v.trim()) { setSearchResults([]); setShowDropdown(false); setEmptyHint(''); return; }
    const kw = v.trim();
    // 判断是否 ID 检索：必须 #数字 前缀
    const idMatch = kw.match(/^#(\d+)$/);
    const isIdLookup = !!idMatch;
    searchTimer.current = setTimeout(async () => {
      setSearching(true);
      setEmptyHint('');
      try {
        if (isIdLookup) {
          const id = parseInt(idMatch![1], 10);
          try {
            const data: any = await request(`/${id}`);
            if (data && data.id && data.id !== Number(taskId)) {
              setSearchResults([{ id: data.id, title: data.title, status: data.status }]);
              setEmptyHint('');
            } else {
              setSearchResults([]);
              setEmptyHint(`工单 #${id} 不存在${data?.id === Number(taskId) ? '（不能关联自己）' : ''}`);
            }
          } catch {
            setSearchResults([]);
            setEmptyHint(`工单 #${id} 不存在`);
          }
        } else {
          const data: any = await request(`?keyword=${encodeURIComponent(kw)}&size=20`);
          const items = (data.items || []).filter((t: any) => t.id !== Number(taskId));
          if (items.length > 0) {
            setSearchResults(items.map((t: any) => ({ id: t.id, title: t.title, status: t.status })));
            setEmptyHint('');
          } else {
            setSearchResults([]);
            setEmptyHint(`没有找到包含 "${kw}" 的工单`);
          }
        }
        setShowDropdown(true);
      } catch (e) {
        console.error('搜索失败', e);
        setSearchResults([]);
        setEmptyHint('搜索失败，请稍后重试');
      } finally {
        setSearching(false);
      }
    }, 300);
  };

  const handleSelectSearchResult = async (peerId: number) => {
    try {
      await createRelation(taskId, peerId, addingType);
      Dialog.alert!({ content: '关联创建成功' });
      setShowAdd(false); setSearchKeyword(''); setSearchResults([]);
      loadData();
    } catch (e: any) {
      Dialog.alert!({ content: `创建失败: ${e?.detail || e?.message || ''}` });
    }
  };

  const handleCreateSubtask = async () => {
    if (!creatingSubtaskTitle.trim()) {
      Dialog.alert!({ content: '请填写标题' }); return;
    }
    if (!creatingSubtaskStepId) {
      Dialog.alert!({ content: '请选择处理阶段' }); return;
    }
    if (!creatingSubtaskStepEndtime) {
      Dialog.alert!({ content: '请选择当前阶段截止时间' }); return;
    }
    setCreatingSubtaskLoading(true);
    try {
      const newTicket = await createTicket({
        title: creatingSubtaskTitle.trim(),
        description: creatingSubtaskDesc.trim() || '（自动创建子任务）',
        ticket_type: (creatingSubtaskTicketType || ticketType || 'problem') as TicketType,
        priority: (creatingSubtaskPriority as any) || 'medium',
        // 指定处理人：选了直接派给此人；没选则不传 → 后端创建 status=NEW → AI 派单 Worker 重新派单
        assigned_to: creatingSubtaskAssignee?.id ?? undefined,
        deadline_at: creatingSubtaskDeadline || undefined,
        curr_step_id: creatingSubtaskStepId,
        curr_step_endtime: creatingSubtaskStepEndtime,
        project_name: projectName, project_id: projectId, customer: customer,
      });
      try {
        await createRelation(taskId, newTicket.id, 'subtask');
      } catch (relErr: any) {
        Dialog.alert!({
          content: `子任务已创建（#${newTicket.id}），但自动关联失败：${relErr?.detail || relErr?.message || ''}。请手动关联。`,
        });
      }
      // 关闭弹窗，reset 交给 useEffect 下次打开时处理
      setShowCreateSubtask(false);
      loadData();
    } catch (e: any) {
      Dialog.alert!({ content: `创建子任务失败: ${e?.detail || e?.message || ''}` });
    } finally { setCreatingSubtaskLoading(false); }
  };

  const toggleCollapsed = (id: string, direction: 'up' | 'down' | 'dup') => {
    setCollapsed(prev => {
      const key = `${direction}:${id}`;
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  const predecessorCount = directRelations.filter(r => r.relation_type === 'predecessor').length;
  const subtaskCount = directRelations.filter(r => r.relation_type === 'subtask').length;
  const duplicateCount = directRelations.filter(r => r.relation_type === 'duplicate').length;
  const totalRelations = predecessorCount + subtaskCount + duplicateCount;

  // Jira 风格方向标签
  const getDirectionLabel = (rel: any): string => {
    const isSource = rel.source_task_id === taskId;
    switch (rel.relation_type) {
      case 'predecessor':
        return isSource ? '前置' : '被前置';
      case 'subtask':
        return isSource ? '子任务' : '父工单';
      case 'duplicate':
        return '重复';
      default:
        return rel.relation_type;
    }
  };

  const getRelationBrief = (rel: any) => {
    // 返回"对方"的 brief（不是当前工单）
    return rel.source_task_id === taskId ? rel.target : rel.source;
  };

  // 按方向标签分组
  const groupedRelations = () => {
    const groups = new Map<string, any[]>();
    for (const rel of directRelations) {
      const label = getDirectionLabel(rel);
      if (!groups.has(label)) groups.set(label, []);
      groups.get(label)!.push(rel);
    }
    return groups;
  };

  const formatGroupTitle = (label: string, count: number): string => {
    return `【${label}】(${count})`;
  };

  // 状态颜色
  const statusColor = (status: string) => {
    const map: Record<string, string> = {
      'todo': '#94a3b8', 'in_progress': '#3b82f6', 'done': '#22c55e',
      'blocked': '#ef4444', 'closed': '#64748b', 'cancelled': '#94a3b8',
      'open': '#3b82f6', 'pending': '#f59e0b', 'new': '#94a3b8',
    };
    return map[status?.toLowerCase()] || '#64748b';
  };

  const statusLabel = (status: string) => {
    const map: Record<string, string> = {
      'todo': '待办', 'in_progress': '进行中', 'done': '已完成',
      'blocked': '阻塞', 'closed': '已关闭', 'cancelled': '已取消',
      'open': '打开', 'pending': '挂起', 'new': '新建',
    };
    return map[status?.toLowerCase()] || status;
  };

  // Jira 风格列表渲染（详情页外部卡片）
  const renderRelationList = () => {
    if (loading) return null;
    if (directRelations.length === 0) return null;

    const groups = groupedRelations();
    const groupOrder = ['被前置', '前置', '子任务', '父工单', '重复'];

    return (
      <div style={{ marginTop: 8 }}>
        {groupOrder
          .filter(label => groups.has(label))
          .map(label => {
            const rels = groups.get(label)!;
            return (
              <div key={label} style={{ marginBottom: 6 }}>
                {/* 关系类型标签 */}
                <div style={{
                  fontSize: 11, fontWeight: 600, color: '#475569',
                  marginBottom: 2,
                }}>
                  {formatGroupTitle(label, rels.length)}
                </div>

                {/* 工单卡片列表 */}
                {rels.map((rel, idx) => {
                  const brief = getRelationBrief(rel);
                  if (!brief) return null;
                  const isBlocked = rel.relation_type === 'predecessor'
                    && rel.source_task_id === taskId
                    && !['done', 'closed'].includes(String(brief.status).toLowerCase());
                  return (
                    <div
                      key={rel.id}
                      onClick={() => navigate(`/tasks/${brief.id}`)}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 6,
                        padding: '3px 10px', marginBottom: idx < rels.length - 1 ? 1 : 0,
                        borderRadius: 4, cursor: 'pointer',
                        background: isBlocked ? 'rgba(239, 68, 68, 0.06)' : 'transparent',
                        border: 'none',
                        transition: 'background 0.15s',
                      }}
                      onMouseEnter={(e) => {
                        (e.currentTarget as HTMLDivElement).style.background = isBlocked
                          ? 'rgba(239, 68, 68, 0.12)'
                          : 'rgba(0,0,0,0.04)';
                      }}
                      onMouseLeave={(e) => {
                        (e.currentTarget as HTMLDivElement).style.background = isBlocked
                          ? 'rgba(239, 68, 68, 0.06)'
                          : 'transparent';
                      }}
                    >
                      {/* tree 符号前缀 */}
                      <span style={{
                        fontSize: 12, color: '#94a3b8', fontFamily: 'ui-monospace, monospace',
                        flexShrink: 0, lineHeight: 1,
                      }}>
                        {idx < rels.length - 1 ? '├' : '└'}
                      </span>

                      {/* ID */}
                      <span style={{
                        fontSize: 12, fontWeight: 600, color: '#1e40af',
                        fontFamily: 'ui-monospace, monospace',
                      }}>
                        #{brief.id}
                      </span>

                      {/* 标题 */}
                      <span style={{
                        flex: 1, fontSize: 13, color: 'var(--foreground)',
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      }}>
                        {brief.title}
                      </span>

                      {/* 状态 */}
                      <span style={{
                        fontSize: 11, padding: '2px 8px', borderRadius: 4,
                        background: statusColor(brief.status) + '1a',
                        color: statusColor(brief.status), fontWeight: 500,
                        flexShrink: 0,
                      }}>
                        {statusLabel(brief.status)}
                      </span>
                    </div>
                  );
                })}
              </div>
            );
          })}
      </div>
    );
  };

  // 画布渲染（给弹窗用）
  const renderCanvas = () => {
    if (!layout || !maps || layout.nodes.length === 0) {
      return (
        <div style={{ padding: '48px 24px', textAlign: 'center', color: '#64748b', fontSize: 13 }}>
          暂无关联工单
        </div>
      );
    }
    return (
      <div style={{
        position: 'relative',
        width: layout.width,
        height: layout.height,
        minHeight: 300,
      }}>
        <EdgeSvg layout={layout} />
        {layout.nodes.map(ln => {
          const node = maps.nodeMap.get(ln.id);
          if (!node) return null;
          const preds = maps.predecessorOf.get(ln.id) || [];
          const kids = maps.subtaskChildren.get(ln.id) || [];
          const deps = maps.predecessorDeps.get(ln.id) || [];
          const dups = maps.duplicateWith.get(ln.id) || [];
          const predBlocked = preds.some(pid => {
            const p = maps.nodeMap.get(pid);
            return p && isUnfinished(p.status, 'predecessor');
          });
          const subBlocked = kids.some(cid => {
            const c = maps.nodeMap.get(cid);
            return c && isUnfinished(c.status, 'subtask');
          });
          const isUpCollapsed = collapsed.has(`up:${ln.id}`);
          const isDownCollapsed = collapsed.has(`down:${ln.id}`);
          const isDupCollapsed = collapsed.has(`dup:${ln.id}`);
          const upHiddenCount = hiddenInfo.hiddenCountMap.get(`up:${ln.id}`) ?? 0;
          const downHiddenCount = hiddenInfo.hiddenCountMap.get(`down:${ln.id}`) ?? 0;
          const dupHiddenCount = hiddenInfo.hiddenCountMap.get(`dup:${ln.id}`) ?? 0;

          return (
            <TaskCard
              key={ln.id}
              node={node}
              x={ln.x}
              y={ln.y}
              isCurrent={ln.id === taskId}
              blocked={predBlocked || subBlocked}
              duplicateCount={dups.length}
              onNavigate={(id) => navigate(`/tasks/${id}`)}
              hasUp={preds.length > 0}
              hasDown={kids.length > 0 || deps.length > 0}
              // DUP 按钮只在 anchor 节点显示（有其他关系或为 root）
              // 纯 duplicate 叶子节点（只有 dup 关系）不显示按钮
              hasDup={dups.length > 0 && (
                kids.length > 0 || deps.length > 0 || preds.length > 0 || ln.id === tree?.root_id
              )}
              isUpCollapsed={isUpCollapsed}
              isDownCollapsed={isDownCollapsed}
              isDupCollapsed={isDupCollapsed}
              upHiddenCount={upHiddenCount}
              downHiddenCount={downHiddenCount}
              dupHiddenCount={dupHiddenCount}
              onToggleUp={() => toggleCollapsed(String(ln.id), 'up')}
              onToggleDown={() => toggleCollapsed(String(ln.id), 'down')}
              onToggleDup={() => toggleCollapsed(String(ln.id), 'dup')}
            />
          );
        })}
      </div>
    );
  };

  return (
    <div className="detail-card" style={{ marginTop: 12 }}>
      {/* 标题行 */}
      <h4
        className="detail-card__h"
        onClick={() => setShowCanvas(true)}
        style={{
          cursor: 'pointer',
          display: 'flex', alignItems: 'center', gap: 6,
          padding: '8px 4px', borderRadius: 6, transition: 'background 0.15s', margin: 0,
          whiteSpace: 'nowrap',
        }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'var(--secondary, rgba(0,0,0,0.04))'; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'transparent'; }}
      >
        <Link2 size={16} strokeWidth={2} />
        关联工单
        {totalRelations > 0 && (
          <span style={{ fontSize: 12, color: 'var(--muted-foreground)', fontWeight: 400 }}>
            （{totalRelations}）
          </span>
        )}
        {blockedError && blockedError.blocked.length > 0 && (
          <span style={{ fontSize: 11, color: '#ef4444', display: 'inline-flex', alignItems: 'center', gap: 2 }}>
            <AlertTriangle size={11} /> 阻塞
          </span>
        )}
        <span className="ticket-dynamics-card__more" style={{ marginLeft: 'auto' }}>查看全部 ›</span>
      </h4>

      {/* Jira 风格直接关系列表 */}
      {renderRelationList()}

      {/* 弹窗：完整画布 */}
      {showCanvas && (
        <Popup
          visible={showCanvas}
          onVisibleChange={(v) => setShowCanvas(v)}
          placement="bottom"
          style={{ maxHeight: '85vh', borderRadius: '16px 16px 0 0', display: 'flex', flexDirection: 'column' }}
        >
        <div style={{ padding: 16, borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <Link2 size={18} strokeWidth={2} />
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600 }}>关联工单</h3>
            {totalRelations > 0 && (
              <span style={{ fontSize: 12, color: 'var(--muted-foreground)' }}>
                {predecessorCount} 前置 · {subtaskCount} 子任务 · {duplicateCount} 重复
              </span>
            )}
          </div>

          {/* 操作按钮：一左一右 */}
          {canOperate && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, marginTop: 10 }}>
              <AppButton size="small" theme="primary" onClick={() => { setShowCanvas(false); setShowAdd(true); }} style={{ flex: 1 }}>
                <Plus size={14} style={{ marginRight: 2 }} />关联
              </AppButton>
              <AppButton size="small" theme="default" onClick={() => { setShowCanvas(false); setShowCreateSubtask(true); }} style={{ flex: 1 }}>
                <Plus size={14} style={{ marginRight: 2 }} />子任务
              </AppButton>
            </div>
          )}

          {/* 阻塞提示（弹窗内也显示） */}
          {blockedError && blockedError.blocked.length > 0 && (
            <div style={{
              marginTop: 10, padding: '8px 12px', background: 'rgba(239, 68, 68, 0.08)',
              borderRadius: 6, border: '1px solid rgba(239, 68, 68, 0.2)',
            }}>
              <div style={{ fontSize: 12, color: '#ef4444', fontWeight: 600, marginBottom: 4 }}>
                当前被 {blockedError.blocked.length} 个工单阻塞：
              </div>
              {blockedError.blocked.slice(0, 3).map(b => (
                <div key={b.task_id} style={{ fontSize: 11, color: '#7f1d1d', cursor: 'pointer' }}
                  onClick={() => navigate(`/tasks/${b.task_id}`)}>
                  #{b.task_id} {b.title}
                </div>
              ))}
              {blockedError.blocked.length > 3 && (
                <div style={{ fontSize: 11, color: '#991b1b' }}>...还有 {blockedError.blocked.length - 3} 个</div>
              )}
            </div>
          )}
        </div>

        {/* 画布内容 */}
        <div style={{
          flex: 1, overflow: 'auto',
          borderTop: 'none', borderRadius: 0,
        }}>
          {loading ? (
            <div style={{ padding: '48px 24px', textAlign: 'center', color: '#64748b', fontSize: 13 }}>
              加载中...
            </div>
          ) : renderCanvas()}
        </div>

        <div style={{ height: 20 }} />
        </Popup>
      )}

      {/* 添加关联弹窗 */}
      <Popup visible={showAdd} onVisibleChange={(v) => setShowAdd(v)} placement="bottom"
        style={{ maxHeight: '80vh', borderRadius: '16px 16px 0 0' }}>
        <div style={{ padding: 16 }}>
          <h3 style={{ margin: '0 0 16px', fontSize: 16, fontWeight: 600 }}>添加关联</h3>
          <div style={{ marginBottom: 16 }}>
            <div style={{ fontSize: 13, color: 'var(--muted-foreground)', marginBottom: 8 }}>选择关系类型</div>
            <div style={{ display: 'flex', gap: 8 }}>
              {(['predecessor', 'duplicate', 'subtask'] as RelationType[]).map(t => (
                <Button key={t} theme={addingType === t ? 'primary' : 'default'} size="small"
                  onClick={() => setAddingType(t)}>
                  {RELATION_TYPE_LABEL[t]}
                </Button>
              ))}
            </div>
            <div style={{ fontSize: 12, color: 'var(--muted-foreground)', marginTop: 6 }}>
              {RELATION_TYPE_DESC[addingType]}
            </div>
          </div>
          <div ref={inputWrapRef} style={{ marginBottom: 12, position: 'relative' }}>
            <Input
              value={searchKeyword}
              onChange={(v) => handleKeywordChange(v as string)}
              onFocus={() => { if (searchResults.length > 0) setShowDropdown(true); }}
              placeholder="输入工单 #ID 或 标题内容过滤搜索"
              clearable
            />
            {/* 下拉结果列表（下方空间不够时向上翻转） */}
            {showDropdown && (searching || searchResults.length > 0 || emptyHint) && (
              <div style={{
                position: 'absolute',
                ...(flipUp
                  ? { bottom: '100%', top: 'auto', marginBottom: 4, marginTop: 0 }
                  : { top: '100%', bottom: 'auto', marginTop: 4 }
                ),
                left: 0, right: 0,
                background: 'white',
                border: '1px solid var(--border, #e2e8f0)',
                borderRadius: 8, maxHeight: 240, overflowY: 'auto',
                zIndex: 100, boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
              }}>
                {searching && (
                  <div style={{ padding: '10px 12px', fontSize: 12, color: 'var(--muted-foreground)' }}>
                    搜索中…
                  </div>
                )}
                {!searching && emptyHint && (
                  <div style={{ padding: '10px 12px', fontSize: 12, color: '#dc2626' }}>
                    {emptyHint}
                  </div>
                )}
                {!searching && searchResults.map((item: any) => (
                  <div key={item.id} style={{
                      padding: '8px 12px',
                      cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8,
                      borderBottom: '1px solid #f1f5f9',
                    }}
                    onClick={() => handleSelectSearchResult(item.id)}
                    onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'var(--secondary, rgba(0,0,0,0.04))'; }}
                    onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = 'transparent'; }}
                  >
                    <span style={{ color: '#1e40af', fontSize: 12, fontWeight: 600, fontFamily: 'ui-monospace, monospace' }}>
                      #{item.id}
                    </span>
                    <span style={{ flex: 1, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {item.title}
                    </span>
                    <Tag theme="default" style={{ borderRadius: 999, fontSize: 11, padding: '2px 8px', border: 'none', background: 'var(--secondary, rgba(0,0,0,0.04))' }}>
                      {item.status}
                    </Tag>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div style={{ height: 8 }} />
        </div>
      </Popup>

      {/* 创建子任务弹窗（参考 ChatPanel 确认工单弹窗样式，ticket-confirm__* CSS 类复用全局样式） */}
      <Popup visible={showCreateSubtask} onVisibleChange={(v) => setShowCreateSubtask(v)} placement="bottom"
        style={{ maxHeight: '90vh', borderRadius: '16px 16px 0 0' }} showOverlay>
        <div className="ticket-confirm">
          <h4 className="ticket-confirm__title">创建子任务</h4>
          <div className="ticket-confirm__body">
            {/* 继承信息提示：项目 / 客户自动继承；工单类型默认继承但可手动切换 */}
            <div className="ticket-confirm__banner ticket-confirm__banner--info" style={{ marginBottom: 4 }}>
              子任务将继承父工单的项目、客户信息；工单类型默认继承，可按需切换
            </div>

            <label className="ticket-confirm__label">工单类型</label>
            <select
              className="ticket-confirm__select"
              value={creatingSubtaskTicketType}
              onChange={(e) => setCreatingSubtaskTicketType(e.target.value)}
            >
              {Object.entries(TICKET_TYPE_DISPLAY_MAP).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>

            <label className="ticket-confirm__label">标题 <span style={{ color: 'var(--destructive)' }}>*</span></label>
            <input
              className="ticket-confirm__input"
              value={creatingSubtaskTitle}
              onChange={(e) => setCreatingSubtaskTitle(e.target.value)}
              placeholder="简洁描述要做什么"
              maxLength={120}
            />

            <label className="ticket-confirm__label">描述</label>
            <textarea
              className="ticket-confirm__textarea"
              value={creatingSubtaskDesc}
              onChange={(e) => setCreatingSubtaskDesc(e.target.value)}
              placeholder="具体要做的事、验收标准（选填）"
              rows={3}
              maxLength={1000}
            />

            <label className="ticket-confirm__label">优先级</label>
            <select
              className="ticket-confirm__select"
              value={creatingSubtaskPriority}
              onChange={(e) => setCreatingSubtaskPriority(e.target.value)}
            >
              {Object.entries(PRIORITY_DISPLAY_MAP).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>

            {/* 指定处理人（选填）：选了直接派给此人；没选走 AI 派单 Worker 重新派单 */}
            <label className="ticket-confirm__label">
              指定处理人 <span style={{ color: 'var(--muted-foreground)', fontSize: 12 }}>（选填，不选则 AI 自动派单）</span>
            </label>
            <UserSelect
              value={creatingSubtaskAssignee?.id ?? null}
              onChange={setCreatingSubtaskAssignee}
              placeholder="点击选择处理人（留空走 AI 派单）"
              title="选择处理人"
            />

            {/* 处理阶段：始终显示，加载中显示占位 */}
            <label className="ticket-confirm__label">处理阶段 <span style={{ color: 'var(--destructive)' }}>*</span></label>
            <select
              className="ticket-confirm__select"
              value={creatingSubtaskStepId ?? ''}
              onChange={(e) => setCreatingSubtaskStepId(e.target.value ? Number(e.target.value) : null)}
            >
              {stepsLoading && <option value="">加载中…</option>}
              {!stepsLoading && subtaskStepTemplate.length === 0 && <option value="">该类型暂无阶段模板</option>}
              {subtaskStepTemplate.map((s) => (
                <option key={s.id} value={s.id}>{s.step_name}</option>
              ))}
            </select>

            {/* 当前阶段截止时间：快捷按钮 + antd DatePicker，精确到分钟 */}
            <label className="ticket-confirm__label">当前阶段截止时间 <span style={{ color: 'var(--destructive)' }}>*</span></label>
            <div className="ticket-confirm__quick-options">
              {STEP_QUICK_OPTIONS.map((o) => {
                const active = subtaskStepEndtimeValue
                  && subtaskStepEndtimeValue.isSame(dayjs().add(o.value, 'day'), 'minute');
                return (
                  <button
                    key={o.value}
                    type="button"
                    className={`ticket-confirm__quick-option${active ? ' ticket-confirm__quick-option--active' : ''}`}
                    onClick={() => setCreatingSubtaskStepEndtime(dayjs().add(o.value, 'day').second(0).millisecond(0).toISOString())}
                  >
                    {o.label}
                  </button>
                );
              })}
            </div>
            <DatePicker
              style={{ width: '100%' }}
              placeholder="点击选择"
              format="YYYY-MM-DD HH:mm"
              showTime={{ format: 'HH:mm', showNow: true }}
              showNow
              placement="topLeft"
              getPopupContainer={(trigger) => trigger.parentElement || document.body}
              value={subtaskStepEndtimeValue}
              onChange={(d: dayjs.Dayjs | null) => setCreatingSubtaskStepEndtime(d ? d.second(0).millisecond(0).toISOString() : '')}
              styles={{ popup: { root: { zIndex: 12000 } } }}
            />

            {/* 整体截止时间（deadline_at，选填，继承父工单） */}
            <label className="ticket-confirm__label">整体截止时间 <span style={{ color: 'var(--muted-foreground)', fontSize: 12 }}>（选填，默认继承父工单）</span></label>
            <input
              type="datetime-local"
              className="ticket-confirm__input"
              value={creatingSubtaskDeadline
                ? dayjs(creatingSubtaskDeadline).format('YYYY-MM-DDTHH:mm')
                : ''}
              onChange={(e) => setCreatingSubtaskDeadline(e.target.value ? dayjs(e.target.value).toISOString() : null)}
            />
          </div>
          <div className="ticket-confirm__btns">
            <button
              type="button"
              className="ticket-confirm__btn ticket-confirm__btn--cancel"
              onClick={() => setShowCreateSubtask(false)}
            >取消</button>
            <button
              type="button"
              className="ticket-confirm__btn ticket-confirm__btn--confirm"
              onClick={handleCreateSubtask}
              disabled={creatingSubtaskLoading}
            >{creatingSubtaskLoading ? '创建中…' : '确认创建'}</button>
          </div>
        </div>
      </Popup>
    </div>
  );
}
