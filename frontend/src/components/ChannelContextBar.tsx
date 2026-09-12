"use client";

import React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useOperatorContext } from "@/lib/operator-context";
import { getChannelNavigation } from "@/lib/navigation";
import { StatusBadge } from "./ui";
import { statusTone } from "@/lib/presentation";
import {
  classifyChannel,
  isClassificationInternal,
  getProvenanceBadgeLabel,
} from "@/lib/channel-classification";

interface Props {
  currentTab?: "dna" | "branding" | "topics" | "research" | "content" | "production" | "schedule" | "publisher" | "analytics" | "learning";
}

export function ChannelContextBar({ currentTab }: Props) {
  const pathname = usePathname();
  const router = useRouter();
  const {
    selectedChannel,
    selectedChannelId,
    setSelectedChannelId,
    channels,
    visibleChannels,
    showInternalChannels,
    channelsLoading,
    channelError,
  } = useOperatorContext();

  const routeChannelId = pathname.match(/^\/channels\/([^/]+)/)?.[1];
  const channelId = routeChannelId || selectedChannelId;
  const channel = channels.find((candidate) => candidate.id === channelId)
    || (selectedChannel?.id === channelId ? selectedChannel : null);

  const channelCls = channel ? classifyChannel(channel).classification : "UNKNOWN";
  const isInternal = isClassificationInternal(channelCls);

  const navItems = getChannelNavigation(channelId);
  const candidateChannels = showInternalChannels ? channels : visibleChannels;

  return (
    <section className="channel-context" aria-label="Channel workspace context" style={{ marginBottom: "16px" }}>
      <div
        style={{
          background: "var(--bg-card, #111823)",
          border: "1px solid var(--line)",
          borderRadius: "var(--radius)",
          padding: "10px 14px",
          display: "flex",
          flexDirection: "column",
          gap: "10px",
        }}
      >
        {/* TOP ROW: SLEEK IDENTITY + SWITCHER */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", minWidth: 0 }}>
            <div
              style={{
                width: "30px",
                height: "30px",
                borderRadius: "8px",
                background: "linear-gradient(135deg, var(--accent), #5147df)",
                color: "#fff",
                display: "grid",
                placeItems: "center",
                fontWeight: 800,
                fontSize: "12px",
                flexShrink: 0,
              }}
            >
              {channel ? channel.name.slice(0, 2).toUpperCase() : "Ω"}
            </div>
            <div style={{ minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <strong style={{ fontSize: "14px", color: "var(--text, #fff)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {channel ? channel.name : "Select Channel"}
                </strong>
                {channel && <StatusBadge tone={statusTone(channel.state)}>{channel.state}</StatusBadge>}
                {channel && isInternal && (
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
                    title={`Internal channel (${channelCls})`}
                  >
                    {getProvenanceBadgeLabel(channelCls) || channelCls}
                  </span>
                )}
                {channel && <span className="cmd-category-tag" style={{ fontSize: "9px" }}>{channel.platform}</span>}
              </div>
              <div className="small muted text-mono" style={{ fontSize: "10px" }}>
                {channel ? `/${channel.slug}` : "No channel selected"}
              </div>
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <label htmlFor="channel-context-select" className="small muted" style={{ fontSize: "11px" }}>
              Switch:
            </label>
            <select
              id="channel-context-select"
              value={channelId}
              onChange={(event) => {
                const nextChannelId = event.target.value;
                void setSelectedChannelId(nextChannelId);
                if (currentTab && ["dna", "branding", "topics", "research", "content", "production"].includes(currentTab)) {
                  const destination = getChannelNavigation(nextChannelId).find((item) => item.key === currentTab);
                  if (destination) router.push(destination.href);
                }
              }}
              className="select"
              style={{
                background: "var(--bg-input, #0f151e)",
                border: "1px solid var(--line)",
                padding: "5px 10px",
                fontSize: "12px",
                borderRadius: "8px",
                color: "var(--text)",
                maxWidth: "220px",
              }}
              disabled={channelsLoading || channels.length === 0}
            >
              {candidateChannels.map((candidate) => {
                const candCls = classifyChannel(candidate).classification;
                const candInternal = isClassificationInternal(candCls);
                const badge = getProvenanceBadgeLabel(candCls);
                return (
                  <option key={candidate.id} value={candidate.id}>
                    {candidate.name} ({candidate.platform})
                    {candInternal && badge ? ` [${badge}]` : ""}
                  </option>
                );
              })}
            </select>
          </div>
        </div>

        {/* WORKFLOW SUBNAV TABS */}
        {navItems.length > 0 && (
          <nav
            style={{
              display: "flex",
              gap: "4px",
              borderTop: "1px solid var(--line)",
              paddingTop: "8px",
              overflowX: "auto",
            }}
            aria-label="Channel workflow"
          >
            {navItems.map((item) => {
              const isActive = currentTab ? currentTab === item.key : pathname === item.href;
              return (
                <Link
                  key={item.key}
                  href={item.href}
                  style={{
                    padding: "6px 11px",
                    borderRadius: "7px",
                    fontSize: "12px",
                    fontWeight: isActive ? 700 : 500,
                    color: isActive ? "#fff" : "var(--muted)",
                    background: isActive ? "rgba(124, 92, 255, 0.18)" : "transparent",
                    border: isActive ? "1px solid rgba(124, 92, 255, 0.35)" : "1px solid transparent",
                    whiteSpace: "nowrap",
                    transition: "all 0.12s ease",
                  }}
                  aria-current={isActive ? "page" : undefined}
                >
                  {item.label}
                </Link>
              );
            })}
          </nav>
        )}
      </div>

      {channelError && (
        <div className="alert alert-danger" role="alert" style={{ marginTop: "10px" }}>
          <span><strong>Channel context unavailable.</strong> {channelError}</span>
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => window.location.reload()}>Retry</button>
        </div>
      )}

      {channel?.state === "ARCHIVED" && (
        <div className="alert alert-warning" style={{ marginTop: "10px" }}>
          <span><strong>Channel archived.</strong> Automated generation and publishing are stopped.</span>
          <Link href={`/channels/${channel.id}`} className="btn btn-secondary btn-sm">Manage channel →</Link>
        </div>
      )}
    </section>
  );
}
