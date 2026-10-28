// 责任双树试做页：节点级 CRUD 直连 DB（resp_trees/nodes/links）+ revision 乐观锁。
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Loading, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';
import { getProfileOptions } from '@/api/profile';
import type { ProfileFieldOptions } from '@/api/profile';
import {
  RespTreeConflictError,
  createRespNode,
  deleteRespLink,
  deleteRespNode,
  getRespTreeNodes,
  importTrialSnapshot,
  patchRespNode,
  putRespLink,
  seedRespTrees,
} from '@/api/respTree';
import { buildSnapshot, parseSnapshot, snapshotBrief, snapshotFileName } from './dualTreeTrialSnapshot';
import type { TrialSnapshot } from './dualTreeTrialSnapshot';
import { buildBootstrapSnapshot } from './dualTreeBootstrap';
import {
  payloadsToTrial,
  resolveOrgParentId,
  type TrialMount,
  type TrialMountRole,
  type TrialNode,
  type TrialOrgKind,
} from './dualTreeDbAdapter';

type MountRole = TrialMountRole;
type OrgKind = TrialOrgKind;
type NodeKind = TrialNode['kind'];
type Mount = TrialMount;

const ROLE_LABEL: Record<MountRole, string> = {
  member: '成员',
  owner: '负责人',
  specialist: '专人',
  leader: '领导',
};

interface Person {
  id: string;
  name: string;
  company: string;
  department: string;
  status?: string;
}

function firstLevelOpen(nodes: TrialNode[]) {
  const open: Record<number, boolean> = {};
  nodes.forEach((n) => { open[n.id] = true; });
  return open;
}

function nodePeople(mounts: Mount[], nodeId: number, role: MountRole, personName: (id: string) => string) {
  return mounts
    .filter((m) => m.nodeId === nodeId && m.role === role)
    .map((m) => ({ id: m.userId, name: personName(m.userId) }));
}

function NodeFacts({
  node, mounts, personName,
}: {
  node: TrialNode;
  mounts: Mount[];
  personName: (id: string) => string;
}) {
  const heads = nodePeople(mounts, node.id, 'owner', personName);
  const leaders = node.kind === 'skill' ? nodePeople(mounts, node.id, 'leader', personName) : [];
  const body = nodePeople(mounts, node.id, node.kind === 'skill' ? 'specialist' : 'member', personName);
  const duty = node.description.trim();
  const vehicles = node.vehicleModels.trim();
  const rich = node.kind !== 'domain';
  if (!rich && !duty && heads.length === 0 && leaders.length === 0) return null;
  const bodyLabel = node.kind === 'skill' ? '专人' : '成员';
  return (
    <div className="dt-facts">
      {(rich || duty) && (
        <div className="dt-fact">
          <span className="dt-fact-k">职责</span>
          <span className={duty ? 'dt-fact-v' : 'dt-fact-miss'}>{duty || '还没写'}</span>
        </div>
      )}
      {vehicles && (
        <div className="dt-fact">
          <span className="dt-fact-k">车型</span>
          <span className="dt-fact-v">{vehicles}</span>
        </div>
      )}
      {(rich || heads.length > 0) && (
        <div className="dt-fact">
          <span className="dt-fact-k">负责人</span>
          {heads.length > 0
            ? <span className="dt-people">{heads.map((p) => <span key={p.id} className="dt-person dt-person--head">{p.name}</span>)}</span>
            : <span className="dt-fact-miss">未指定</span>}
        </div>
      )}
      {node.kind === 'skill' && (rich || leaders.length > 0) && (
        <div className="dt-fact">
          <span className="dt-fact-k">领导</span>
          {leaders.length > 0
            ? <span className="dt-people">{leaders.map((p) => <span key={p.id} className="dt-person dt-person--head">{p.name}</span>)}</span>
            : <span className="dt-fact-miss">未指定</span>}
        </div>
      )}
      {rich && (
        <div className="dt-fact">
          <span className="dt-fact-k">{bodyLabel}</span>
          {body.length > 0
            ? <span className="dt-people">{body.map((p) => <span key={p.id} className="dt-person">{p.name}</span>)}</span>
            : <span className="dt-fact-miss">还没有</span>}
        </div>
      )}
    </div>
  );
}

export default function DualTreeTrial() {
  const navigate = useNavigate();
  const request = useMemo(() => createRequest(API_CONFIG.ADMIN.BASE_URL, 'Admin'), []);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [statusLine, setStatusLine] = useState('落库编辑 · revision 乐观锁');
  const [people, setPeople] = useState<Person[]>([]);
  const [nodes, setNodes] = useState<TrialNode[]>([]);
  const [mounts, setMounts] = useState<Mount[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const [catalog, setCatalog] = useState<ProfileFieldOptions | null>(null);
  const [orgPick, setOrgPick] = useState<null | { domainId: number; parentId: number }>(null);
  const [orgKeyword, setOrgKeyword] = useState('');
  const [pickerMode, setPickerMode] = useState<'off' | 'head' | 'people'>('off');
  const [keyword, setKeyword] = useState('');
  const [pickRole, setPickRole] = useState<MountRole>('member');
  const [dropTargetId, setDropTargetId] = useState<number | null>(null);
  const dragOrgRef = useRef<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const skillTreeIdRef = useRef(0);
  const orgTreeIdRef = useRef(0);
  const skillRevRef = useRef(0);
  const orgRevRef = useRef(0);
  const orgDomainMapRef = useRef<Record<number, number>>({});
  const patchTimers = useRef<Record<number, ReturnType<typeof setTimeout>>>({});
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;

  const applyView = useCallback((view: ReturnType<typeof payloadsToTrial>, keepSelected?: number | null) => {
    skillTreeIdRef.current = view.skillTreeId;
    orgTreeIdRef.current = view.orgTreeId;
    skillRevRef.current = view.skillRevision;
    orgRevRef.current = view.orgRevision;
    orgDomainMapRef.current = view.orgDomainBySkillDomain;
    setNodes(view.nodes);
    setMounts(view.mounts);
    setExpanded(firstLevelOpen(view.nodes));
    setStatusLine(
      `落库 · skill rev ${view.skillRevision} / org rev ${view.orgRevision}`
      + ` · ${view.nodes.length} 节点 / ${view.mounts.length} 挂人`,
    );
    const prefer = keepSelected != null && view.nodes.some((n) => n.id === keepSelected)
      ? keepSelected
      : (view.nodes.find((n) => n.kind === 'domain')?.id ?? view.nodes[0]?.id ?? null);
    setSelectedId(prefer);
  }, []);

  const reloadFromDb = useCallback(async (keepSelected?: number | null) => {
    const seeded = await seedRespTrees();
    const trees = seeded.trees || [];
    const skillMeta = trees.find((t) => t.kind === 'skill');
    const orgMeta = trees.find((t) => t.kind === 'org');
    if (!skillMeta || !orgMeta) throw new Error('未初始化 org/skill 树');
    let skill = await getRespTreeNodes(skillMeta.id);
    let org = await getRespTreeNodes(orgMeta.id);
    if (skill.nodes.length === 0 && org.nodes.length === 0) {
      const boot = buildBootstrapSnapshot();
      await importTrialSnapshot({ nodes: boot.nodes, mounts: boot.mounts, force: false });
      skill = await getRespTreeNodes(skillMeta.id);
      org = await getRespTreeNodes(orgMeta.id);
    }
    applyView(payloadsToTrial(skill, org), keepSelected);
  }, [applyView]);

  const onConflict = useCallback(async () => {
    Toast({ message: '树已被他人修改，已重新加载', theme: 'warning' });
    try {
      await reloadFromDb(selectedId);
    } catch {
      Toast({ message: '重新加载失败', theme: 'error' });
    }
  }, [reloadFromDb, selectedId]);

  const runWrite = useCallback(async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      if (e instanceof RespTreeConflictError) {
        await onConflict();
      } else {
        Toast({ message: e instanceof Error ? e.message : '保存失败', theme: 'error' });
      }
    } finally {
      setBusy(false);
    }
  }, [onConflict]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const raw = await request<Person[]>('/users/?skip=0&limit=1000');
        if (!cancelled) {
          setPeople((raw || [])
            .filter((u) => u.id && (u.status === 'active' || !u.status))
            .map((u) => ({
              id: u.id,
              name: u.name || u.id,
              company: (u.company || '').trim(),
              department: (u.department || '').trim(),
              status: u.status,
            })));
        }
      } catch {
        if (!cancelled) Toast({ message: '人员名单没有加载到，可以先编辑树结构', theme: 'warning' });
      }
      try {
        const options = await getProfileOptions();
        if (!cancelled) setCatalog(options);
      } catch {
        if (!cancelled) Toast({ message: '公司部门名单没有加载到，仍可手写名称', theme: 'warning' });
      }
      try {
        await reloadFromDb();
      } catch (e) {
        if (!cancelled) {
          Toast({ message: e instanceof Error ? e.message : '加载双树失败', theme: 'error' });
        }
      }
      if (!cancelled) setLoading(false);
    })();
    return () => { cancelled = true; };
  }, [request, reloadFromDb]);

  const selected = nodes.find((n) => n.id === selectedId) || null;
  const personById = useMemo(() => {
    const map = new Map<string, Person>();
    people.forEach((p) => map.set(p.id, p));
    return map;
  }, [people]);

  const domainIdOf = (nodeId: number) => {
    let cur = nodes.find((n) => n.id === nodeId);
    const guard = new Set<number>();
    while (cur && !guard.has(cur.id)) {
      if (cur.kind === 'domain') return cur.id;
      guard.add(cur.id);
      cur = nodes.find((n) => n.id === cur?.parentId);
    }
    return nodeId;
  };

  const bumpRev = (tree: 'skill' | 'org', revision: number) => {
    if (tree === 'skill') skillRevRef.current = revision;
    else orgRevRef.current = revision;
    setStatusLine(
      `落库 · skill rev ${skillRevRef.current} / org rev ${orgRevRef.current}`
      + ` · ${nodesRef.current.length} 节点`,
    );
  };

  const schedulePatch = (nodeId: number, patch: Partial<TrialNode>) => {
    setNodes((prev) => prev.map((n) => (n.id === nodeId ? { ...n, ...patch } : n)));
    clearTimeout(patchTimers.current[nodeId]);
    patchTimers.current[nodeId] = setTimeout(() => {
      void runWrite(async () => {
        const node = nodesRef.current.find((n) => n.id === nodeId);
        if (!node) return;
        const tree = node.tree;
        const expected = tree === 'skill' ? skillRevRef.current : orgRevRef.current;
        const body: Record<string, unknown> = { expected_revision: expected };
        if (patch.name != null) body.name = patch.name;
        if (patch.description != null) body.description = patch.description;
        if (patch.vehicleModels != null) body.vehicle_models = patch.vehicleModels;
        const res = await patchRespNode(nodeId, body);
        bumpRev(tree, res.revision);
        if (node.kind === 'domain' && patch.name != null) {
          const orgId = orgDomainMapRef.current[nodeId];
          if (orgId) {
            const orgRes = await patchRespNode(orgId, {
              name: patch.name,
              expected_revision: orgRevRef.current,
            });
            bumpRev('org', orgRes.revision);
          }
        }
      });
    }, 500);
  };

  const updateNode = (id: number, patch: Partial<TrialNode>) => {
    schedulePatch(id, patch);
  };

  const addNamedOrg = (parentId: number, name: string, orgKind?: OrgKind) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    if (nodes.some((n) => n.parentId === parentId && n.kind === 'org' && n.name === trimmed)) return;
    void runWrite(async () => {
      const dbParent = resolveOrgParentId(parentId, orgDomainMapRef.current, nodesRef.current);
      if (dbParent == null) throw new Error('找不到组织父节点');
      const res = await createRespNode({
        tree_id: orgTreeIdRef.current,
        parent_id: dbParent,
        name: trimmed,
        node_kind: 'org',
        org_kind: orgKind || null,
        expected_revision: orgRevRef.current,
      });
      bumpRev('org', res.revision);
      await reloadFromDb(res.node.id);
      setExpanded((prev) => ({ ...prev, [parentId]: true }));
      setSelectedId(res.node.id);
    });
  };

  const openOrgPick = (parentId: number) => {
    setSelectedId(parentId);
    setOrgPick({ domainId: domainIdOf(parentId), parentId });
    setOrgKeyword('');
    setPickerMode('off');
  };

  const chooseOrg = (parentId: number, name: string, orgKind?: OrgKind) => {
    addNamedOrg(parentId, name, orgKind);
    setOrgPick(null);
  };

  const addNode = (parentId: number | null, kind: NodeKind, orgKind?: OrgKind) => {
    const name = kind === 'org' ? '新组织' : (kind === 'domain' ? '新问题领域' : '新技能');
    void runWrite(async () => {
      if (kind === 'domain') {
        const sk = await createRespNode({
          tree_id: skillTreeIdRef.current,
          name,
          node_kind: 'umbrella',
          expected_revision: skillRevRef.current,
        });
        bumpRev('skill', sk.revision);
        const og = await createRespNode({
          tree_id: orgTreeIdRef.current,
          name,
          node_kind: 'umbrella',
          linked_node_id: sk.node.id,
          expected_revision: orgRevRef.current,
        });
        bumpRev('org', og.revision);
        orgDomainMapRef.current = { ...orgDomainMapRef.current, [sk.node.id]: og.node.id };
        await reloadFromDb(sk.node.id);
        return;
      }
      if (kind === 'skill') {
        if (parentId == null) throw new Error('技能需要父节点');
        const res = await createRespNode({
          tree_id: skillTreeIdRef.current,
          parent_id: parentId,
          name,
          node_kind: 'leaf',
          expected_revision: skillRevRef.current,
        });
        bumpRev('skill', res.revision);
        await reloadFromDb(res.node.id);
        setExpanded((prev) => ({ ...prev, [parentId]: true }));
        return;
      }
      if (parentId == null) throw new Error('组织需要父节点');
      const dbParent = resolveOrgParentId(parentId, orgDomainMapRef.current, nodesRef.current);
      if (dbParent == null) throw new Error('找不到组织父节点');
      const res = await createRespNode({
        tree_id: orgTreeIdRef.current,
        parent_id: dbParent,
        name,
        node_kind: 'org',
        org_kind: orgKind || null,
        expected_revision: orgRevRef.current,
      });
      bumpRev('org', res.revision);
      await reloadFromDb(res.node.id);
      setExpanded((prev) => ({ ...prev, [parentId]: true }));
    });
  };

  const removeNode = (id: number) => {
    const node = nodes.find((n) => n.id === id);
    if (!node || node.kind === 'domain') return;
    void runWrite(async () => {
      const tree = node.tree;
      const expected = tree === 'skill' ? skillRevRef.current : orgRevRef.current;
      const res = await deleteRespNode(id, expected);
      bumpRev(tree, res.revision);
      await reloadFromDb(null);
    });
  };

  const blockedDockIds = (orgId: number) => {
    const blocked = new Set<number>([orgId]);
    const walk = (cur: number) => {
      nodes.filter((n) => n.parentId === cur).forEach((n) => {
        blocked.add(n.id);
        walk(n.id);
      });
    };
    walk(orgId);
    return blocked;
  };

  const canDock = (orgId: number, parentId: number) => {
    if (!orgId || orgId === parentId) return false;
    const org = nodes.find((n) => n.id === orgId);
    const parent = nodes.find((n) => n.id === parentId);
    if (!org || org.kind !== 'org' || !parent) return false;
    if (parent.kind !== 'org' && parent.kind !== 'domain') return false;
    if (org.parentId === parentId) return false;
    return !blockedDockIds(orgId).has(parentId);
  };

  const dockOrg = (orgId: number, parentId: number) => {
    if (!canDock(orgId, parentId)) return;
    const org = nodes.find((n) => n.id === orgId);
    if (!org) return;
    const clash = nodes.some((n) => n.id !== orgId && n.parentId === parentId && n.kind === 'org' && n.name === org.name);
    if (clash) {
      Toast({ message: '这一层已经有同名组织', theme: 'warning' });
      setDropTargetId(null);
      return;
    }
    void runWrite(async () => {
      const dbParent = resolveOrgParentId(parentId, orgDomainMapRef.current, nodesRef.current);
      if (dbParent == null) throw new Error('找不到目标父节点');
      const res = await patchRespNode(orgId, {
        parent_id: dbParent,
        expected_revision: orgRevRef.current,
      });
      bumpRev('org', res.revision);
      await reloadFromDb(orgId);
      setExpanded((prev) => ({ ...prev, [parentId]: true }));
      setDropTargetId(null);
    });
  };

  const nodeMounts = selected ? mounts.filter((m) => m.nodeId === selected.id) : [];
  const headRole: MountRole = 'owner';
  const headMounts = nodeMounts.filter((m) => m.role === headRole);
  const otherMounts = nodeMounts.filter((m) => m.role !== headRole);

  const suggestKind = useCallback((person: Person, node: TrialNode): 'dept' | 'company' | null => {
    if (node.kind !== 'org') return null;
    const parent = nodes.find((n) => n.id === node.parentId);
    if (person.department && person.department === node.name) {
      const underOtherCompany = parent?.kind === 'org'
        && parent.orgKind === 'company'
        && !!person.company
        && parent.name !== person.company;
      if (!underOtherCompany) return 'dept';
    }
    if (person.company === node.name && !person.department) return 'company';
    return null;
  }, [nodes]);

  const toggleMount = (userId: string, role: MountRole) => {
    if (!selected) return;
    const hit = mounts.find((m) => m.nodeId === selected.id && m.userId === userId && m.role === role);
    const tree = selected.tree;
    void runWrite(async () => {
      const expected = tree === 'skill' ? skillRevRef.current : orgRevRef.current;
      if (hit) {
        // 同人同节点唯一：取消时删整行；若还有其它角色需再 put
        const others = mounts.filter((m) => m.nodeId === selected.id && m.userId === userId && m.role !== role);
        const res = await deleteRespLink(selected.id, userId, expected);
        bumpRev(tree, res.revision);
        let rev = res.revision;
        for (const m of others) {
          const put = await putRespLink({
            node_id: selected.id,
            user_id: userId,
            role: m.role,
            expected_revision: rev,
          });
          rev = put.revision;
          bumpRev(tree, rev);
        }
      } else {
        const res = await putRespLink({
          node_id: selected.id,
          user_id: userId,
          role,
          expected_revision: expected,
        });
        bumpRev(tree, res.revision);
      }
      await reloadFromDb(selected.id);
    });
  };

  const importOrgFromSystem = (domainId: number) => {
    void runWrite(async () => {
      const companies = new Map<string, number>();
      const deptKeys = new Set<string>();
      nodesRef.current.forEach((n) => {
        if (n.parentId === domainId && n.orgKind === 'company') companies.set(n.name, n.id);
        if (n.orgKind === 'department' && n.parentId != null) {
          deptKeys.add(`${n.parentId}::${n.name}`);
        }
      });
      const dbDomain = resolveOrgParentId(domainId, orgDomainMapRef.current, nodesRef.current);
      if (dbDomain == null) throw new Error('找不到领域对应的组织树域节点');
      for (const p of people) {
        const company = (p.company || '').trim();
        if (!company) continue;
        let companyId = companies.get(company);
        if (!companyId) {
          const res = await createRespNode({
            tree_id: orgTreeIdRef.current,
            parent_id: dbDomain,
            name: company,
            node_kind: 'org',
            org_kind: 'company',
            expected_revision: orgRevRef.current,
          });
          bumpRev('org', res.revision);
          companyId = res.node.id;
          companies.set(company, companyId);
        }
        const dept = (p.department || '').trim();
        if (!dept) continue;
        const dkey = `${companyId}::${dept}`;
        if (deptKeys.has(dkey)) continue;
        const res = await createRespNode({
          tree_id: orgTreeIdRef.current,
          parent_id: companyId,
          name: dept,
          node_kind: 'org',
          org_kind: 'department',
          expected_revision: orgRevRef.current,
        });
        bumpRev('org', res.revision);
        deptKeys.add(dkey);
      }
      await reloadFromDb(domainId);
      setExpanded((prev) => ({ ...prev, [domainId]: true }));
      Toast({ message: '已把系统里的公司部门加到这个领域的组织树。人员仍需点选激活。', theme: 'success' });
    });
  };

  const exportSnapshot = () => {
    // 导出试做格式：把 number id 转成 string，便于 migrate 脚本
    const snap = buildSnapshot(
      nodes.map((n) => ({
        id: String(n.id),
        parentId: n.parentId == null ? null : String(n.parentId),
        kind: n.kind,
        orgKind: n.orgKind,
        name: n.name,
        description: n.description,
        vehicleModels: n.vehicleModels,
      })),
      mounts.map((m) => ({
        nodeId: String(m.nodeId),
        userId: m.userId,
        role: m.role,
      })),
    );
    const url = URL.createObjectURL(new Blob([JSON.stringify(snap, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = snapshotFileName(snap.saved_at);
    link.click();
    URL.revokeObjectURL(url);
  };

  const importSnapshot = async (file: File) => {
    let parsed: TrialSnapshot<{
      id: string; parentId: string | null; kind: NodeKind; orgKind?: OrgKind;
      name: string; description: string; vehicleModels: string;
    }, { nodeId: string; userId: string; role: MountRole }> | null = null;
    try {
      parsed = parseSnapshot(await file.text());
    } catch {
      parsed = null;
    }
    if (!parsed) {
      Toast({ message: '这个文件不是双树试做导出的 JSON', theme: 'warning' });
      return;
    }
    void runWrite(async () => {
      await importTrialSnapshot({
        nodes: parsed!.nodes,
        mounts: parsed!.mounts,
        force: true,
      });
      await reloadFromDb();
      Toast({ message: `已导入并写入数据库：${snapshotBrief(parsed!)}`, theme: 'success' });
    });
  };

  const renderNodes = (parentId: number, kind: 'org' | 'skill') => {
    const list = nodes.filter((n) => n.parentId === parentId && n.kind === kind);
    if (list.length === 0) {
      return <li className="dt-empty">这一支还是空的</li>;
    }
    return list.map((n) => {
      const kids = nodes.some((c) => c.parentId === n.id);
      const open = expanded[n.id] !== false;
      const kindLabel = n.kind === 'skill' ? '技能' : '组织';
      const personName = (id: string) => personById.get(id)?.name || id;
      return (
        <li key={n.id}>
          <div
            className={`dt-card ${selectedId === n.id ? 'is-on' : ''} ${n.kind === 'org' && dropTargetId === n.id ? 'is-drop' : ''}`}
            onDragOver={n.kind === 'org' ? (e) => {
              const from = dragOrgRef.current;
              if (!from || !canDock(from, n.id)) return;
              e.preventDefault();
              e.stopPropagation();
              if (dropTargetId !== n.id) setDropTargetId(n.id);
            } : undefined}
            onDrop={n.kind === 'org' ? (e) => {
              e.preventDefault();
              e.stopPropagation();
              const raw = e.dataTransfer.getData('text/plain');
              const from = Number(raw || dragOrgRef.current || 0);
              dockOrg(from, n.id);
              dragOrgRef.current = null;
              setDropTargetId(null);
            } : undefined}
          >
            <div className="dt-row">
              {n.kind === 'org' && (
                <span
                  className="dt-grip"
                  draggable
                  title="拖到另一个组织上，成为它的下级"
                  onDragStart={(e) => {
                    e.stopPropagation();
                    dragOrgRef.current = n.id;
                    e.dataTransfer.setData('text/plain', String(n.id));
                    e.dataTransfer.effectAllowed = 'move';
                  }}
                  onDragEnd={() => {
                    dragOrgRef.current = null;
                    setDropTargetId(null);
                  }}
                >
                  ⋮⋮
                </span>
              )}
              {kids ? (
                <button
                  type="button"
                  className="dt-twist"
                  aria-label={open ? '收起' : '展开'}
                  onClick={() => setExpanded((prev) => ({ ...prev, [n.id]: !open }))}
                >
                  {open ? '▾' : '▸'}
                </button>
              ) : <span className="dt-twist" />}
              <button
                type="button"
                className="dt-name"
                onClick={() => { setSelectedId(n.id); setPickerMode('off'); }}
              >
                <span className={`dt-kind ${n.kind === 'skill' ? 'dt-kind--skill' : 'dt-kind--org'}`}>{kindLabel}</span>
                <span className="dt-label">{n.name || '未命名'}</span>
              </button>
              {n.kind === 'org' && (
                <span className="dt-row-actions">
                  <button type="button" className="dt-add" onClick={() => openOrgPick(n.id)}>下级</button>
                  <button type="button" className="dt-add dt-add--danger" onClick={() => removeNode(n.id)}>删除</button>
                </span>
              )}
            </div>
            <NodeFacts node={n} mounts={mounts} personName={personName} />
          </div>
          {kids && open && (
            <ul>{renderNodes(n.id, kind)}</ul>
          )}
        </li>
      );
    });
  };

  const renderDomain = (domain: TrialNode) => {
    const domainOpen = expanded[domain.id] !== false;
    return (
      <li key={domain.id}>
        <div className={`dt-card dt-card--root ${selectedId === domain.id ? 'is-on' : ''}`}>
          <div className="dt-row dt-row--root">
            <button
              type="button"
              className="dt-twist"
              aria-label={domainOpen ? '收起' : '展开'}
              onClick={() => setExpanded((prev) => ({ ...prev, [domain.id]: !domainOpen }))}
            >
              {domainOpen ? '▾' : '▸'}
            </button>
            <button
              type="button"
              className="dt-name"
              onClick={() => { setSelectedId(domain.id); setPickerMode('off'); }}
            >
              <span className="dt-kind dt-kind--domain">领域</span>
              <span className="dt-label">{domain.name || '未命名'}</span>
            </button>
          </div>
          <NodeFacts node={domain} mounts={mounts} personName={(id) => personById.get(id)?.name || id} />
        </div>
        {domainOpen && (
          <ul>
            <li>
              <div
                className={`dt-row dt-row--branch ${dropTargetId === domain.id ? 'is-drop' : ''}`}
                onDragOver={(e) => {
                  const from = dragOrgRef.current;
                  if (!from || !canDock(from, domain.id)) return;
                  e.preventDefault();
                  e.stopPropagation();
                  if (dropTargetId !== domain.id) setDropTargetId(domain.id);
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  const from = Number(e.dataTransfer.getData('text/plain') || dragOrgRef.current || 0);
                  dockOrg(from, domain.id);
                  dragOrgRef.current = null;
                  setDropTargetId(null);
                }}
              >
                <span className="dt-branch-mark dt-branch-mark--org" />
                <span className="dt-branch-title">组织树</span>
                <span className="dt-meta">
                  <button type="button" className="dt-add" onClick={() => openOrgPick(domain.id)}>
                    新增组织
                  </button>
                </span>
              </div>
              <ul>{renderNodes(domain.id, 'org')}</ul>
            </li>
            <li>
              <div className="dt-row dt-row--branch">
                <span className="dt-branch-mark dt-branch-mark--skill" />
                <span className="dt-branch-title">技能树</span>
                <span className="dt-meta">
                  <button type="button" className="dt-add" onClick={() => addNode(domain.id, 'skill')}>
                    添加技能
                  </button>
                </span>
              </div>
              <ul>{renderNodes(domain.id, 'skill')}</ul>
            </li>
          </ul>
        )}
      </li>
    );
  };

  const filteredPeople = people.filter((p) => {
    const q = keyword.trim();
    if (!q) return true;
    return `${p.name} ${p.company} ${p.department}`.includes(q);
  });
  const suggested = selected ? filteredPeople.filter((p) => suggestKind(p, selected)) : [];
  const others = selected ? filteredPeople.filter((p) => !suggestKind(p, selected)) : filteredPeople;

  const selectAllSuggested = () => {
    if (!selected || suggested.length === 0) return;
    suggested.forEach((p) => toggleMount(p.id, pickRole));
  };

  if (loading) return <Loading text="加载责任双树..." />;

  const orgRoles: MountRole[] = ['member', 'owner'];
  const skillRoles: MountRole[] = ['specialist', 'owner', 'leader'];
  const roleChoices = selected?.kind === 'org' ? orgRoles : skillRoles;

  return (
    <div className="mac-page" style={{ paddingBottom: 32 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <button type="button" className="mac-btn mac-btn--outline" onClick={() => navigate('/admin/users')}>
          返回
        </button>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600 }}>责任双树{busy ? ' · 保存中…' : ''}</div>
          <div style={{ fontSize: 12, color: '#888', marginTop: 2 }}>
            改动直接写入数据库（节点级 CRUD + revision 乐观锁）。不再写浏览器 localStorage。
          </div>
        </div>
      </div>

      <style>{`
        .dt-split {
          display: grid;
          grid-template-columns: minmax(520px, 1.7fr) minmax(300px, 0.75fr);
          gap: 16px;
          margin-top: 12px;
          align-items: start;
        }
        .dt-pane { max-height: calc(100vh - 168px); overflow: auto; }
        .dt-forest, .dt-forest ul { list-style: none; margin: 0; padding: 0; }
        .dt-forest > li + li { margin-top: 18px; }
        .dt-forest li { position: relative; }
        .dt-forest ul > li { padding-left: 20px; }
        .dt-forest ul > li::before {
          content: '';
          position: absolute;
          left: 9px;
          top: 0;
          bottom: 0;
          border-left: 1px solid #c5d3db;
        }
        .dt-forest ul > li:last-child::before { bottom: auto; height: 22px; }
        .dt-forest ul > li::after {
          content: '';
          position: absolute;
          left: 9px;
          top: 22px;
          width: 11px;
          border-top: 1px solid #c5d3db;
        }
        .dt-card {
          margin: 4px 4px 8px 0;
          border: 1px solid #e4eef2;
          border-radius: 10px;
          background: #fff;
        }
        .dt-card.is-on { border-color: #7eb6d4; box-shadow: inset 0 0 0 1px #d7eef8; }
        .dt-card.is-drop, .dt-row.is-drop { outline: 2px solid #3697c3; background: #f3fafd; }
        .dt-grip {
          width: 18px;
          flex-shrink: 0;
          cursor: grab;
          color: #9aa7ad;
          font-size: 11px;
          letter-spacing: -1px;
          user-select: none;
          text-align: center;
        }
        .dt-card--root { background: #f8fbfc; }
        .dt-row {
          display: flex;
          align-items: center;
          gap: 2px;
          height: 36px;
          border-radius: 8px;
          padding-right: 4px;
        }
        .dt-row:hover { background: #f4f7f8; }
        .dt-row.is-on, .dt-row.is-on:hover { background: #e7f4fb; }
        .dt-row--root .dt-label { font-weight: 600; }
        .dt-twist {
          width: 20px;
          height: 20px;
          padding: 0;
          border: none;
          background: transparent;
          color: #6a7a82;
          flex-shrink: 0;
          line-height: 20px;
          cursor: pointer;
        }
        .dt-name {
          flex: 1;
          min-width: 0;
          height: 36px;
          display: flex;
          align-items: center;
          gap: 8px;
          text-align: left;
          border: none;
          background: transparent;
          padding: 0 4px;
          color: #243136;
          font-size: 14px;
        }
        .dt-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .dt-kind {
          flex: 0 0 auto;
          font-size: 11px;
          line-height: 18px;
          padding: 0 6px;
          border-radius: 4px;
          background: #eef3f5;
          color: #5c6b73;
        }
        .dt-kind--domain { background: #243136; color: #fff; }
        .dt-kind--org { background: #e7f4fb; color: #227197; }
        .dt-kind--skill { background: #f6f1e8; color: #7a6244; }
        .dt-meta {
          margin-left: auto;
          display: flex;
          align-items: center;
          gap: 8px;
          flex-shrink: 0;
          color: #8a969c;
          font-size: 12px;
          white-space: nowrap;
        }
        .dt-owner { max-width: 140px; overflow: hidden; text-overflow: ellipsis; }
        .dt-facts { padding: 0 10px 8px 28px; display: flex; flex-direction: column; gap: 4px; }
        .dt-fact { display: flex; gap: 8px; align-items: flex-start; font-size: 12px; line-height: 1.5; }
        .dt-fact-k { flex: 0 0 36px; color: #8a969c; padding-top: 1px; }
        .dt-fact-v { color: #243136; white-space: pre-wrap; }
        .dt-fact-miss { color: #b0b8bc; }
        .dt-people { display: flex; flex-wrap: wrap; gap: 4px; }
        .dt-person { background: #f3f6f7; color: #243136; border-radius: 999px; padding: 0 8px; line-height: 20px; }
        .dt-person--head { background: #e7f4fb; color: #1d6f96; }
        .dt-row--branch { padding-left: 2px; }
        .dt-branch-mark { width: 8px; height: 8px; border-radius: 2px; margin: 0 6px 0 6px; flex-shrink: 0; }
        .dt-branch-mark--org { background: #3697c3; }
        .dt-branch-mark--skill { background: #c4a574; }
        .dt-branch-title { font-size: 13px; font-weight: 600; color: #243136; }
        .dt-add {
          border: none;
          background: transparent;
          color: #227197;
          font-size: 12px;
          line-height: 22px;
          padding: 0 6px;
          border-radius: 6px;
          cursor: pointer;
        }
        .dt-add:hover { background: #e7f4fb; }
        .dt-row-actions { display: none; align-items: center; flex-shrink: 0; }
        .dt-row:hover .dt-row-actions, .dt-row.is-on .dt-row-actions { display: flex; }
        .dt-add--danger { color: #c45c26; }
        .dt-add--danger:hover { background: #fdeee6; }
        .dt-empty {
          height: 36px;
          display: flex;
          align-items: center;
          color: #98a4a9;
          font-size: 12px;
        }
        @media (max-width: 900px) {
          .dt-split { grid-template-columns: 1fr; }
          .dt-pane { max-height: none; }
        }
      `}</style>

      <div style={{ display: 'flex', gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
        <button type="button" className="mac-btn mac-btn--outline" onClick={() => addNode(null, 'domain')}>
          添加问题领域
        </button>
        {selected?.kind === 'domain' && (
          <button type="button" className="mac-btn mac-btn--outline" onClick={() => importOrgFromSystem(selected.id)}>
            把系统公司部门填进组织树
          </button>
        )}
        <button type="button" className="mac-btn mac-btn--outline" onClick={exportSnapshot}>
          导出 JSON
        </button>
        <button type="button" className="mac-btn mac-btn--outline" onClick={() => fileInputRef.current?.click()}>
          导入 JSON（覆盖入库）
        </button>
        <button type="button" className="mac-btn mac-btn--outline" disabled={busy} onClick={() => void reloadFromDb(selectedId)}>
          刷新
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="application/json,.json"
          style={{ display: 'none' }}
          onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = '';
            if (file) void importSnapshot(file);
          }}
        />
      </div>
      <div style={{ fontSize: 12, color: '#888', marginTop: 6 }}>{statusLine}</div>

      <div className="dt-split">
        <div className="surface-card dt-pane" style={{ padding: '8px 8px 12px 4px' }}>
          <ul className="dt-forest">
            {nodes.filter((n) => n.kind === 'domain').map((domain) => renderDomain(domain))}
          </ul>
        </div>

        <div className="surface-card dt-pane" style={{ padding: 14 }}>
          {!selected && <div style={{ color: '#888' }}>先在左侧选一个节点。</div>}
          {selected && (
            <>
              <div style={{ fontSize: 12, color: '#3697c3', marginBottom: 8 }}>
                {selected.kind === 'domain' && '这是问题领域。组织树与技能树分挂两侧，改动即时落库。'}
                {selected.kind === 'org' && '这一级是组织。拖左侧手柄或改上级，即可调整挂靠。'}
                {selected.kind === 'skill' && '技能节点：挂专人 / 负责人 / 领导。'}
              </div>
              {orgPick && (
                <OrgCatalog
                  pick={orgPick}
                  catalog={catalog}
                  keyword={orgKeyword}
                  nodes={nodes}
                  onKeyword={setOrgKeyword}
                  onClose={() => setOrgPick(null)}
                  onPick={(name, orgKind) => chooseOrg(orgPick.parentId, name, orgKind)}
                  onManual={() => {
                    addNode(orgPick.parentId, 'org');
                    setOrgPick(null);
                  }}
                />
              )}
              <label className="mac-field" style={{ display: 'block' }}>
                <div className="mac-field__label">名称</div>
                <input
                  value={selected.name}
                  onChange={(e) => updateNode(selected.id, { name: e.target.value })}
                  style={{ width: '100%', marginTop: 6, padding: 8, borderRadius: 8, border: '1px solid #e4e8ea' }}
                />
              </label>
              <label style={{ display: 'block', marginTop: 10 }}>
                <div className="mac-field__label">职责描述</div>
                <textarea
                  value={selected.description}
                  onChange={(e) => updateNode(selected.id, { description: e.target.value })}
                  rows={4}
                  style={{ width: '100%', marginTop: 6, padding: 8, borderRadius: 8, border: '1px solid #e4e8ea' }}
                />
              </label>
              {selected.kind === 'org' && (
                <label style={{ display: 'block', marginTop: 10 }}>
                  <div className="mac-field__label">上级</div>
                  <select
                    value={selected.parentId ?? ''}
                    onChange={(e) => dockOrg(selected.id, Number(e.target.value))}
                    style={{ width: '100%', marginTop: 6, padding: 8, borderRadius: 8, border: '1px solid #e4e8ea', background: '#fff' }}
                  >
                    {(() => {
                      const domainId = domainIdOf(selected.id);
                      const domain = nodes.find((n) => n.id === domainId);
                      const blocked = blockedDockIds(selected.id);
                      const rows: { id: number; label: string }[] = [];
                      if (domain) rows.push({ id: domain.id, label: `直接挂在「${domain.name}」下` });
                      const walk = (pid: number, depth: number) => {
                        nodes
                          .filter((n) => n.parentId === pid && n.kind === 'org' && !blocked.has(n.id))
                          .forEach((n) => {
                            rows.push({ id: n.id, label: `${'\u3000'.repeat(depth)}${n.name || '未命名'}` });
                            walk(n.id, depth + 1);
                          });
                      };
                      if (domain) walk(domain.id, 0);
                      return rows.map((row) => <option key={row.id} value={row.id}>{row.label}</option>);
                    })()}
                  </select>
                </label>
              )}
              {selected.kind === 'org' && (
                <label style={{ display: 'block', marginTop: 10 }}>
                  <div className="mac-field__label">负责车型</div>
                  <input
                    value={selected.vehicleModels}
                    onChange={(e) => updateNode(selected.id, { vehicleModels: e.target.value })}
                    style={{ width: '100%', marginTop: 6, padding: 8, borderRadius: 8, border: '1px solid #e4e8ea' }}
                  />
                </label>
              )}

              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
                {selected.kind === 'domain' && (
                  <>
                    <button type="button" className="mac-btn mac-btn--primary" onClick={() => openOrgPick(selected.id)}>新增组织</button>
                    <button type="button" className="mac-btn mac-btn--outline" onClick={() => addNode(selected.id, 'skill')}>添加技能</button>
                  </>
                )}
                {selected.kind === 'org' && (
                  <button type="button" className="mac-btn mac-btn--primary" onClick={() => openOrgPick(selected.id)}>
                    新增下级组织
                  </button>
                )}
                {selected.kind === 'skill' && (
                  <button type="button" className="mac-btn mac-btn--outline" onClick={() => addNode(selected.id, 'skill')}>添加下级技能</button>
                )}
                {selected.kind !== 'domain' && (
                  <button type="button" className="mac-btn mac-btn--outline" onClick={() => removeNode(selected.id)}>
                    {selected.kind === 'org' ? '删除此组织' : '删除此技能'}
                  </button>
                )}
              </div>

              <div style={{ marginTop: 16, padding: 12, borderRadius: 10, background: '#f7fbfc' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                  <strong>负责人</strong>
                  <button
                    type="button"
                    className="mac-btn mac-btn--primary"
                    onClick={() => {
                      setPickRole(headRole);
                      setPickerMode((v) => (v === 'head' ? 'off' : 'head'));
                    }}
                  >
                    {pickerMode === 'head' ? '收起' : '指定负责人'}
                  </button>
                </div>
                <div style={{ marginTop: 8, fontSize: 13, color: headMounts.length ? '#243136' : '#c45c26' }}>
                  {headMounts.length
                    ? headMounts.map((m) => personById.get(m.userId)?.name || m.userId).join('、')
                    : '未指定'}
                </div>
              </div>

              {selected.kind !== 'domain' && (
                <div style={{ marginTop: 16 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <strong>已激活的人</strong>
                    <button
                      type="button"
                      className="mac-btn mac-btn--primary"
                      onClick={() => {
                        setPickerMode((v) => (v === 'people' ? 'off' : 'people'));
                        setPickRole(selected.kind === 'org' ? 'member' : 'specialist');
                      }}
                    >
                      {pickerMode === 'people' ? '收起加人' : '加人'}
                    </button>
                  </div>
                  {otherMounts.length === 0 && (
                    <div style={{ color: '#888', fontSize: 13, marginTop: 8 }}>还没有激活任何人。</div>
                  )}
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
                    {otherMounts.map((m) => {
                      const p = personById.get(m.userId);
                      return (
                        <button
                          key={`${m.userId}-${m.role}`}
                          type="button"
                          onClick={() => toggleMount(m.userId, m.role)}
                          style={{ border: '1px solid #d5e6ef', background: '#f4fbfe', borderRadius: 999, padding: '4px 10px' }}
                        >
                          {p?.name || m.userId} · {ROLE_LABEL[m.role]} · 取消
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}

              {pickerMode !== 'off' && (
                <div style={{ marginTop: 12, borderTop: '1px solid #eef1f2', paddingTop: 12 }}>
                  {pickerMode === 'people' && (
                    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
                      {roleChoices.map((role) => (
                        <button
                          key={role}
                          type="button"
                          className={pickRole === role ? 'mac-btn mac-btn--primary' : 'mac-btn mac-btn--outline'}
                          onClick={() => setPickRole(role)}
                        >
                          激活为{ROLE_LABEL[role]}
                        </button>
                      ))}
                    </div>
                  )}
                  <input
                    value={keyword}
                    onChange={(e) => setKeyword(e.target.value)}
                    placeholder="搜索姓名、公司、部门"
                    style={{ width: '100%', padding: 8, borderRadius: 8, border: '1px solid #e4e8ea' }}
                  />
                  {selected.kind === 'org' && (
                    <PersonBlock
                      title={
                        suggested.some((p) => suggestKind(p, selected) === 'dept')
                          ? '系统里已在此部门'
                          : '系统里已在此公司、还没有部门'
                      }
                      people={suggested}
                      mounts={nodeMounts}
                      role={pickRole}
                      badgeOf={(p) => (suggestKind(p, selected) === 'company' ? '已在此公司' : '已在此部门')}
                      onToggle={toggleMount}
                      onSelectAll={pickerMode === 'people' ? selectAllSuggested : undefined}
                    />
                  )}
                  <PersonBlock
                    title={selected.kind === 'org' ? '其他人（含兼职）' : '选择人员'}
                    people={selected.kind === 'org' ? others : filteredPeople}
                    mounts={nodeMounts}
                    role={pickRole}
                    onToggle={toggleMount}
                  />
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function OrgCatalog({
  pick, catalog, keyword, nodes, onKeyword, onClose, onPick, onManual,
}: {
  pick: { domainId: number; parentId: number };
  catalog: ProfileFieldOptions | null;
  keyword: string;
  nodes: TrialNode[];
  onKeyword: (value: string) => void;
  onClose: () => void;
  onPick: (name: string, orgKind?: OrgKind) => void;
  onManual: () => void;
}) {
  const q = keyword.trim();
  const taken = (name: string) => nodes.some((n) => n.parentId === pick.parentId && n.kind === 'org' && n.name === name);
  const companies = (catalog?.companies || []).filter((c) => (!q || c.name.includes(q)) && !taken(c.name));
  const departments = Object.entries(catalog?.departments_by_company || {}).flatMap(([companyName, depts]) => (
    (depts || []).map((d) => ({ ...d, companyName }))
  )).filter((d) => (!q || `${d.name}${d.companyName}`.includes(q)) && !taken(d.name));
  return (
    <div style={{ marginBottom: 12, padding: 10, borderRadius: 10, background: '#f7fbfc' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <strong>新增组织</strong>
        <button type="button" className="mac-btn mac-btn--outline" onClick={onClose}>关闭</button>
      </div>
      <input
        value={keyword}
        onChange={(e) => onKeyword(e.target.value)}
        placeholder="搜索公司或部门"
        style={{ width: '100%', marginTop: 8, padding: 8, borderRadius: 8, border: '1px solid #e4e8ea' }}
      />
      <div style={{ maxHeight: 240, overflow: 'auto', marginTop: 8 }}>
        {companies.length === 0 && departments.length === 0 && <div style={{ color: '#888', fontSize: 13 }}>没有可添加的项</div>}
        {companies.length > 0 && <div style={{ fontSize: 12, color: '#8a969c', paddingTop: 4 }}>公司</div>}
        {companies.map((c) => (
          <button key={c.id} type="button" onClick={() => onPick(c.name, 'company')} style={{ display: 'block', width: '100%', textAlign: 'left', padding: '8px 0', border: 'none', borderBottom: '1px solid #f0f2f3', background: 'transparent' }}>
            {c.name}
          </button>
        ))}
        {departments.length > 0 && <div style={{ fontSize: 12, color: '#8a969c', paddingTop: 8 }}>部门</div>}
        {departments.map((d) => (
          <button key={`${d.companyName}-${d.id}`} type="button" onClick={() => onPick(d.name, 'department')} style={{ display: 'block', width: '100%', textAlign: 'left', padding: '8px 0', border: 'none', borderBottom: '1px solid #f0f2f3', background: 'transparent' }}>
            {d.name}<span style={{ color: '#888', marginLeft: 8, fontSize: 12 }}>{d.companyName}</span>
          </button>
        ))}
      </div>
      <button type="button" className="mac-btn mac-btn--outline" style={{ marginTop: 8 }} onClick={onManual}>
        新建组织
      </button>
    </div>
  );
}

function PersonBlock({
  title, people, mounts, role, badge, badgeOf, onToggle, onSelectAll,
}: {
  title: string;
  people: Person[];
  mounts: Mount[];
  role: MountRole;
  badge?: string;
  badgeOf?: (person: Person) => string | undefined;
  onToggle: (userId: string, role: MountRole) => void;
  onSelectAll?: () => void;
}) {
  const allOn = people.length > 0 && people.every((p) => mounts.some((m) => m.userId === p.id && m.role === role));
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, marginBottom: 6 }}>
        <div style={{ fontSize: 13, color: '#5c6b73' }}>{title}（{people.length}）</div>
        {onSelectAll && people.length > 0 && (
          <button type="button" className="mac-btn mac-btn--outline" onClick={onSelectAll}>
            {allOn ? '取消全选' : '全选'}
          </button>
        )}
      </div>
      <div style={{ maxHeight: 240, overflow: 'auto' }}>
        {people.slice(0, 80).map((p) => {
          const on = mounts.some((m) => m.userId === p.id && m.role === role);
          return (
            <button
              key={p.id}
              type="button"
              onClick={() => onToggle(p.id, role)}
              style={{
                display: 'flex', width: '100%', justifyContent: 'space-between', gap: 8,
                padding: '8px 0', border: 'none', borderBottom: '1px solid #f0f2f3',
                background: 'transparent', textAlign: 'left',
              }}
            >
              <span>
                {p.name}
                <span style={{ color: '#888', marginLeft: 8, fontSize: 12 }}>
                  {[p.company, p.department].filter(Boolean).join(' / ') || '未填公司部门'}
                </span>
                {(() => {
                  const mark = badgeOf?.(p) || badge;
                  return mark ? (
                    <span style={{ marginLeft: 8, fontSize: 12, color: '#227197', background: '#e8f5fb', borderRadius: 999, padding: '1px 6px' }}>
                      {mark}
                    </span>
                  ) : null;
                })()}
              </span>
              <span style={{ color: on ? '#227197' : '#888', flexShrink: 0 }}>{on ? '已激活' : '激活'}</span>
            </button>
          );
        })}
        {people.length === 0 && <div style={{ color: '#aaa', fontSize: 13 }}>没有匹配的人</div>}
      </div>
    </div>
  );
}
