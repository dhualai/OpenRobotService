import { Suspense } from 'react';
import { Outlet } from 'react-router-dom';
import { Loading } from 'tdesign-mobile-react';

export default function App() {
  return (
    <Suspense fallback={<Loading text="加载中..." />}>
      <Outlet />
    </Suspense>
  );
}
