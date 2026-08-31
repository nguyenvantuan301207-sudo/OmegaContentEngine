"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useOperatorContext } from "@/lib/operator-context";

interface Props {
  currentTab?: "dna" | "topics" | "research" | "content" | "production" | "schedule" | "publisher" | "analytics" | "learning";
}

export function ChannelContextBar({ currentTab }: Props) {
  const pathname = usePathname();
  const { selectedChannel, selectedChannelId, setSelectedChannelId, channels } = useOperatorContext();

  const channel = selectedChannel;
  const channelId = selectedChannelId;

  const getStatusBadge = (state?: string) => {
    switch (state) {
      case "ACTIVE":
        return "badge-active";
      case "PAUSED":
        return "badge-paused";
      case "ARCHIVED":
        return "badge-failed";
      case "DRAFT":
      default:
        return "badge-draft";
    }
  };

  const navItems = [
    { key: "dna", label: "🧬 DNA", href: `/channels/${channelId}` },
    { key: "topics", label: "💡 Topics", href: `/channels/${channelId}/topics` },
    { key: "research", label: "🔬 Research", href: `/channels/${channelId}/research` },
    { key: "content", label: "✍️ Content", href: `/channels/${channelId}/content` },
    { key: "production", label: "🎬 Production", href: `/channels/${channelId}/production` },
    { key: "schedule", label: "◷ Schedule", href: `/schedule` },
    { key: "publisher", label: "☁ Publisher", href: `/publisher` },
    { key: "analytics", label: "📊 Analytics", href: `/analytics` },
    { key: "learning", label: "🧠 Learning", href: `/learning` },
  ];

  return (
    <div style={{ marginBottom: "1.5rem" }}>
      {/* Active Channel Header & Selector */}
      <div
        className="card"
        style={{
          padding: "1rem 1.25rem",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: "1rem",
          borderLeft: channel?.state === "ACTIVE" ? "3px solid var(--status-success)" : channel?.state === "ARCHIVED" ? "3px solid var(--status-danger)" : "3px solid var(--border-subtle)",
        }}
      >
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginBottom: "0.25rem" }}>
            <span style={{ fontSize: "0.72rem", textTransform: "uppercase", color: "var(--text-muted)", letterSpacing: "0.06em", fontWeight: 700 }}>
              Channel Context
            </span>
            {channel && (
              <span className={`badge ${getStatusBadge(channel.state)}`} style={{ fontSize: "0.7rem", padding: "0.15rem 0.45rem" }}>
                {channel.state}
              </span>
            )}
            {channel && (
              <span className="badge badge-neutral text-mono" style={{ fontSize: "0.7rem", padding: "0.15rem 0.45rem" }}>
                {channel.platform}
              </span>
            )}
          </div>
          <div style={{ fontWeight: 700, fontSize: "1.15rem", color: "var(--text-primary)" }}>
            {channel ? channel.name : "Loading Channel..."}
          </div>
          <div className="text-mono" style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "0.2rem" }}>
            {channel ? `Slug: /${channel.slug} • ID: ${channel.id}` : `ID: ${channelId}`}
          </div>
        </div>

        {/* Channel Selector */}
        <div style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
          <label style={{ fontSize: "0.8rem", color: "var(--text-secondary)", fontWeight: 500 }}>
            Switch Channel:
          </label>
          <select
            value={channelId}
            onChange={(e) => setSelectedChannelId(e.target.value)}
            className="form-select"
            style={{
              padding: "0.4rem 0.85rem",
              fontSize: "0.85rem",
              minWidth: "240px",
              background: "var(--bg-input)",
              borderColor: "var(--border-subtle)",
              color: "var(--text-primary)",
              borderRadius: "var(--radius-sm)",
            }}
          >
            {channels.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} [{c.state}] ({c.platform})
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Archived Warning Banner */}
      {channel?.state === "ARCHIVED" && (
        <div
          style={{
            padding: "0.75rem 1rem",
            background: "var(--status-warning-bg)",
            border: "1px solid var(--status-warning-border)",
            borderRadius: "var(--radius-sm)",
            color: "var(--status-warning)",
            marginTop: "0.75rem",
            fontSize: "0.82rem",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span>
            ⚠️ <strong>Channel Archived:</strong> This channel is archived. Automated generation and publishing are stopped. You can activate it in Channel Workspace or switch to an active channel above.
          </span>
          <Link href={`/channels/${channel.id}`} className="btn btn-secondary btn-sm" style={{ fontSize: "0.75rem", padding: "0.2rem 0.5rem" }}>
            Manage Channel →
          </Link>
        </div>
      )}

      {/* Pipeline Navigation Bar */}
      <div
        style={{
          display: "flex",
          gap: "0.35rem",
          marginTop: "0.75rem",
          overflowX: "auto",
          paddingBottom: "0.25rem",
        }}
      >
        {navItems.map((item) => {
          const isActive = currentTab ? currentTab === item.key : pathname === item.href;
          return (
            <Link
              key={item.key}
              href={item.href}
              className={`btn btn-sm ${isActive ? "btn-primary" : "btn-secondary"}`}
              style={{
                fontSize: "0.78rem",
                padding: "0.35rem 0.75rem",
                whiteSpace: "nowrap",
                fontWeight: isActive ? 600 : 400,
              }}
            >
              {item.label}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
