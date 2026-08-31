"use client";

import React, { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { getHealth } from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";

export function TopBar() {
  const pathname = usePathname();
  const { mode, toggleMode, canaryChannelName } = useOperatorContext();
  const [healthStatus, setHealthStatus] = useState<string>("loading");

  useEffect(() => {
    let isMounted = true;
    const checkHealth = async () => {
      try {
        const res = await getHealth();
        if (isMounted) {
          setHealthStatus(res.status === "ok" ? "healthy" : "warning");
        }
      } catch {
        if (isMounted) {
          setHealthStatus("danger");
        }
      }
    };

    checkHealth();
    const interval = setInterval(checkHealth, 20000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  const getBreadcrumb = () => {
    if (pathname === "/") return { section: "Overview", page: "System Telemetry" };
    if (pathname === "/channels") return { section: "Fleet", page: "Channels & Workspaces" };
    if (pathname.startsWith("/channels/")) return { section: "Channels", page: canaryChannelName };
    if (pathname === "/missions") return { section: "Orchestration", page: "Missions Registry" };
    if (pathname.startsWith("/missions/")) return { section: "Missions", page: "Mission Inspection" };
    return { section: "Console", page: "Workspace" };
  };

  const breadcrumb = getBreadcrumb();

  return (
    <header className="app-topbar">
      <div className="topbar-left">
        <div className="topbar-breadcrumb">
          <span className="topbar-breadcrumb-item">{breadcrumb.section}</span>
          <span className="text-muted">/</span>
          <span className="topbar-breadcrumb-item active">{breadcrumb.page}</span>
        </div>
      </div>

      <div className="topbar-right">
        {/* System Health Heartbeat */}
        <div className="topbar-pill" title={`System status: ${healthStatus}`}>
          <span className={`pulse-dot ${healthStatus}`} />
          <span className="text-mono">
            {healthStatus === "healthy" ? "HEALTHY" : healthStatus === "warning" ? "DEGRADED" : healthStatus === "loading" ? "CHECKING" : "OFFLINE"}
          </span>
        </div>

        {/* Autonomy Mode */}
        <div className="topbar-pill">
          <span className="text-muted">MODE:</span>
          <span style={{ color: "var(--accent-secondary)" }}>SUPERVISED</span>
        </div>

        {/* Approvals Counter */}
        <div className="topbar-pill" title="Pending Guardian & Editorial Approvals">
          <span className="text-muted">APPROVALS:</span>
          <span style={{ color: "var(--status-success)" }}>0 READY</span>
        </div>

        {/* Operator vs Development Mode Toggle */}
        <button
          type="button"
          onClick={toggleMode}
          className={`mode-toggle-btn ${mode === "OPERATOR" ? "operator" : "development"}`}
          title={
            mode === "OPERATOR"
              ? "Currently showing only verified Canary and Operational records. Click to view all Development fixtures."
              : "Currently displaying all development and test fixtures. Click to return to clean Operator Mode."
          }
        >
          <span>{mode === "OPERATOR" ? "🛡️ Operator View" : "🛠️ Dev Data Active"}</span>
        </button>
      </div>
    </header>
  );
}
