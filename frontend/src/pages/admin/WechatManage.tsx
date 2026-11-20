import { useState } from 'react';
import { Tabs, TabPanel } from 'tdesign-mobile-react';
import WechatMenuManage from './WechatMenuManage';
import WechatTagManage from './WechatTagManage';

export default function WechatManage() {
  const [activeTab, setActiveTab] = useState('menu');

  return (
    <div style={{ height: '100%' }}>
      <Tabs value={activeTab} onChange={(v) => setActiveTab(String(v))}>
        <TabPanel value="menu" label="菜单管理">
          <WechatMenuManage />
        </TabPanel>
        <TabPanel value="tag" label="标签管理">
          <WechatTagManage />
        </TabPanel>
      </Tabs>
    </div>
  );
}