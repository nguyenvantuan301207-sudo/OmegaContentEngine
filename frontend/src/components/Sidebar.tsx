"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { CANARY_CHANNEL_ID, useOperatorContext } from "@/lib/operator-context";

interface NavItemDef {
  label: string;
  href: string;
  icon: string;
  enabled: boolean;
  badge?: string;
}

export function Sidebar({ open = false, onClose }: { open?: boolean; onClose?: () => void }) {
  const pathname = usePathname();
  const { selectedChannelId, canaryChannelId } = useOperatorContext();
  const targetChannelId = selectedChannelId || canaryChannelId || CANARY_CHANNEL_ID;

  const overviewNav: NavItemDef[] = [
    { label: "Overview", href: "/", icon: "⌂", enabled: true },
  ];

  const workspaceNav: NavItemDef[] = [
    { label: "Channels", href: "/channels", icon: "⊞", enabled: true },
    { label: "Missions", href: "/missions", icon: "⚡", enabled: true },
  ];

  const pipelineNav: NavItemDef[] = [
    {
      label: "Topics",
      href: `/channels/${targetChannelId}/topics`,
      icon: "💡",
      enabled: true,
    },
    {
      label: "Research",
      href: `/channels/${targetChannelId}/research`,
      icon: "🔬",
      enabled: true,
    },
    {
      label: "Content",
      href: `/channels/${targetChannelId}/content`,
      icon: "✎",
      enabled: true,
    },
    {
      label: "Production",
      href: `/channels/${targetChannelId}/production`,
      icon: "▶",
      enabled: true,
    },
  ];

  const operationsNav: NavItemDef[] = [
    { label: "Schedule", href: "/schedule", icon: "◷", enabled: true },
    { label: "Publisher", href: "/publisher", icon: "☁", enabled: true },
  ];

  const intelligenceNav: NavItemDef[] = [
    { label: "Analytics", href: "/analytics", icon: "📊", enabled: true },
    { label: "Learning", href: "/learning", icon: "🧠", enabled: true },
    { label: "System", href: "/", icon: "⚙", enabled: true },
  ];

  const renderItem = (item: NavItemDef) => {
    const isActive =
      item.href !== "#" &&
      (pathname === item.href ||
        (item.href !== "/" && pathname.startsWith(item.href)));

    if (!item.enabled) {
      return (
        <div key={item.label} className="nav-item disabled" title="Feature coming soon in canary rollout">
          <span className="nav-icon">{item.icon}</span>
          <span>{item.label}</span>
          {item.badge && <span className="nav-badge-pill">{item.badge}</span>}
        </div>
      );
    }

    return (
      <Link
        key={item.label}
        href={item.href}
        className={`nav-item ${isActive ? "active" : ""}`}
      >
        <span className="nav-icon">{item.icon}</span>
        <span>{item.label}</span>
        {item.badge && <span className="nav-badge-pill">{item.badge}</span>}
      </Link>
    );
  };

  return (
    <aside className={`app-sidebar${open ? " open" : ""}`} aria-label="Primary navigation">
      <div className="sidebar-header">
        <div className="sidebar-logo-icon" aria-hidden="true">Ω</div>
        <div className="sidebar-brand">
          <h2>OMEGA</h2>
          <p>Content operating system</p>
        </div>
        <button type="button" className="sidebar-close" onClick={onClose} aria-label="Close navigation">
          ×
        </button>
      </div>

      <nav className="sidebar-nav">
        <div className="nav-group-title">Overview</div>
        {overviewNav.map(renderItem)}

        <div className="nav-group-title">Workspace</div>
        {workspaceNav.map(renderItem)}

        <div className="nav-group-title">Pipeline</div>
        {pipelineNav.map(renderItem)}

        <div className="nav-group-title">Operations</div>
        {operationsNav.map(renderItem)}

        <div className="nav-group-title">Intelligence & Core</div>
        {intelligenceNav.map(renderItem)}
      </nav>

      <div className="sidebar-footer">
        <span className="sidebar-environment"><span className="status-dot healthy" /> Canary · DmYTB</span>
        <span className="badge badge-ready">v0.1.0</span>
      </div>
    </aside>
  );
}
