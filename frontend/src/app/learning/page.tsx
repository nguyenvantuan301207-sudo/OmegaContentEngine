"use client";

import React from "react";
import { useOperatorContext } from "@/lib/operator-context";
import { LearningInsightsCard } from "@/components/LearningInsightsCard";
import { ChannelContextBar } from "@/components/ChannelContextBar";

export default function LearningPage() {
  const { selectedChannelId } = useOperatorContext();

  return (
    <div style={{ maxWidth: "1200px", margin: "0 auto", padding: "1.5rem" }}>
      {/* Universal Channel Context Bar */}
      <ChannelContextBar currentTab="learning" />

      {/* Page Header */}
      <div className="page-header" style={{ marginBottom: "1.5rem" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", marginBottom: "0.25rem" }}>
            <h1 className="page-title">🧠 Learning & Knowledge Engine</h1>
            <span className="badge badge-active">OMEGA-013</span>
          </div>
          <p className="page-subtitle">
            Continuous cohort observation, Bayesian baseline modeling, empirical hypothesis validation, and institutional memory.
          </p>
        </div>
      </div>

      {/* Learning Insights Card */}
      {selectedChannelId && (
        <LearningInsightsCard channelId={selectedChannelId} />
      )}
    </div>
  );
}
