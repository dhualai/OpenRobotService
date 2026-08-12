import { useEffect, useRef, useState } from 'react';
import { API_CONFIG } from '@/config/api';

/**
 * 下载中转页（解决微信内「在浏览器中打开」打开的是工单详情页而非下载链接的问题）。
 *
 * 背景：微信内置 WebView 对 window.location.href = 下载URL 的处理有局限——
 *   后端返回 Content-Disposition: attachment 时微信会拦截下载并回退到原页面，
 *   导致用户点「在浏览器中打开」时 WebView 地址栏仍是工单详情页 URL（/p/app/tasks/354），
 *   外部浏览器打开的也就是工单页而非下载链接。
 *
 * 方案：附件下载先跳到本中转页 /download?path=...&filename=...&token=...，
 *   本页是普通 SPA 页面（URL 不含敏感下载语义，微信不会拦截），
 *   加载后立即 window.location.replace 到真实下载 URL。
 *   用户在微信点「在浏览器中打开」时 WebView 地址栏是 /download?...，
 *   外部浏览器打开本页 → 本页再次 replace 到下载 URL → 触发下载。
 */
export default function DownloadRedirect() {
  const [error, setError] = useState<string>('');
  const triggered = useRef(false);

  useEffect(() => {
    if (triggered.current) return; // 严格单次触发，避免 StrictMode 双调用重复跳转
    triggered.current = true;

    const params = new URLSearchParams(window.location.search);
    const path = params.get('path');
    const filename = params.get('filename') || 'download';
    const token = params.get('token');

    if (!path || !token) {
      setError('下载参数缺失');
      return;
    }

    const origin = window.location.origin;
    const downloadUrl = `${origin}${API_CONFIG.TASKS.BASE_URL}/attachments/download?path=${encodeURIComponent(path)}&filename=${encodeURIComponent(filename)}&token=${encodeURIComponent(token)}`;

    // replace 替换当前历史记录，避免用户点「返回」又回到中转页形成死循环
    window.location.replace(downloadUrl);
  }, []);

  if (error) {
    return (
      <div style={{ padding: 24, textAlign: 'center', color: '#e53935' }}>
        {error}
      </div>
    );
  }

  return (
    <div style={{ padding: 24, textAlign: 'center', color: '#666' }}>
      正在下载，请稍候…
    </div>
  );
}
