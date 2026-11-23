export function MigrationBanner({ url }: { url?: string }) {
  const href = (url || "").trim();
  if (!href) return null;
  return (
    <p className="migrate-banner" role="status">
      本站已迁移至 <a href={href}>{href}</a>，请登录新地址更新。
    </p>
  );
}
