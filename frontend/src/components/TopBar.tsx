"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { getHealth } from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import { getProvenanceBadgeLabel } from "@/lib/channel-classification";
import { Breadcrumbs } from "@/components/ui";
import { getBreadcrumbs } from "@/lib/navigation";
import { CommandPalette } from "@/components/CommandPalette";

export function TopBar({ onNavigationToggle, navigationOpen }: { onNavigationToggle?: () => void; navigationOpen?: boolean }) {
  const pathname = usePathname();
  const {
    mode,
    toggleMode,
    selectedChannel,
    channels,
    selectedChannelClassification,
    isSelectedChannelInternal,
  } = useOperatorContext();
  const badgeLabel = getProvenanceBadgeLabel(selectedChannelClassification);
  const [healthStatus, setHealthStatus] = useState<string>("loading");
  const [cmdOpen, setCmdOpen] = useState(false);

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

  // Global Ctrl+K / Cmd+K listener
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCmdOpen((prev) => !prev);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  const routeChannelId = pathname.match(/^\/channels\/([^/]+)/)?.[1];
  const routeChannelName = routeChannelId
    ? channels.find((channel) => channel.id === routeChannelId)?.name
    : selectedChannel?.name;
  const breadcrumbs = getBreadcrumbs(pathname, routeChannelName);

  return (
    <>
      <header className="topbar app-topbar">
        <div className="topbar-left">
          <button
            type="button"
            className="mobile-menu-button icon-btn"
            onClick={onNavigationToggle}
            aria-label={navigationOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={navigationOpen}
          >
            <span aria-hidden="true">{navigationOpen ? "×" : "☰"}</span>
          </button>
          <button
            type="button"
            className="icon-btn cmd-trigger-btn"
            id="cmdBtn"
            onClick={() => setCmdOpen(true)}
            title="Open command palette (Ctrl+K)"
            aria-label="Open command palette"
          >
            ⌘
          </button>
          <div
            className="search topbar-search-bar"
            onClick={() => setCmdOpen(true)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setCmdOpen(true);
              }
            }}
            aria-label="Search OMEGA (Ctrl+K)"
          >
            <span aria-hidden="true">⌕</span>
            <span className="search-placeholder">Search missions, channels, routes...</span>
            <kbd className="kbd">Ctrl+K</kbd>
          </div>
          <div className="topbar-breadcrumbs-wrap">
            <Breadcrumbs items={breadcrumbs} />
          </div>
        </div>

        <div className="top-spacer" />

        <div className="topbar-right">
          {selectedChannel && (
            <Link
              href={`/channels/${selectedChannel.id}`}
              className="pill topbar-channel-pill"
              title={`Workspace: ${selectedChannel.name}${isSelectedChannelInternal ? ` (${selectedChannelClassification})` : ""} (Click to manage)`}
            >
              <span className="pill-prefix">WORKSPACE</span>
              <b>{selectedChannel.name}</b>
              {isSelectedChannelInternal && badgeLabel && (
                <span
                  style={{
                    fontSize: "10px",
                    padding: "1px 5px",
                    borderRadius: "4px",
                    background: "rgba(245, 158, 11, 0.15)",
                    color: "var(--warning, #f59e0b)",
                    border: "1px solid rgba(245, 158, 11, 0.3)",
                    fontWeight: 700,
                    letterSpacing: "0.04em",
                  }}
                  title={`Internal channel (${selectedChannelClassification})`}
                >
                  {badgeLabel}
                </span>
              )}
              <span className="pill-arrow">▾</span>
            </Link>
          )}

          {/* System Health Heartbeat */}
          <div className="pill topbar-health-pill" title={`System status: ${healthStatus}`}>
            <span className={`dot pulse-dot ${healthStatus}`} aria-hidden="true" />
            <span>
              {healthStatus === "healthy" ? "Healthy" : healthStatus === "warning" ? "Degraded" : healthStatus === "loading" ? "Checking" : "Offline"}
            </span>
          </div>

          {/* Mode Toggle */}
          <button
            type="button"
            onClick={toggleMode}
            className={`pill mode-toggle-btn ${mode === "OPERATOR" ? "operator" : "development"}`}
            title={
              mode === "OPERATOR"
                ? "Currently showing operational records for the selected workspace."
                : "Currently displaying all development fixtures."
            }
          >
            <span>{mode === "OPERATOR" ? "Operator" : "Dev data"}</span>
          </button>

          {/* Operator Avatar */}
          <div className="avatar" title="Operator Profile" aria-label="Operator">
            OP
          </div>
        </div>
      </header>

      <CommandPalette open={cmdOpen} onClose={() => setCmdOpen(false)} />
    </>
  );
}
