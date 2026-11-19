import { FormEvent, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { MigrationBanner } from "../migrate";
import { resetSession } from "../session";

export function LoginPage() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [err, setErr] = useState("");
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => api<{ ok: boolean; migrated_url?: string }>("/api/v1/health"),
    retry: false,
  });
  useEffect(() => {
    api("/api/v1/me")
      .then(() => nav("/usp", { replace: true }))
      .catch(() => undefined);
  }, [nav]);
  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    setErr("");
    try {
      await api("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({
          username: String(fd.get("username") || ""),
          password: String(fd.get("password") || ""),
        }),
      });
      resetSession(qc);
      nav("/usp");
    } catch {
      setErr("登录失败");
    }
  }
  return (
    <div className="login-body">
      <form className="login-card" onSubmit={onSubmit}>
        <MigrationBanner url={health.data?.migrated_url} />
        <h1>功能拓扑</h1>
        <p className="sub">使用账号登录</p>
        {err ? <p className="err">{err}</p> : null}
        <label htmlFor="user">账号</label>
        <input id="user" name="username" autoComplete="username" required autoFocus />
        <label htmlFor="pass">密码</label>
        <input id="pass" name="password" type="password" autoComplete="current-password" required />
        <button type="submit">登录</button>
      </form>
    </div>
  );
}
