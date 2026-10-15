import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./styles.css";
import { LoginPage } from "./pages/Login";
import { TopologyPage } from "./pages/Topology";
import { ModulePage } from "./pages/Module";
import { AppShell } from "./pages/Shell";
import { RequirementsPage } from "./pages/Requirements";
import { RequirementDetailPage } from "./pages/RequirementDetail";
import { ReleasesPage } from "./pages/Releases";
import { EditorPage } from "./pages/Editor";
import { AuditPage } from "./pages/Audit";
import { VisitsPage } from "./pages/Visits";
import { HoursHistoryPage, HoursLegacyRedirect } from "./pages/HoursHistory";
import { AccountPage } from "./pages/Account";
import { UsersPage } from "./pages/Users";
import { DraftsPage } from "./pages/Drafts";
import { DraftDetailPage } from "./pages/DraftDetail";

const qc = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 15_000, refetchOnWindowFocus: false },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<AppShell />}>
            <Route path="/:product" element={<TopologyPage />} />
            <Route path="/:product/m/:id" element={<ModulePage />} />
            <Route path="/:product/hours" element={<HoursLegacyRedirect />} />
            <Route path="/:product/hours/history" element={<HoursHistoryPage />} />
            <Route path="/:product/account" element={<AccountPage />} />
            <Route path="/:product/users" element={<UsersPage />} />
            <Route path="/:product/drafts" element={<DraftsPage />} />
            <Route path="/:product/drafts/:id" element={<DraftDetailPage />} />
            <Route path="/:product/requirements" element={<RequirementsPage />} />
            <Route path="/:product/requirements/:id" element={<RequirementDetailPage />} />
            <Route path="/:product/releases" element={<ReleasesPage />} />
            <Route path="/:product/editor" element={<EditorPage />} />
            <Route path="/:product/audit" element={<AuditPage />} />
            <Route path="/:product/visits" element={<VisitsPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/usp" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
