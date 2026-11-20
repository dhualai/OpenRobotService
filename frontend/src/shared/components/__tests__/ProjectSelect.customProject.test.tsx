// ProjectSelect 自定义新项目条目（ORS-1023）：
// 草稿确认弹窗允许用户输入系统里不存在的项目——搜索词在候选里无精确匹配时，
// 列表顶部出现「使用『关键词』作为新项目」条目，点击回调 onCustomPick；
// 精确匹配已有项目名时不出现（避免误建重名项目）；
// 不开 allowCustom 的调用方行为与改造前完全一致。
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, act, fireEvent } from '@testing-library/react';

vi.mock('@/api/projects', () => ({
  getProjects: vi.fn(async () => [
    { name: '杭州湾智造项目', project_code: 'HZ001', status: 'active' },
    { name: '宁波港区项目', project_code: 'NB002', status: 'active' },
  ]),
  getMyProjectRelevance: vi.fn(async () => ({ ticketed: [], owned: [] })),
}));

import ProjectSelect from '../ProjectSelect';

const openPanel = async () => {
  // 触发按钮：无选中时显示占位文案
  await act(async () => {
    fireEvent.click(await screen.findByText('请选择绑定项目'));
  });
};

const search = async (kw: string) => {
  await act(async () => {
    fireEvent.change(screen.getByPlaceholderText('搜索项目名称 / 编码…'), { target: { value: kw } });
  });
};

describe('ProjectSelect：列表外新项目条目（ORS-1023）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('搜索词无匹配：出现新项目条目，点击回调 onCustomPick 并关闭面板', async () => {
    const onCustomPick = vi.fn();
    render(<ProjectSelect allowCustom onCustomPick={onCustomPick} />);
    await openPanel();
    await search('全新测试项目');

    const createItem = await screen.findByTestId('project-select-create');
    expect(createItem.textContent).toContain('使用「全新测试项目」作为新项目');
    await act(async () => {
      fireEvent.click(createItem);
    });

    expect(onCustomPick).toHaveBeenCalledTimes(1);
    expect(onCustomPick).toHaveBeenCalledWith('全新测试项目');
    // 面板关闭：搜索框随浮层卸载
    expect(screen.queryByPlaceholderText('搜索项目名称 / 编码…')).toBeNull();
  });

  it('搜索词与已有项目名精确一致：不出现新项目条目', async () => {
    const onCustomPick = vi.fn();
    render(<ProjectSelect allowCustom onCustomPick={onCustomPick} />);
    await openPanel();
    await search('杭州湾智造项目');

    expect(await screen.findByText('HZ001')).toBeTruthy();
    expect(screen.queryByTestId('project-select-create')).toBeNull();
    expect(onCustomPick).not.toHaveBeenCalled();
  });

  it('包含匹配但无精确匹配（搜「杭州」出现「杭州湾智造项目」）：匹配结果与新项目条目并存', async () => {
    const onCustomPick = vi.fn();
    render(<ProjectSelect allowCustom onCustomPick={onCustomPick} />);
    await openPanel();
    await search('杭州');

    // 既有模糊匹配到的项目
    expect(await screen.findByText('杭州湾智造项目')).toBeTruthy();
    // 也有新项目条目（用户想建的可能恰好叫「杭州」）
    const createItem = screen.getByTestId('project-select-create');
    expect(createItem.textContent).toContain('使用「杭州」作为新项目');
  });

  it('未开启 allowCustom：无匹配时保持原空态文案，不出现新项目条目', async () => {
    const onCustomPick = vi.fn();
    render(<ProjectSelect onCustomPick={onCustomPick} />);
    await openPanel();
    await search('不存在的项目');

    expect(await screen.findByText('未找到匹配项目')).toBeTruthy();
    expect(screen.queryByTestId('project-select-create')).toBeNull();
    expect(onCustomPick).not.toHaveBeenCalled();
  });
});
