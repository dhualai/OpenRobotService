/**
 * 微信经典表情包（卡通小黄脸）清单与渲染辅助。
 *
 * 资源：frontend/src/shared/emoji/wechat/NN.png（75 张，经 Vite 资产管线
 * 打包，自动带部署 base 前缀与哈希，dev/prod 路径均正确）。
 * 来源：github.com/xxk8/wechat-emojis（face 分类），内部系统使用。
 *
 * 存储约定：评论 content 存纯文本 shortcode（如「干得漂亮[微笑]」），
 * 渲染层把 [表情名] 替换为 Markdown 图片语法 → MarkdownRenderer 直渲 <img>。
 * 好处：DB 无需改动、历史消息纯文本可读、别的端不认识 shortcode 时也能看到文字。
 */
const modules = import.meta.glob('./wechat/*.png', {
  eager: true,
  query: '?url',
  import: 'default',
}) as Record<string, string>;

export interface WechatEmoji {
  /** shortcode 名称（不含方括号），如「微笑」 */
  code: string;
  /** 打包后的图片 URL */
  url: string;
}

/** 面板展示顺序（高频在前），与下载时的文件序号一一对应 */
const CODES = [
  '微笑', '憨笑', '偷笑', '破涕为笑', '呲牙', '笑脸', '愉快', '坏笑', '调皮', '捂脸',
  '旺柴', '好的', '666', '耶', '鼓掌', '加油', '再见', '机智', '嘿哈', '吃瓜',
  '让我看看', '裂开', '苦涩', '叹气', '无语', '发呆', '色', '害羞', '脸红', '亲亲',
  '可怜', '委屈', '快哭了', '流泪', '大哭', '失望', '难过', '衰', '困', '睡',
  '汗', '擦汗', '生病', '晕', '惊恐', '恐惧', '惊讶', '天啊', '哇', '疑问',
  '皱眉', '白眼', '翻白眼', '撇嘴', '闭嘴', '嘘', '发怒', '咒骂', '抓狂', '吐',
  '打脸', '抠鼻', '敲打', '骷髅', '阴险', '鄙视', '傲慢', '得意', '悠闲', '尴尬',
  '囧', '奸笑', '社会社会', '右哼哼', 'Emm',
];

export const WECHAT_EMOJIS: WechatEmoji[] = CODES.map((code, i) => ({
  code,
  url: modules[`./wechat/${String(i + 1).padStart(2, '0')}.png`] ?? '',
})).filter((e) => e.url);

/** code → url 快查表 */
const URL_BY_CODE = new Map(WECHAT_EMOJIS.map((e) => [e.code, e.url]));
/** url 集合：MarkdownRenderer 据此识别表情图并直渲（不走 AuthImage 鉴权管线） */
export const WECHAT_EMOJI_URL_SET = new Set(WECHAT_EMOJIS.map((e) => e.url));

/** `[表情名]` 匹配正则（按 code 长度降序拼接，避免「快哭了」被「哭」抢先命中） */
const SHORTCODE_RE = new RegExp(
  `\\[(${[...URL_BY_CODE.keys()].sort((a, b) => b.length - a.length)
    .map((c) => c.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('|')})\\]`,
  'g',
);

/**
 * 把文本中的 [表情名] shortcode 替换为 Markdown 图片语法。
 * 未命中集合的方括号文本原样保留（不影响普通文字）。
 */
export function replaceWechatEmoji(text: string): string {
  if (!text || !text.includes('[')) return text;
  return text.replace(SHORTCODE_RE, (full, code: string) => {
    const url = URL_BY_CODE.get(code);
    return url ? `![${code}](${url})` : full;
  });
}

/** 整条消息仅含一个 shortcode（与 WeChat 单发表情同尺寸） */
export function parseStandaloneEmoji(text: string): { code: string; url: string } | null {
  const m = /^\s*\[([^\]]+)\]\s*$/.exec(text || '');
  if (!m) return null;
  const url = URL_BY_CODE.get(m[1]);
  return url ? { code: m[1], url } : null;
}
