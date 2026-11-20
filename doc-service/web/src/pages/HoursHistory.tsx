import { Link, Navigate, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { HoursReadBar, fmtHours, personName } from "../hours-bar";

type Feat = { id: string; title: string; version: string; hours: number };
type Person = { user: string; name?: string; hours: number; features: Feat[] };
type Week = { week: string; open: boolean; total: number; by_user: Person[]; start?: string; end?: string };
type Hist = { week: string; cap?: number; items: Week[] };

function md(iso: string | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || "");
  if (!m) return "";
  return `${Number(m[2])}.${Number(m[3])}`;
}

function weekDates(w: Week): string {
  const a = md(w.start);
  const b = md(w.end);
  return a && b ? `${a}–${b}` : "";
}

function People({ product, people, cap }: { product: string; people: Person[]; cap: number }) {
  return (
    <ul className="hrs-people">
      {people.map((p) => (
        <li key={p.user} className="hrs-person">
          <div className="hrs-person-h">
            <span className="hrs-who">{personName(p)}</span>
          </div>
          {(p.features || []).map((f) => (
            <span key={`${f.id}|${f.version}`} className="hrs-feat">
              <Link to={`/${product}/m/${encodeURIComponent(f.id)}?v=${encodeURIComponent(f.version)}`}>
                {f.title}
                {f.version ? <span className="hrs-ver">{f.version}</span> : null}
              </Link>
              <HoursReadBar hours={f.hours} max={cap} />
              <span className="hrs-n">{fmtHours(f.hours)}</span>
            </span>
          ))}
        </li>
      ))}
    </ul>
  );
}

function WeekLabel({ week, current }: { week: string; current?: boolean }) {
  return (
    <span className="hrs-wk">
      {current ? "本周" : null}
      {current ? <span className="hrs-wk-id">{week}</span> : week}
    </span>
  );
}

export function HoursLegacyRedirect() {
  const { product = "usp" } = useParams();
  return <Navigate to={`/${product}/hours/history`} replace />;
}

export function HoursHistoryPage() {
  const { product = "usp" } = useParams();
  const q = useQuery({
    queryKey: ["hours-hist", product],
    queryFn: () => api<Hist>(`/api/v1/products/${product}/hours/history`),
  });
  if (q.isLoading) return <p>…</p>;
  if (q.isError) return <p className="err">无法加载</p>;
  const week = q.data?.week || "";
  const cap = q.data?.cap && q.data.cap > 0 ? q.data.cap : 40;
  const items = q.data?.items || [];
  const now = items.find((w) => w.open);
  const past = items.filter((w) => !w.open);
  return (
    <div className="panel hrs-stat">
      <div className="page-head">
        <h1>工时统计</h1>
        {week && !now ? <span className="meta">{week}</span> : null}
      </div>
      {!items.length ? (
        <p className="empty">暂无工时</p>
      ) : (
        <>
          {now ? (
            <section className="hrs-now">
              <header className="hrs-h">
                <h2>
                  <WeekLabel week={now.week} current />
                </h2>
                {weekDates(now) ? <span className="hrs-dates">{weekDates(now)}</span> : null}
              </header>
              <People product={product} people={now.by_user} cap={cap} />
            </section>
          ) : null}
          {past.length ? (
            <div className="hrs-past">
              {past.map((w) => (
                <details key={w.week} className="hrs-week">
                  <summary>
                    <WeekLabel week={w.week} />
                    {weekDates(w) ? <span className="hrs-dates">{weekDates(w)}</span> : null}
                  </summary>
                  <People product={product} people={w.by_user} cap={cap} />
                </details>
              ))}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
