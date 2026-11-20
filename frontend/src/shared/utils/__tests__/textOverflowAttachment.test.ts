import { describe, expect, it } from 'vitest';
import {
  TEXT_INPUT_LIMIT,
  appendAttachmentNames,
  clipboardLabel,
  countContentLines,
  insertSpillName,
  isClipboardFile,
  makeSpillFile,
  nextClipboardRef,
  readClipboardText,
  spillOverLimit,
} from '../textOverflowAttachment';

describe('countContentLines', () => {
  it('按换行计行', () => {
    expect(countContentLines('a')).toBe(1);
    expect(countContentLines('a\nb\nc')).toBe(3);
  });
});

describe('nextClipboardRef', () => {
  it('按行数生成记号', () => {
    const body = Array.from({ length: 19 }, (_, i) => `L${i}`).join('\n');
    const r = nextClipboardRef(body, []);
    expect(r.token).toBe('#clipboard(19行)');
    expect(r.filename).toBe('clipboard(19行).txt');
  });

  it('同行数已占用则加序号', () => {
    const body = 'a\nb';
    const r = nextClipboardRef(body, ['clipboard(2行).txt']);
    expect(r.token).toBe('#clipboard(2行-2)');
    expect(r.filename).toBe('clipboard(2行-2).txt');
  });
});

describe('clipboardLabel', () => {
  it('文件名转回记号', () => {
    expect(clipboardLabel('clipboard(19行).txt')).toBe('#clipboard(19行)');
    expect(clipboardLabel('shot.png')).toBe('shot.png');
  });
});

describe('isClipboardFile', () => {
  it('识别剪贴板附件', () => {
    expect(isClipboardFile('clipboard(207行).txt')).toBe(true);
    expect(isClipboardFile('log.zip')).toBe(false);
  });
});

describe('spillOverLimit', () => {
  it('未超限不拆附件', () => {
    const r = spillOverLimit('短文本', []);
    expect(r.filename).toBeNull();
    expect(r.text).toBe('短文本');
    expect(r.content).toBe('');
  });

  it('超限则整段进附件，输入框只留记号', () => {
    const raw = Array.from({ length: 12 }, (_, i) => `行${i}`).join('\n') + 'x'.repeat(TEXT_INPUT_LIMIT);
    const r = spillOverLimit(raw, []);
    expect(r.text).toBe('#clipboard(12行)');
    expect(r.filename).toBe('clipboard(12行).txt');
    expect(r.content).toBe(raw);
  });
});

describe('insertSpillName', () => {
  it('空输入框只留记号', () => {
    expect(insertSpillName('', '#clipboard(19行)')).toBe('#clipboard(19行)');
  });

  it('已有短文案时换行接上记号', () => {
    expect(insertSpillName('帮我看下日志', '#clipboard(19行)')).toBe('帮我看下日志\n#clipboard(19行)');
  });
});

describe('makeSpillFile', () => {
  it('文件字节数等于正文 UTF-8 长度，不能是空文件', () => {
    const body = '日志内容'.repeat(80);
    const f = makeSpillFile(body, 'clipboard(19行).txt');
    expect(f.name).toBe('clipboard(19行).txt');
    expect(f.size).toBe(new TextEncoder().encode(body).byteLength);
    expect(f.size).toBeGreaterThan(0);
  });
});

describe('readClipboardText', () => {
  it('text/plain 为空时回退到 text', () => {
    const dt = {
      getData: (type: string) => (type === 'text' ? 'hello\r\nworld' : ''),
    } as DataTransfer;
    expect(readClipboardText({ clipboardData: dt })).toBe('hello\nworld');
  });
});

describe('appendAttachmentNames', () => {
  it('正文已含记号则不重复追加', () => {
    expect(appendAttachmentNames('#clipboard(19行)', ['clipboard(19行).txt'])).toBe('#clipboard(19行)');
  });

  it('复制时补上尚未出现的记号', () => {
    expect(appendAttachmentNames('#clipboard(19行)', ['clipboard(19行).txt', 'clipboard(8行).txt']))
      .toBe('#clipboard(19行)\n#clipboard(8行)');
  });
});
