import type { ReactNode } from 'react';
import { ConfigProvider, type ThemeConfig } from 'antd';

const learningTheme: ThemeConfig = {
  token: {
    colorPrimary: '#3697c3', colorPrimaryHover: '#56b1da', colorPrimaryActive: '#227197',
    colorPrimaryBg: '#dff8ff', colorPrimaryBgHover: '#c1f4ff', colorPrimaryBorder: '#93e0ff',
    colorInfo: '#3697c3', colorInfoBg: '#dff8ff', colorInfoBorder: '#93e0ff',
    colorSuccess: '#3697c3', colorSuccessBg: '#dff8ff', colorSuccessBorder: '#93e0ff',
    colorWarning: '#56b1da', colorWarningBg: '#dff8ff', colorWarningBorder: '#93e0ff',
    colorLink: '#3697c3', colorLinkHover: '#56b1da', colorLinkActive: '#227197',
    controlItemBgActive: '#dff8ff', controlItemBgActiveHover: '#c1f4ff',
  },
  components: {
    Button: { primaryColor: '#fff', fontWeight: 600, defaultColor: '#227197', defaultBorderColor: '#93e0ff' },
    Tag: { defaultBg: '#dff8ff', defaultColor: '#227197' },
  },
};

export default function LearningTheme({ children }: { children: ReactNode }) {
  return <ConfigProvider theme={learningTheme}>{children}</ConfigProvider>;
}
