// 文件导入（AI 识别）弹层 —— 项目信息树编辑页「文件导入」入口。
// 对照设计稿 components/tree/NodeImportDialog.tsx：选择文件 → 正在识别 → 三组预览勾选确认。
// 实现差异：正文抽取与大模型识别都在后端（POST /info-nodes/projects/{id}/parse-file，
// 摇人同款 DeepSeek flash），前端不引 mammoth/xlsx。
//
// 本弹层只负责「选文件 → 拿三组预览」；预览列表与落库在 ProjectInfoImportPreview
// （与「同步信息」共用，两边的落库行为必须一模一样），这里只多渲染识别来源那两行说明。
import { useRef, useState } from 'react';
import { Popup, Toast } from 'tdesign-mobile-react';
import { MacSparkles, MacUpload } from '@/shared/components/macaronIcons';
import { parseImportFileApi, type ApiImportParseResult } from '@/api/infoNodes';
import type { ProjectInfoNode } from '@/shared/utils/projectInfoTree';
import ProjectInfoImportPreview from './ProjectInfoImportPreview';

export default function ProjectInfoFileImport({ visible, onClose, projectId, nodes, canEditTree, onApplied }: {
  visible: boolean;
  onClose: () => void;
  projectId: string;
  /** 当前项目的全部信息节点（扁平，含刚解析出的匹配目标与归属节点） */
  nodes: ProjectInfoNode[];
  /** 能不能改这棵树（本项目成员或 admin）：决定「未匹配到节点」那组能不能真的建节点 */
  canEditTree: boolean;
  /** 导入落库后回调（调用方重新拉树） */
  onApplied: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [parsing, setParsing] = useState(false);
  const [result, setResult] = useState<ApiImportParseResult | null>(null);

  const close = () => {
    if (parsing) return;
    setFile(null);
    setResult(null);
    onClose();
  };

  const errMsg = (err: unknown, fallback: string) =>
    err instanceof Error && err.message ? err.message : fallback;

  const handleFile = async (picked: File) => {
    setFile(picked);
    setResult(null);
    setParsing(true);
    try {
      setResult(await parseImportFileApi(projectId, picked));
    } catch (err) {
      Toast({ message: errMsg(err, '文件识别失败，请稍后重试'), theme: 'error' });
    } finally {
      setParsing(false);
    }
  };

  return (
    <Popup visible={visible} onClose={close} placement="bottom" showOverlay>
      <div className="mac-sheet">
        <h4 className="mac-sheet__title">文件导入</h4>
        <p className="mac-import__hint">
          支持 Word（.docx）、Markdown（.md）、文本（.txt/.csv）、Excel（.xlsx），由 AI 识别后先预览再确认。
        </p>
        <input
          ref={fileRef}
          type="file"
          accept=".docx,.md,.markdown,.txt,.csv,.xlsx"
          hidden
          onChange={(event) => {
            const selected = event.target.files?.[0];
            event.target.value = '';
            if (selected) void handleFile(selected);
          }}
        />
        <button
          type="button"
          className="mac-btn mac-btn--outline mac-btn--block mac-import__pick"
          disabled={parsing}
          onClick={() => fileRef.current?.click()}
        >
          {parsing ? <MacSparkles size={13} /> : <MacUpload size={13} />}
          {parsing ? '正在识别…' : file?.name || '选择文件'}
        </button>

        {result && (
          <>
            <p className="mac-import__meta">
              识别文件：{result.file_name} · 模型：{result.model}
              {result.truncated ? ' · 内容过长已截断' : ''}
            </p>
            {result.name_mismatch && (
              <div className="mac-import__warn" role="alert">
                文件中识别到的项目名是「{result.file_project_name}」，与当前项目「{result.project_name}」不一致，
                可能导错了文件。请核对文件内容后选择「取消」，或确认无误继续导入。
              </div>
            )}
            <ProjectInfoImportPreview
              result={result}
              projectId={projectId}
              nodes={nodes}
              canEditTree={canEditTree}
              onApplied={onApplied}
              onClose={close}
            />
          </>
        )}
      </div>
    </Popup>
  );
}
