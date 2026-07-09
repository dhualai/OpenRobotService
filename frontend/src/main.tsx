import React from 'react';
import ReactDOM from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { router } from '@/router';
import 'tdesign-mobile-react/es/style/index.css';
import '@/shared/styles/global.css';

// 初始化认证状态
import { useAuthStore } from '@/stores/auth';
useAuthStore.getState().checkLoginStatus();

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>
);
