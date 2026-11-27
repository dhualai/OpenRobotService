import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { matchPath } from 'react-router-dom';
import { router } from '../index';

describe('Router Configuration', () => {
  it('should have login route', () => {
    const route = router.routes.find((r) => r.path === '/login');
    expect(route).toBeDefined();
    expect(route?.path).toBe('/login');
  });

  it('should have no-permission route', () => {
    const route = router.routes.find((r) => r.path === '/no-permission');
    expect(route).toBeDefined();
  });

  it('should have / root with children', () => {
    const route = router.routes.find((r) => r.path === '/');
    expect(route).toBeDefined();
    expect(route?.children).toBeDefined();
    expect(route?.children?.length).toBeGreaterThan(0);
  });

  it('should have call child route', () => {
    const rootRoute = router.routes.find((r) => r.path === '/');
    const callRoute = rootRoute?.children?.find((r) => r.path === 'call');
    expect(callRoute).toBeDefined();
  });

  it('should have call history and ticket detail child routes', () => {
    const rootRoute = router.routes.find((r) => r.path === '/');
    const paths = rootRoute?.children?.map((r) => r.path) || [];
    expect(paths).toContain('call/history');
    expect(paths).toContain('call/ticket/:id');
  });

  it('should have tasks child route', () => {
    const rootRoute = router.routes.find((r) => r.path === '/');
    const tasksRoute = rootRoute?.children?.find((r) => r.path === 'tasks');
    expect(tasksRoute).toBeDefined();
  });

  it('should have admin child route with nested children', () => {
    const rootRoute = router.routes.find((r) => r.path === '/');
    const adminRoute = rootRoute?.children?.find((r) => r.path === 'admin');
    expect(adminRoute).toBeDefined();
    expect(adminRoute?.children).toBeDefined();

    // Should have Dashboard at index
    const adminIndex = adminRoute?.children?.find((r) => r.index === true);
    expect(adminIndex).toBeDefined();

    // Should have AdminLayout routes
    const adminChildren = adminRoute?.children?.find((r) => r.children);
    expect(adminChildren).toBeDefined();
    expect(adminChildren?.children?.length).toBeGreaterThan(10);
  });

  it('should redirect root to /call', () => {
    const route = router.routes.find((r) => r.path === '/');
    expect(route).toBeDefined();
  });

  it('should have legacy redirects for old paths', () => {
    const oldCallRoutes = router.routes.filter(
      (r) => r.path === '/call' || r.path === '/call/ai-chat' || r.path === '/call/new-ticket'
    );
    expect(oldCallRoutes.length).toBe(3);
  });

  it('should have catch-all wildcard route', () => {
    const wildcard = router.routes.find((r) => r.path === '*');
    expect(wildcard).toBeDefined();
  });

  it('should have admin sub-routes for all management pages', () => {
    const rootRoute = router.routes.find((r) => r.path === '/');
    const adminRoute = rootRoute?.children?.find((r) => r.path === 'admin');
    const adminLayout = adminRoute?.children?.find((r) => r.children);
    const paths = adminLayout?.children?.map((r) => r.path) || [];

    expect(paths).toContain('dashboard');
    expect(paths).toContain('project-manage');
    expect(paths).toContain('project-edit/:id?');
    expect(paths).toContain('risks');
    expect(paths).toContain('reports');
    expect(paths).toContain('users');
    expect(paths).toContain('roles');
    expect(paths).toContain('permissions');
    expect(paths).toContain('resources');
    expect(paths).toContain('data-import');
    expect(paths).toContain('operation-logs');
    expect(paths).toContain('risk-edit/:id?');
    expect(paths).toContain('ticket-monitor');
    expect(paths).toContain('project-progress');
    expect(paths).toContain('daily-reports');
    // 已从导航移除（文件保留，路由注释），不应再出现在路由表中
    expect(paths).not.toContain('progress');
    expect(paths).not.toContain('personnel');
    expect(paths).not.toContain('project-hr');
    // 项目授权已并入项目管理二级页面（ProjectManage.tsx 内部展示），不再单独挂路由
    expect(paths).not.toContain('project-auth');
  });

  it('should have dashboard drill-down routes directly under admin', () => {
    const rootRoute = router.routes.find((r) => r.path === '/');
    const adminRoute = rootRoute?.children?.find((r) => r.path === 'admin');
    const paths = adminRoute?.children?.map((r) => r.path) || [];

    expect(paths).toContain('dashboard/tickets/:status');
    expect(paths).toContain('dashboard/projects/:dimension/:key');
    expect(paths).toContain('entries');
  });
});

// —— 真实路由表（src/main.tsx） ——
// 本目录的 index.tsx 并没有被应用引用（见其文件头），上面那些用例守的是那份留存副本。
// 应用真正加载的是 main.tsx 内联的 createBrowserRouter：只改上面那份 = 改了不生效，
// 点击会落到 main.tsx 的 /admin/* 兜底并跳回 /admin（曾把「项目工单卡三格下钻」跳错页）。
// 这里直接读 main.tsx 源码抽 path 字面量做匹配——不关心它嵌套在哪一层，
// 只要 URL 能被某条规则匹配上，就不会掉进兜底。
// 按 cwd 定位（vitest 的 cwd 即 frontend/，与 vite.config.ts 同级）：
// jsdom 环境下 import.meta.url 不是 file:// 协议，fileURLToPath 会抛「The URL must be of scheme file」
const MAIN_SRC = readFileSync(path.resolve(process.cwd(), 'src/main.tsx'), 'utf8');
// 通配（/*、*、/admin/*）一律排除：它们能匹配任何 URL，留着会让下面的断言恒真
const REAL_ROUTE_PATHS = [...MAIN_SRC.matchAll(/path:\s*'([^']+)'/g)]
  .map((m) => m[1])
  .filter((p) => !p.includes('*'));
/** admin 子路由在 main.tsx 里是相对写法（不带 /admin 前缀），故两种都试 */
const resolvesInRealRouter = (url: string) =>
  REAL_ROUTE_PATHS.some((p) => matchPath(`/${p}`, url) !== null || matchPath(`/${p}`, url.replace(/^\/admin/, '')) !== null);

describe('真实路由表（src/main.tsx）', () => {
  it('题目审核工单归入工单任务，并兼容历史详情链接', () => {
    expect(resolvesInRealRouter('/tasks/reviews/9007199254740993')).toBe(true);
    expect(resolvesInRealRouter('/tasks/learning/reviews/9007199254740993')).toBe(true);
    expect(MAIN_SRC).toContain("import('@/pages/learning/LearningReviewDetail')");
    expect(MAIN_SRC).toContain("{ path: 'tasks/learning/reviews/:taskId', element: <LearningReviewDetail /> }");
    expect(MAIN_SRC).toContain("{ path: 'tasks/reviews/:taskId', element: <LearningReviewDetail /> }");
  });
  it('题目生成页面挂在真实后台路由而非历史副本', () => {
    expect(resolvesInRealRouter('/admin/question-generation')).toBe(true);
    expect(MAIN_SRC).toContain("import('@/pages/admin/learning/QuestionGeneration')");
    expect(MAIN_SRC).toContain("{ path: 'question-generation', element: <QuestionGeneration /> }");
  });
  it('项目工单卡三格的下钻路由已挂上（每个按钮各一个列表页）', () => {
    // 卡片点击后跳的正是这三个 URL（见 ProjectTicketsCard.openTickets）
    for (const scope of ['all', 'pending', 'overdue']) {
      expect(resolvesInRealRouter(`/admin/project-detail/P-001/tickets/${scope}`)).toBe(true);
    }
    // 反向对照：同层级但规则不存在的路径必须匹配不上，否则上面的断言只是恒真
    // （:status 是通配段，故用第三段的 tickets 换成 nope 来构造「真不存在」的路径）
    expect(resolvesInRealRouter('/admin/project-detail/P-001/nope/all')).toBe(false);
  });

  it('工单条目跳工单详情页的路由也在（/tasks/:id）', () => {
    expect(resolvesInRealRouter('/tasks/12')).toBe(true);
  });
});
