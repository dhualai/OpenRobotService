import { describe, it, expect, beforeEach } from 'vitest';
import { currentShareUrl, setShareMeta, shareImageUrl, SHARE_SITE_NAME } from '../shareMeta';

/** 读 og 标签内容 */
function og(key: string): string | null {
  return document.head.querySelector(`meta[property="${key}"]`)?.getAttribute('content') ?? null;
}

/** 读 name 标签内容 */
function named(key: string): string | null {
  return document.head.querySelector(`meta[name="${key}"]`)?.getAttribute('content') ?? null;
}

describe('转发卡片元信息（ORS-1086）', () => {
  beforeEach(() => {
    document.head.innerHTML = '';
    document.title = '';
  });

  it('写入标题、描述与 logo 缩略图', () => {
    setShareMeta({ title: '高价值问题：AGV 定位漂移', description: '悬赏 ¥100.00 · 12 人也遇到' });

    expect(document.title).toBe('高价值问题：AGV 定位漂移');
    expect(og('og:title')).toBe('高价值问题：AGV 定位漂移');
    expect(og('og:description')).toBe('悬赏 ¥100.00 · 12 人也遇到');
    expect(named('description')).toBe('悬赏 ¥100.00 · 12 人也遇到');
    expect(og('og:site_name')).toBe(SHARE_SITE_NAME);
  });

  it('缩略图是绝对地址（企微按绝对地址抓图，相对路径会抓不到）', () => {
    setShareMeta({ title: '摇人吧' });

    const image = og('og:image') ?? '';
    expect(image).toBeTruthy();
    expect(image.startsWith('http')).toBe(true);
    expect(image.endsWith('share-thumb.png')).toBe(true);
  });

  it('卡片链接写入 og:url 与 canonical', () => {
    setShareMeta({ title: '摇人吧', url: 'https://usp.ep-zl.com/p/app/call' });

    expect(og('og:url')).toBe('https://usp.ep-zl.com/p/app/call');
    expect(document.head.querySelector('link[rel="canonical"]')?.getAttribute('href'))
      .toBe('https://usp.ep-zl.com/p/app/call');
  });

  it('重复调用只更新内容，不堆叠重复标签', () => {
    setShareMeta({ title: '第一版', description: '描述一' });
    setShareMeta({ title: '第二版', description: '描述二' });

    expect(document.head.querySelectorAll('meta[property="og:title"]').length).toBe(1);
    expect(og('og:title')).toBe('第二版');
    expect(og('og:description')).toBe('描述二');
    expect(document.title).toBe('第二版');
  });

  it('描述为空时清掉上一段描述，不留旧文案', () => {
    setShareMeta({ title: '第一版', description: '旧描述' });
    setShareMeta({ title: '第二版' });

    expect(og('og:description')).toBeNull();
    expect(named('twitter:description')).toBeNull();
  });

  it('标题为空时不动已有标题（路由切换瞬间不把卡片标题清空）', () => {
    setShareMeta({ title: '保留我' });
    setShareMeta({ title: '   ' });

    expect(document.title).toBe('保留我');
    expect(og('og:title')).toBe('保留我');
  });

  it('当前页面地址不含 hash（微信按 URL 匹配，hash 不参与）', () => {
    expect(currentShareUrl()).not.toContain('#');
  });

  it('默认缩略图按当前站点推导，生产/测试/本地各适配自己的构建前缀', () => {
    const image = shareImageUrl();

    expect(image.startsWith('http')).toBe(true);
    expect(image.endsWith('/share-thumb.png')).toBe(true);
  });
});
