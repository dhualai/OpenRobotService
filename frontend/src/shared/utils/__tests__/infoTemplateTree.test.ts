// 详情模板本地树操作测试：不可变更新、增删、排序、缩进/取消缩进、层级查询。
import { describe, it, expect } from 'vitest';
import {
  appendTemplateNode,
  countTemplateNodes,
  findTemplateNode,
  indentTargetId,
  indentTemplateNode,
  moveTemplateSibling,
  newTemplateNode,
  outdentTemplateNode,
  removeTemplateNode,
  subtreeDepth,
  templateNodeDepth,
  updateTemplateNode,
  type TemplateContentType,
} from '../infoTemplateTree';
import type { ApiInfoTemplateNode } from '@/api/infoNodes';

const n = (
  id: string,
  title: string,
  children: ApiInfoTemplateNode[] = [],
  content_type: string = 'text',
): ApiInfoTemplateNode => ({ id, title, content_type, children });

/**  fixture：  a [a1, a2[a21]]  b  */
const tree = (): ApiInfoTemplateNode[] => [
  n('a', 'A', [n('a1', 'A1'), n('a2', 'A2', [n('a21', 'A21')])]),
  n('b', 'B'),
];

const ids = (nodes: ApiInfoTemplateNode[]): string[] => nodes.map((node) => node.id);

describe('infoTemplateTree（模板树操作）', () => {
  it('newTemplateNode：默认 text 节点、允许增补、id 唯一', () => {
    const first = newTemplateNode();
    const second = newTemplateNode();
    expect(first.id).not.toBe(second.id);
    // allow_custom 恒为 true：所有节点都允许各项目在其下增补信息
    expect(first).toMatchObject({ title: '新节点', content_type: 'text', allow_custom: true });
    expect(newTemplateNode('项目类型').title).toBe('项目类型');
  });

  it('countTemplateNodes / subtreeDepth / templateNodeDepth', () => {
    const nodes = tree();
    expect(countTemplateNodes(nodes)).toBe(5);
    expect(countTemplateNodes([])).toBe(0);
    expect(subtreeDepth(nodes[0])).toBe(3);
    expect(subtreeDepth(nodes[1])).toBe(1);
    expect(templateNodeDepth(nodes, 'a')).toBe(1);
    expect(templateNodeDepth(nodes, 'a21')).toBe(3);
    expect(templateNodeDepth(nodes, '不存在')).toBe(0);
  });

  it('findTemplateNode：返回节点、父 id 与下标；找不到返回 null', () => {
    const nodes = tree();
    expect(findTemplateNode(nodes, 'a')).toMatchObject({ parentId: null, index: 0 });
    expect(findTemplateNode(nodes, 'a2')).toMatchObject({ parentId: 'a', index: 1 });
    // 深层节点 parentId 是直接父节点（a2），不是根
    const deep = findTemplateNode(nodes, 'a21');
    expect(deep?.node.title).toBe('A21');
    expect(deep?.parentId).toBe('a2');
    expect(findTemplateNode(nodes, '不存在')).toBeNull();
  });

  it('updateTemplateNode：只更新目标节点，无关子树保持原引用', () => {
    const nodes = tree();
    const next = updateTemplateNode(nodes, 'a21', { title: 'A21改' });
    expect(findTemplateNode(next, 'a21')?.node.title).toBe('A21改');
    expect(next[1]).toBe(nodes[1]); // 无关根节点引用不变（未做无谓重建）
    expect(nodes[0].children?.[1].children?.[0].title).toBe('A21'); // 原树不被改动
    // 内容类型与选项一起改
    const typed = updateTemplateNode(nodes, 'a1', { content_type: 'select' as TemplateContentType, options: ['试点', 'PK'] });
    expect(findTemplateNode(typed, 'a1')?.node).toMatchObject({ content_type: 'select', options: ['试点', 'PK'] });
  });

  it('removeTemplateNode：删除节点连带子树；未知 id 返回内容不变的新树', () => {
    const nodes = tree();
    const next = removeTemplateNode(nodes, 'a2');
    expect(ids(next[0].children ?? [])).toEqual(['a1']);
    expect(findTemplateNode(next, 'a21')).toBeNull();
    expect(next[1]).toBe(nodes[1]);

    const untouched = removeTemplateNode(nodes, '不存在');
    expect(ids(untouched)).toEqual(['a', 'b']);
    expect(countTemplateNodes(untouched)).toBe(5);
  });

  it('appendTemplateNode：追加到根 / 指定父节点的末尾', () => {
    const nodes = tree();
    const child = newTemplateNode('新');
    const atRoot = appendTemplateNode(nodes, null, child);
    expect(ids(atRoot)).toEqual(['a', 'b', child.id]);
    const underA = appendTemplateNode(nodes, 'a', child);
    expect(ids(underA[0].children ?? [])).toEqual(['a1', 'a2', child.id]);
    expect(underA[1]).toBe(nodes[1]);
  });

  it('moveTemplateSibling：同级交换；到头返回原引用', () => {
    const nodes = tree();
    expect(ids(moveTemplateSibling(nodes, 'a', 1))).toEqual(['b', 'a']);
    expect(moveTemplateSibling(nodes, 'a', -1)).toBe(nodes); // 已是第一个
    expect(moveTemplateSibling(nodes, 'b', 1)).toBe(nodes); // 已是最后一个
    expect(ids(moveTemplateSibling(nodes, 'a1', 1)[0].children ?? [])).toEqual(['a2', 'a1']);
  });

  it('indentTemplateNode：成为上一个同级节点的最后一个子节点；首个同级不动', () => {
    const nodes = tree();
    const next = indentTemplateNode(nodes, 'b');
    expect(ids(next)).toEqual(['a']);
    expect(ids(next[0].children ?? [])).toEqual(['a1', 'a2', 'b']);
    expect(indentTemplateNode(nodes, 'a')).toBe(nodes); // 根层第一个，无上一个同级
    // 深层：a2 归入 a1
    const deep = indentTemplateNode(nodes, 'a2');
    expect(ids(deep[0].children ?? [])).toEqual(['a1']);
    expect(ids(deep[0].children?.[0].children ?? [])).toEqual(['a2']);
  });

  it('outdentTemplateNode：成为父节点的下一个同级；根节点不动', () => {
    const nodes = tree();
    const next = outdentTemplateNode(nodes, 'a21');
    expect(ids(next)).toEqual(['a', 'b']);
    expect(ids(next[0].children ?? [])).toEqual(['a1', 'a2', 'a21']);
    expect(outdentTemplateNode(nodes, 'a')).toBe(nodes); // 根层无父可升
    const top = outdentTemplateNode(nodes, 'a2');
    expect(ids(top)).toEqual(['a', 'a2', 'b']);
  });

  it('indentTargetId：与 indentTemplateNode 的落点一致，降不动的返回 null', () => {
    const nodes = tree();
    expect(indentTargetId(nodes, 'b')).toBe('a');      // b 归入 a
    expect(indentTargetId(nodes, 'a2')).toBe('a1');    // 深层：a2 归入 a1
    expect(indentTargetId(nodes, 'a')).toBeNull();     // 根层第一个，无上一个同级
    expect(indentTargetId(nodes, 'a1')).toBeNull();    // 同层第一个
    expect(indentTargetId(nodes, '不存在')).toBeNull();
  });
});
