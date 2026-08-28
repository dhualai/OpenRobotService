/**
 * 微信经典表情包（卡通小黄脸）清单与渲染辅助。
 *
 * 资源：frontend/src/shared/emoji/wechat/NNN.png（109 张，经 Vite 资产管线
 * 打包，自动带部署 base 前缀与哈希，dev/prod 路径均正确）。
 * 来源：github.com/airinghost/wechat-emoji（web.wechat.com/uncompressed，
 * 官方微信默认表情面板 109 个，文件名含面板顺序序号），内部系统使用。
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

/** 面板展示顺序，与官方微信默认表情面板一致（web.wechat.com，109 个） */
const CODES = [
  '微笑', '撇嘴', '色', '发呆', '得意', '流泪', '害羞', '闭嘴', '睡', '大哭',
  '尴尬', '发怒', '调皮', '呲牙', '惊讶', '难过', '囧', '抓狂', '吐', '偷笑',
  '愉快', '白眼', '傲慢', '困', '惊恐', '憨笑', '悠闲', '咒骂', '疑问', '嘘',
  '晕', '衰', '骷髅', '敲打', '再见', '擦汗', '抠鼻', '鼓掌', '坏笑', '右哼哼',
  '鄙视', '委屈', '快哭了', '阴险', '亲亲', '可怜', '笑脸', '生病', '脸红', '破涕为笑',
  '恐惧', '失望', '无语', '嘿哈', '捂脸', '奸笑', '机智', '皱眉', '耶', '吃瓜',
  '加油', '汗', '天啊', 'Emm', '社会社会', '旺柴', '好的', '打脸', '哇', '翻白眼',
  '666', '让我看看', '叹气', '苦涩', '裂开', '嘴唇', '爱心', '心碎', '拥抱', '强',
  '弱', '握手', '胜利', '抱拳', '勾引', '拳头', 'OK', '合十', '啤酒', '咖啡',
  '蛋糕', '玫瑰', '凋谢', '菜刀', '炸弹', '便便', '月亮', '太阳', '庆祝', '礼物',
  '红包', '發', '福', '烟花', '爆竹', '猪头', '跳跳', '发抖', '转圈',
];

export const WECHAT_EMOJIS: WechatEmoji[] = CODES.map((code, i) => ({
  code,
  url: modules[`./wechat/${String(i + 1).padStart(3, '0')}.png`] ?? '',
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
