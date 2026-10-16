import { describe, expect, it } from 'vitest';
import {
  SNAPSHOT_FORMAT,
  SNAPSHOT_VERSION,
  buildSnapshot,
  formatSnapshotTime,
  parseSnapshot,
  snapshotBrief,
  snapshotCounts,
  snapshotFileName,
} from './dualTreeTrialSnapshot';

const nodes = [
  { id: 'dom', parentId: null, kind: 'domain', name: '车体硬件', description: '', vehicleModels: '' },
  { id: 'org', parentId: 'dom', kind: 'org', orgKind: 'company', name: 'XX 公司', description: '', vehicleModels: '' },
  { id: 'sk', parentId: 'dom', kind: 'skill', name: '机械', description: '', vehicleModels: '' },
];
const mounts = [{ nodeId: 'sk', userId: 'u1', role: 'specialist' }];

describe('双树试做快照', () => {
  it('buildSnapshot 带上格式头，导出与落盘同一份结构', () => {
    const snap = buildSnapshot(nodes, mounts, '2026-10-14 15:52:07');
    expect(snap.format).toBe(SNAPSHOT_FORMAT);
    expect(snap.version).toBe(SNAPSHOT_VERSION);
    expect(snap.saved_at).toBe('2026-10-14 15:52:07');
    expect(snap.nodes).toHaveLength(3);
    expect(snap.mounts).toHaveLength(1);
  });

  it('parseSnapshot 认新格式、旧裸对象和 JSON 字符串', () => {
    const built = buildSnapshot(nodes, mounts, '2026-10-14 15:52:07');
    expect(parseSnapshot(built)?.nodes).toHaveLength(3);
    expect(parseSnapshot(JSON.stringify(built))?.saved_at).toBe('2026-10-14 15:52:07');
    const legacy = parseSnapshot({ nodes, mounts });
    expect(legacy?.nodes).toHaveLength(3);
    expect(legacy?.format).toBe(SNAPSHOT_FORMAT);
  });

  it('parseSnapshot 遇到坏数据返回 null，调用方据此不覆盖现有树', () => {
    expect(parseSnapshot('不是 json')).toBeNull();
    expect(parseSnapshot(null)).toBeNull();
    expect(parseSnapshot([1, 2])).toBeNull();
    expect(parseSnapshot({ nodes: [] })).toBeNull();
    expect(parseSnapshot({ mounts: [] })).toBeNull();
    expect(parseSnapshot({ nodes: {}, mounts: [] })).toBeNull();
  });

  it('文件名带时间戳，便于按份回看', () => {
    expect(snapshotFileName('2026-10-14 15:52:07')).toBe('双树试做-20261014-1552.json');
    expect(snapshotFileName('')).toBe('双树试做-snapshot.json');
  });

  it('计数与一句话说明按 kind 归类', () => {
    const snap = buildSnapshot(nodes, mounts, '2026-10-14 15:52:07');
    const counts = snapshotCounts(snap);
    expect(counts.domains).toBe(1);
    expect(counts.orgs).toBe(1);
    expect(counts.skills).toBe(1);
    expect(counts.mounts).toBe(1);
    expect(snapshotBrief(snap)).toBe('1 个问题领域 / 1 个组织 / 1 个技能 / 1 个挂人');
  });

  it('formatSnapshotTime 出可读本地时间', () => {
    expect(formatSnapshotTime(new Date(2026, 9, 14, 15, 52, 7))).toBe('2026-10-14 15:52:07');
  });
});
