"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { CANARY_CHANNEL_ID } from "@/lib/operator-context";

interface NavItemDef {
  label: string;
  href: string;
  icon: string;
  enabled: boolean;
  badge?: string;
}

export function Sidebar() {
  const pathname = usePathname();

  const coreNav: NavItemDef[] = [
    { label: "Overview", href: "/", icon: "⌂", enabled: true },
    { label: "Channels", href: "/channels", icon: "⊞", enabled: true },
    { label: "Missions", href: "/missions", icon: "⚡", enabled: true },
  ];

  const pipelineNav: NavItemDef[] = [
    {
      label: "Content",
      href: `/channels/${CANARY_CHANNEL_ID}/content`,
      icon: "✎",
      enabled: true,
      badge: "Canary",
    },
    {
      label: "Production",
      href: `/channels/${CANARY_CHANNEL_ID}/production`,
      icon: "▶",
      enabled: true,
      badge: "Canary",
    },
    { label: "Schedule", href: "#", icon: "◷", enabled: false, badge: "Soon" },
    { label: "Publisher", href: "#", icon: "☁", enabled: false, badge: "Soon" },
  ];

  const intelligenceNav: NavItemDef[] = [
    { label: "Analytics", href: "#", icon: "📊", enabled: false, badge: "Soon" },
    { label: "Learning", href: "#", icon: "🧠", enabled: false, badge: "Soon" },
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
    <aside className="app-sidebar">
      <div className="sidebar-header">
        <div className="sidebar-logo-icon">Ω</div>
        <div className="sidebar-brand">
          <h2>OMEGA</h2>
          <p>Operator Console</p>
        </div>
      </div>

      <nav className="sidebar-nav">
        <div className="nav-group-title">Operations</div>
        {coreNav.map(renderItem)}

        <div className="nav-group-title">Pipeline</div>
        {pipelineNav.map(renderItem)}

        <div className="nav-group-title">Intelligence & Core</div>
        {intelligenceNav.map(renderItem)}
      </nav>

      <div className="sidebar-footer">
        <span>Canary: DmYTB</span>
        <span className="badge badge-ready">v0.1.0</span>
      </div>
    </aside>
  );
}
