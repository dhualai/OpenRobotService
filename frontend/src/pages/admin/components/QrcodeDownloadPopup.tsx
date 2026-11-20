import { useState } from 'react';
import { Popup, Toast } from 'tdesign-mobile-react';
import { downloadQrcodes, type QrcodeDownloadParams } from '@/api/qrcode';

interface Props {
  selectedIds: number[];
  pageIds: number[];
  filteredTotal: number;
  filters: Pick<QrcodeDownloadParams, 'status' | 'qrcode_type' | 'keyword' | 'date_start' | 'date_end' | 'date_field'>;
  onClose: () => void;
}

export default function QrcodeDownloadPopup({ selectedIds, pageIds, filteredTotal, filters, onClose }: Props) {
  const [scope, setScope] = useState<QrcodeDownloadParams['scope']>(selectedIds.length ? 'selected' : 'filtered');
  const [idStart, setIdStart] = useState('');
  const [idEnd, setIdEnd] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const params: QrcodeDownloadParams = { scope };
    if (scope === 'selected' || scope === 'page') {
      params.qid_list = scope === 'selected' ? selectedIds : pageIds;
      if (!params.qid_list.length) {
        Toast({ message: '所选范围内没有二维码' });
        return;
      }
      if (params.qid_list.length > 500) {
        Toast({ message: '单次最多下载 500 条，请缩小下载范围' });
        return;
      }
    } else if (scope === 'filtered') {
      Object.assign(params, filters);
    } else if (scope === 'range') {
      params.id_start = Number(idStart);
      params.id_end = Number(idEnd);
      if (!Number.isSafeInteger(params.id_start) || !Number.isSafeInteger(params.id_end)
        || params.id_start < 1 || params.id_end < params.id_start) {
        Toast({ message: '请填写有效的起止 ID，起始 ID 不能大于结束 ID' });
        return;
      }
    }
    setBusy(true);
    try {
      const blob = await downloadQrcodes(params);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `二维码_${new Date().toISOString().replace(/[:.]/g, '-')}.zip`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
      Toast({ message: '已开始下载，跳过或失败的记录请查看压缩包内的下载说明' });
      onClose();
    } catch (error) {
      Toast({ message: error instanceof Error ? error.message : '批量下载失败，请重试' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Popup visible placement="bottom" onVisibleChange={(visible) => { if (!visible && !busy) onClose(); }}>
      <div className="qr-popup">
        <h3>批量下载二维码</h3>
        <p className="qr-popup-hint">打包为 ZIP，单次最多 500 条。未生成 ticket 的记录自动跳过，不会生成或修改二维码。</p>
        <label className="qr-field" htmlFor="qr-download-scope">下载范围</label>
        <select id="qr-download-scope" className="qr-input" value={scope} disabled={busy}
          onChange={(event) => setScope(event.target.value as QrcodeDownloadParams['scope'])}>
          <option value="selected" disabled={!selectedIds.length}>已勾选记录（{selectedIds.length} 条，含跨页勾选）</option>
          <option value="page" disabled={!pageIds.length}>当前页（{pageIds.length} 条）</option>
          <option value="filtered" disabled={!filteredTotal}>当前筛选结果（{filteredTotal} 条，含所有分页）</option>
          <option value="range">指定 ID 区间</option>
          <option value="all">全部记录（忽略当前筛选）</option>
        </select>
        {scope === 'range' && (
          <div className="qr-row">
            <div className="qr-field-group">
              <label className="qr-field" htmlFor="qr-download-start">起始 ID（包含）</label>
              <input id="qr-download-start" className="qr-input" type="number" min="1" step="1"
                value={idStart} disabled={busy} onChange={(event) => setIdStart(event.target.value)} />
            </div>
            <div className="qr-field-group">
              <label className="qr-field" htmlFor="qr-download-end">结束 ID（包含）</label>
              <input id="qr-download-end" className="qr-input" type="number" min="1" step="1"
                value={idEnd} disabled={busy} onChange={(event) => setIdEnd(event.target.value)} />
            </div>
          </div>
        )}
        {scope === 'range' && <p className="qr-popup-hint">按二维码 ID 下载，不受当前状态、类型和搜索条件影响。</p>}
        <div className="qr-popup-actions">
          <button className="qr-btn qr-btn--ghost" disabled={busy} onClick={onClose}>取消</button>
          <button className="qr-btn qr-btn--primary" disabled={busy} onClick={submit}>
            {busy ? '正在打包，请稍候…' : '下载 ZIP'}
          </button>
        </div>
      </div>
    </Popup>
  );
}
