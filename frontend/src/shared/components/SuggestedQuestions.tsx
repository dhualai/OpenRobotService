// 「猜你想问」—— 主流 AI 引导对话框式建议问题悬浮框
// 场景：进入「我要摇人」对话框且无输入时随机推荐；输入内容时按关键字检索匹配。
// 交互：点击任一条目，该问题立即作为一条用户提问消息渲染进会话框（由父组件接管 send）。
interface SuggestedQuestionsProps {
  questions: string[];
  title?: string;
  onPick: (question: string) => void;
  /** 传入则显示「换一批」（仅空输入的随机推荐场景） */
  onRefresh?: () => void;
}

export default function SuggestedQuestions({
  questions,
  title = '猜你想问',
  onPick,
  onRefresh,
}: SuggestedQuestionsProps) {
  if (!questions.length) return null;

  return (
    <div className="suggested-questions" role="listbox" aria-label={title}>
      <div className="suggested-questions__header">
        <span className="suggested-questions__title">
          <svg
            className="suggested-questions__title-icon"
            viewBox="0 0 24 24"
            width="14"
            height="14"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M9 18h6" />
            <path d="M10 22h4" />
            <path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V17h6v-.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z" />
          </svg>
          {title}
        </span>
        {onRefresh && (
          <button
            type="button"
            className="suggested-questions__refresh"
            onClick={onRefresh}
            aria-label="换一批"
          >
            <svg
              viewBox="0 0 24 24"
              width="13"
              height="13"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="23 4 23 10 17 10" />
              <polyline points="1 20 1 14 7 14" />
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
            </svg>
            换一批
          </button>
        )}
      </div>
      <div className="suggested-questions__list">
        {questions.map((q) => (
          <button
            key={q}
            type="button"
            className="suggested-questions__item"
            role="option"
            aria-selected="false"
            onClick={() => onPick(q)}
          >
            <span className="suggested-questions__item-text">{q}</span>
            <svg
              className="suggested-questions__item-arrow"
              viewBox="0 0 24 24"
              width="16"
              height="16"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>
        ))}
      </div>
    </div>
  );
}
