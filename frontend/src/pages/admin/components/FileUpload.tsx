// 文件上传组件
import { Upload, Toast } from 'tdesign-mobile-react';
import { createRequest } from '@/api/client';
import API_CONFIG from '@/config/api';

interface FileUploadProps {
  onSuccess?: (result: unknown) => void;
  onError?: (err: Error) => void;
  accept?: string;
  maxSize?: number;
}

export default function FileUpload({ onSuccess, onError, accept = '.csv,.xlsx,.xls', maxSize = 10 }: FileUploadProps) {
  const request = createRequest(API_CONFIG.PROJECT.BASE_URL, 'Project');

  const handleUpload = async (file: File) => {
    const formData = new FormData();
    formData.append('file', file);
    try {
      const result = await request('/data/import/file', { method: 'POST', body: formData });
      Toast({ message: '上传成功', theme: 'success' });
      onSuccess?.(result);
    } catch (err) {
      const error = err instanceof Error ? err : new Error('上传失败');
      Toast({ message: error.message, theme: 'error' });
      onError?.(error);
    }
  };

  return (
    <Upload
      accept={accept}
      max={1}
      onSuccess={({ fileList }) => {
        if (fileList?.[0]?.raw) handleUpload(fileList[0].raw);
      }}
    />
  );
}
