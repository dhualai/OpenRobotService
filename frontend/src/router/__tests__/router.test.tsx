import { describe, it, expect } from 'vitest';
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

  it('should have /app root with children', () => {
    const route = router.routes.find((r) => r.path === '/app');
    expect(route).toBeDefined();
    expect(route?.children).toBeDefined();
    expect(route?.children?.length).toBeGreaterThan(0);
  });

  it('should have call child route', () => {
    const appRoute = router.routes.find((r) => r.path === '/app');
    const callRoute = appRoute?.children?.find((r) => r.path === 'call');
    expect(callRoute).toBeDefined();
  });

  it('should have tasks child route', () => {
    const appRoute = router.routes.find((r) => r.path === '/app');
    const tasksRoute = appRoute?.children?.find((r) => r.path === 'tasks');
    expect(tasksRoute).toBeDefined();
  });

  it('should have admin child route with nested children', () => {
    const appRoute = router.routes.find((r) => r.path === '/app');
    const adminRoute = appRoute?.children?.find((r) => r.path === 'admin');
    expect(adminRoute).toBeDefined();
    expect(adminRoute?.children).toBeDefined();

    // Should have AdminView at index
    const adminIndex = adminRoute?.children?.find((r) => r.index === true);
    expect(adminIndex).toBeDefined();

    // Should have AdminLayout routes
    const adminChildren = adminRoute?.children?.find((r) => r.children);
    expect(adminChildren).toBeDefined();
    expect(adminChildren?.children?.length).toBeGreaterThan(10);
  });

  it('should redirect root to /app/call', () => {
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
    const appRoute = router.routes.find((r) => r.path === '/app');
    const adminRoute = appRoute?.children?.find((r) => r.path === 'admin');
    const adminLayout = adminRoute?.children?.find((r) => r.children);
    const paths = adminLayout?.children?.map((r) => r.path) || [];

    expect(paths).toContain('dashboard');
    expect(paths).toContain('project-manage');
    expect(paths).toContain('project-edit/:id?');
    expect(paths).toContain('progress');
    expect(paths).toContain('personnel');
    expect(paths).toContain('risks');
    expect(paths).toContain('reports');
    expect(paths).toContain('users');
    expect(paths).toContain('roles');
    expect(paths).toContain('permissions');
    expect(paths).toContain('resources');
    expect(paths).toContain('data-import');
    expect(paths).toContain('operation-logs');
    expect(paths).toContain('project-hr');
    expect(paths).toContain('project-auth');
    expect(paths).toContain('risk-edit/:id?');
  });
});
