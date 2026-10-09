import { describe, expect, it } from 'vitest';
import {
  attachmentPreviewKind,
  basenameOf,
  commentFileProxyUrl,
  extOf,
} from '../attachmentPreview';

describe('extOf / basenameOf', () => {
  it('从 clipboard(207行).txt 取到 txt', () => {
    expect(extOf('clipboard(207行).txt')).toBe('txt');
    expect(basenameOf('helpdesk-comment/tmp/clipboard(207行).txt')).toBe('clipboard(207行).txt');
  });

  it('解码百分号文件名', () => {
    expect(basenameOf('bucket/tmp/clipboard%28207%E8%A1%8C%29.txt')).toBe('clipboard(207行).txt');
  });
});

describe('attachmentPreviewKind', () => {
  it('clipboard txt 走文本预览', () => {
    expect(attachmentPreviewKind('clipboard(207行).txt')).toBe('text');
  });

  it('#clipboard 记号也走文本预览', () => {
    expect(attachmentPreviewKind('#clipboard(207行)')).toBe('text');
  });

  it('markdown 链接截断的 clipboard(207行 仍按正文打开', () => {
    expect(attachmentPreviewKind('clipboard(207行)')).toBe('text');
    expect(attachmentPreviewKind('clipboard%28207%E8%A1%8C%29.txt')).toBe('text');
  });

  it('从代理 URL 识别 clipboard 文件', () => {
    expect(
      attachmentPreviewKind(
        'file',
        '/api/tasks/files/helpdesk-comment/tmp/clipboard(207行).txt',
      ),
    ).toBe('text');
  });

  it('md 走 markdown 预览', () => {
    expect(attachmentPreviewKind('方案.md')).toBe('md');
    expect(attachmentPreviewKind('readme.markdown')).toBe('md');
  });

  it('普通 txt 走文本预览', () => {
    expect(attachmentPreviewKind('log.txt')).toBe('text');
  });
});

describe('commentFileProxyUrl', () => {
  it('按段编码文件名里的括号和中文', () => {
    const url = commentFileProxyUrl(
      '/api/tasks',
      'helpdesk-comment/tmp/clipboard(207行).txt',
    );
    expect(url).toBe(
      `/api/tasks/files/helpdesk-comment/tmp/${encodeURIComponent('clipboard(207行).txt').replace(/\(/g, '%28').replace(/\)/g, '%29')}`,
    );
    expect(url).not.toContain('(');
    expect(url).not.toContain(')');
  });
});
