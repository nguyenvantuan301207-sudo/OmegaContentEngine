"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useOperatorContext } from "@/lib/operator-context";

interface Props {
  currentTab?: "dna" | "branding" | "topics" | "research" | "content" | "production" | "schedule" | "publisher" | "analytics" | "learning";
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
    { key: "branding", label: "◆ Branding", href: `/channels/${channelId}/branding` },
    { key: "topics", label: "💡 Topics", href: `/channels/${channelId}/topics` },
    { key: "research", label: "🔬 Research", href: `/channels/${channelId}/research` },
    { key: "content", label: "✍️ Content", href: `/channels/${channelId}/content` },
    { key: "production", label: "🎬 Production", href: `/channels/${channelId}/production` },
    { key: "schedule", label: "◷ Schedule", href: `/schedule` },
    { key: "publisher", label: "☁ Publisher", href: `/publisher` },
    { key: "analytics", label: "📊 Analytics", href: `/analytics` },
    { key: "learning", label: "🧠 Learning", href: `/learning` },
  ];

  const stateClass = channel?.state === "ACTIVE" ? "active" : channel?.state === "ARCHIVED" ? "archived" : "inactive";

  return (
    <section className="channel-context" aria-label="Channel workspace context">
      <div className={`card channel-context-card ${stateClass}`}>
        <div className="channel-context-identity">
          <div className="channel-context-eyebrow">
            <span>Active workspace</span>
            {channel && <span className={`badge ${getStatusBadge(channel.state)}`}>{channel.state}</span>}
            {channel && <span className="badge badge-neutral text-mono">{channel.platform}</span>}
          </div>
          <strong className="channel-context-name">{channel ? channel.name : "Loading channel..."}</strong>
          <span className="channel-context-meta text-mono">
            {channel ? `/${channel.slug} · ${channel.id}` : `ID: ${channelId}`}
          </span>
        </div>

        <div className="channel-context-selector">
          <label htmlFor="channel-context-select">Switch channel</label>
          <select
            id="channel-context-select"
            value={channelId}
            onChange={(event) => setSelectedChannelId(event.target.value)}
            className="form-select"
          >
            {channels.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>
                {candidate.name} [{candidate.state}] ({candidate.platform})
              </option>
            ))}
          </select>
        </div>
      </div>

      {channel?.state === "ARCHIVED" && (
        <div className="alert alert-warning channel-context-warning">
          <span><strong>Channel archived.</strong> Automated generation and publishing are stopped. Activate it in Channel Workspace or switch channels.</span>
          <Link href={`/channels/${channel.id}`} className="btn btn-secondary btn-sm">Manage channel →</Link>
        </div>
      )}

      <nav className="channel-context-tabs" aria-label="Channel workflow">
        {navItems.map((item) => {
          const isActive = currentTab ? currentTab === item.key : pathname === item.href;
          return (
            <Link key={item.key} href={item.href} className={`channel-context-tab${isActive ? " active" : ""}`} aria-current={isActive ? "page" : undefined}>
              {item.label}
            </Link>
          );
        })}
      </nav>
    </section>
  );
}
