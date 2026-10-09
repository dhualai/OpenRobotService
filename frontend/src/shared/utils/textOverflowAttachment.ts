/**
 * 讨论区输入超限转剪贴板引用。
 *
 * 一次性粘贴超过 TEXT_INPUT_LIMIT 时，输入框只留工业记号 #clipboard(19行)，
 * 原文写成 clipboard(19行).txt 附件。复制消息时记号一起带走。
 */

export const TEXT_INPUT_LIMIT = 500;

const CLIPBOARD_FILE = /^clipboard\((.+)\)\.txt$/i;

export function isClipboardFile(filename: string): boolean {
  return CLIPBOARD_FILE.test(filename);
}

export function countContentLines(text: string): number {
  if (!text) return 0;
  return text.replace(/\r\n/g, '\n').split('\n').length;
}

/** 附件文件名 → 输入框/复制用的记号 */
export function clipboardLabel(filename: string): string {
  const m = filename.match(CLIPBOARD_FILE);
  return m ? `#clipboard(${m[1]})` : filename;
}

/** 按行数生成不冲突的 #clipboard(N行) 与文件名 */
export function nextClipboardRef(text: string, existingNames: string[]): { token: string; filename: string } {
  const lines = Math.max(1, countContentLines(text));
  const taken = new Set(existingNames.map((n) => n.toLowerCase()));
  let n = 0;
  while (true) {
    const core = n === 0 ? `${lines}行` : `${lines}行-${n + 1}`;
    const filename = `clipboard(${core}).txt`;
    if (!taken.has(filename.toLowerCase())) {
      return { token: `#clipboard(${core})`, filename };
    }
    n += 1;
  }
}

export function makeSpillFile(content: string, name: string): File {
  // 微信/部分 WebView 里 `new File([string])` 会得到 size=0 的空文件，必须走 UTF-8 字节。
  const bytes = new TextEncoder().encode(content);
  const blob = new Blob([bytes], { type: 'text/plain' });
  try {
    const file = new File([blob], name, { type: 'text/plain' });
    if (file.size > 0 || bytes.byteLength === 0) return file;
  } catch {
    /* File 构造失败时退回带 name 的 Blob */
  }
  Object.defineProperty(blob, 'name', { value: name, configurable: true });
  Object.defineProperty(blob, 'lastModified', { value: Date.now(), configurable: true });
  return blob as File;
}

export type SpillResult = {
  /** 输入框留下的内容（#clipboard(N行)） */
  text: string;
  filename: string | null;
  /** 写入附件的完整正文 */
  content: string;
};

/** 超限则整段进附件，输入框只留 #clipboard(N行) */
export function spillOverLimit(
  text: string,
  existingNames: string[],
  limit = TEXT_INPUT_LIMIT,
): SpillResult {
  if (text.length <= limit) {
    return { text, filename: null, content: '' };
  }
  const { token, filename } = nextClipboardRef(text, existingNames);
  return { text: token, filename, content: text };
}

/** 一次性粘贴超限：原文进附件，记号插在光标处（已有短文案则换行接上） */
export function insertSpillName(before: string, token: string, after = ''): string {
  const left = before.replace(/\s*$/, '');
  const joined = left ? `${left}\n${token}` : token;
  return after ? joined + after : joined;
}

/** 从粘贴事件取纯文本（微信里 text/plain 可能为空，要兜底 text） */
export function readClipboardText(e: { clipboardData?: DataTransfer | null }): string {
  const d = e.clipboardData;
  if (!d) return '';
  return (d.getData('text/plain') || d.getData('text') || '').replace(/\r\n/g, '\n');
}

/** 复制时把尚未出现在正文里的剪贴板记号（或普通附件名）追加进去 */
export function appendAttachmentNames(content: string, names: string[]): string {
  const extra = names.map(clipboardLabel).filter((n) => !!n && !content.includes(n));
  if (!extra.length) return content;
  const body = content.trimEnd();
  return [body, ...extra].filter(Boolean).join('\n');
}
