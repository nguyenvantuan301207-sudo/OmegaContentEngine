"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { globalNavigation, type NavigationGroup } from "@/lib/navigation";

export function Sidebar({ open = false, onClose }: { open?: boolean; onClose?: () => void }) {
  const pathname = usePathname();
  const groups: readonly NavigationGroup[] = ["Create", "Library", "Grow", "Manage"];

  const renderItem = (item: (typeof globalNavigation)[number]) => {
    const isActive =
      item.href !== "#" &&
      (pathname === item.href ||
        (item.href !== "/" && pathname.startsWith(item.href)));

    return (
      <Link
        key={item.label}
        href={item.href}
        className={`nav ${isActive ? "active" : ""}`}
        onClick={onClose}
      >
        <span className="nav-icon">{item.icon}</span>
        <span className="txt">{item.label}</span>
      </Link>
    );
  };

  return (
    <aside className={`sidebar app-sidebar${open ? " open" : ""}`} aria-label="Primary navigation">
      <div className="logo">
        <div className="logo-mark" aria-hidden="true">Ω</div>
        <span className="txt">OMEGA</span>
        <button type="button" className="sidebar-close" onClick={onClose} aria-label="Close navigation">
          ×
        </button>
      </div>

      <nav className="sidebar-nav">
        {groups.map((group) => (
          <div className="nav-group" key={group}>
            <div className="nav-label">{group}</div>
            {globalNavigation.filter((item) => item.group === group).map(renderItem)}
          </div>
        ))}
      </nav>

      <div className="side-footer">
        <div className="between">
          <div>
            <b>System</b>
            <div className="small muted">All services online</div>
          </div>
          <span className="dot" aria-label="Services online" />
        </div>
        <div className="progress" style={{ marginTop: "10px" }}>
          <span style={{ width: "100%" }} />
        </div>
        <div className="between small muted" style={{ marginTop: "6px" }}>
          <span>Product workspace</span>
          <span>Ready</span>
        </div>
      </div>
    </aside>
  );
}
