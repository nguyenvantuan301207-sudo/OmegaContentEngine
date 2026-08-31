"use client";

import React, { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AutonomyLevel, Channel, createMission, getChannels } from "@/lib/api";
import { CANARY_CHANNEL_ID, CANARY_CHANNEL_NAME } from "@/lib/operator-context";

function CreateMissionForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialChannelId = searchParams.get("channel_id") || "";

  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [channelId, setChannelId] = useState<string>(initialChannelId);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [description, setDescription] = useState("");
  const [autonomyLevel, setAutonomyLevel] = useState<AutonomyLevel>("SUPERVISED");
  const [priority, setPriority] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getChannels("ACTIVE", undefined, 50, 0)
      .then((data) => setChannels(data))
      .catch(() => {
        // Ignore fetch errors for optional dropdown
      });
  }, []);

  // If initialChannelId arrives via URL, ensure it is selected
  useEffect(() => {
    if (initialChannelId && !channelId) {
      setChannelId(initialChannelId);
    }
  }, [initialChannelId, channelId]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || !objective.trim()) {
      setError("Title and objective are required.");
      return;
    }

    setSubmitting(true);
    setError(null);

    try {
      const created = await createMission({
        title: title.trim(),
        objective: objective.trim(),
        channel_id: channelId ? channelId : undefined,
        description: description.trim() || undefined,
        autonomy_level: autonomyLevel,
        priority: Number(priority),
      });
      router.push(`/missions/${created.id}`);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create mission");
      setSubmitting(false);
    }
  };

  return (
    <div className="form-card">
      {error && (
        <div style={{ padding: "1rem", background: "var(--status-danger-bg)", border: "1px solid var(--status-danger-border)", borderRadius: "var(--radius-sm)", color: "var(--status-danger)", marginBottom: "1.5rem", fontSize: "0.85rem" }}>
          {error}
        </div>
      )}

      <form onSubmit={handleSubmit}>
        {/* Mission Title */}
        <div className="form-group">
          <label className="form-label">Mission Title *</label>
          <input
            type="text"
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="e.g. Q3 Developer Intelligence Campaign"
            className="input"
            style={{ width: "100%" }}
          />
          <span className="form-helper">Concise, descriptive name identifying this content campaign.</span>
        </div>

        {/* Operating Channel */}
        <div className="form-group">
          <label className="form-label">Operating Channel (Optional)</label>
          <select
            value={channelId}
            onChange={(e) => setChannelId(e.target.value)}
            className="select"
            style={{ width: "100%" }}
          >
            <option value="">-- Standalone Mission (No Channel Link) --</option>
            <option value={CANARY_CHANNEL_ID}>
              ⭐ {CANARY_CHANNEL_NAME} (Canary Fleet - DmYTB)
            </option>
            {channels
              .filter((c) => c.id !== CANARY_CHANNEL_ID)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} (/{c.slug}) — {c.state}
                </option>
              ))}
          </select>
          <span className="form-helper">
            Linking a channel pins its active DNA revision and target audience profile at execution time.
          </span>
        </div>

        {/* Objective */}
        <div className="form-group">
          <label className="form-label">Objective / Core Mandate *</label>
          <textarea
            required
            rows={4}
            value={objective}
            onChange={(e) => setObjective(e.target.value)}
            placeholder="Detail the fundamental goal, topic focus, hook directions, or strategic editorial outcome..."
          />
          <span className="form-helper">Provides context to research, topic intelligence, and script generation agents.</span>
        </div>

        {/* Description */}
        <div className="form-group">
          <label className="form-label">Description (Optional)</label>
          <textarea
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Additional background context, target audiences, or channel notes..."
          />
        </div>

        {/* Autonomy Level & Priority Grid */}
        <div className="grid grid-cols-2" style={{ gap: "1.25rem", margin: "1.25rem 0" }}>
          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">Autonomy Level</label>
            <select
              value={autonomyLevel}
              onChange={(e) => setAutonomyLevel(e.target.value as AutonomyLevel)}
              className="select"
              style={{ width: "100%" }}
            >
              <option value="SUPERVISED">SUPERVISED (Recommended: Auto-dispatch with approval gates)</option>
              <option value="ASSISTED">ASSISTED (Human-guided milestones)</option>
              <option value="MANUAL">MANUAL (Explicit step execution)</option>
            </select>
            <span className="form-helper">Controls approval gates between production and publishing.</span>
          </div>

          <div className="form-group" style={{ marginBottom: 0 }}>
            <label className="form-label">Priority (1 to 10)</label>
            <input
              type="number"
              min={1}
              max={10}
              value={priority}
              onChange={(e) => setPriority(Number(e.target.value))}
              className="input"
              style={{ width: "100%" }}
            />
            <span className="form-helper">1 = Standard background, 5 = Elevated, 10 = Urgent dispatch.</span>
          </div>
        </div>

        {/* Actions */}
        <div className="form-actions">
          <Link href="/missions" className="btn btn-secondary">
            Cancel
          </Link>
          <button
            type="submit"
            disabled={submitting}
            className="btn btn-primary"
          >
            {submitting ? "Creating Mission..." : "+ Create Draft Mission"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default function NewMissionPage() {
  return (
    <div>
      {/* Header */}
      <div className="page-header" style={{ maxWidth: "840px", margin: "0 auto 2rem auto" }}>
        <div>
          <div style={{ marginBottom: "0.5rem" }}>
            <Link
              href="/missions"
              style={{ fontSize: "0.78rem", color: "var(--accent-secondary)", textDecoration: "none" }}
            >
              ← Back to Missions
            </Link>
          </div>
          <h1 className="page-title">Create New Mission</h1>
          <p className="page-subtitle">
            Define a high-level goal, optional channel link, and autonomy constraints for the orchestrator.
          </p>
        </div>
      </div>

      <Suspense fallback={<div style={{ textAlign: "center", padding: "3rem", color: "var(--text-muted)" }}>Loading form...</div>}>
        <CreateMissionForm />
      </Suspense>
    </div>
  );
}
