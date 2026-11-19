import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { gotoOrsLogin, orsToken } from "../ors-session";

/**
 * 账号统一后不再有本地登录页：
 * 有摇人吧 token 直接进拓扑页，没有就回摇人吧登录（带 from 便于跳回）。
 */
export function LoginPage() {
  const nav = useNavigate();
  useEffect(() => {
    if (!orsToken()) {
      gotoOrsLogin();
      return;
    }
    api("/api/v1/me")
      .then(() => nav("/usp", { replace: true }))
      .catch(() => gotoOrsLogin());
  }, [nav]);
  return (
    <div className="login-body">
      <div className="login-card">
        <h1>功能拓扑</h1>
        <p className="sub">正在跳转摇人吧登录…</p>
      </div>
    </div>
  );
}
