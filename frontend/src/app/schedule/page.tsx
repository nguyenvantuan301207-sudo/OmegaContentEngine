"use client";

import React from "react";
import { useOperatorContext } from "@/lib/operator-context";
import { SchedulerTimelineCard } from "@/components/SchedulerTimelineCard";
import { ChannelContextBar } from "@/components/ChannelContextBar";

export default function SchedulePage() {
  const { selectedChannelId } = useOperatorContext();

  return (
    <div style={{ maxWidth: "1200px", margin: "0 auto", padding: "1.5rem" }}>
      {/* Universal Channel Context Bar */}
      <ChannelContextBar currentTab="schedule" />

      {/* Page Header */}
      <div className="page-header" style={{ marginBottom: "1.5rem" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.25rem" }}>
            <h1 className="page-title">◷ Smart Scheduler & Cadence Allocator</h1>
            <span className="badge badge-active">OMEGA-010</span>
          </div>
          <p className="page-subtitle">
            Autonomous publishing slot reservation, cadence policy enforcement, capacity limits, and timeline inspection.
          </p>
        </div>
      </div>

      {/* Scheduler Timeline Card */}
      {selectedChannelId && (
        <SchedulerTimelineCard channelId={selectedChannelId} />
      )}
    </div>
  );
}
