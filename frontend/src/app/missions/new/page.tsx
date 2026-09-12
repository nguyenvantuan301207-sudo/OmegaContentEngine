"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { type AutonomyLevel, type Channel, createMission, getChannels } from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import {
  classifyChannel,
  isClassificationInternal,
  isChannelVisible,
  getProvenanceBadgeLabel,
} from "@/lib/channel-classification";
import { Alert, FormField, LoadingState, PageHeader, PageSection } from "@/components/ui";

function CreateMissionForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const requestedChannelId = searchParams.get("channel_id") || "";
  const { selectedChannelId, showInternalChannels } = useOperatorContext();
  const [title, setTitle] = useState("");
  const [objective, setObjective] = useState("");
  const [description, setDescription] = useState("");
  const [channelId, setChannelId] = useState(requestedChannelId);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [autonomyLevel, setAutonomyLevel] = useState<AutonomyLevel>("SUPERVISED");
  const [priority, setPriority] = useState(1);
  const [channelsLoading, setChannelsLoading] = useState(true);
  const [channelError, setChannelError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let active = true;
    setChannelsLoading(true);
    getChannels("ACTIVE", undefined, 50, 0)
      .then((data) => {
        if (!active) return;
        setChannels(data);
        setChannelError(null);
      })
      .catch((requestError: unknown) => {
        if (active) setChannelError(requestError instanceof Error ? requestError.message : "Unable to load active channels.");
      })
      .finally(() => {
        if (active) setChannelsLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!channelId && !requestedChannelId && selectedChannelId) setChannelId(selectedChannelId);
  }, [channelId, requestedChannelId, selectedChannelId]);

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!title.trim() || !objective.trim()) {
      setSubmitError("Mission title and objective are required.");
      return;
    }

    setSubmitting(true);
    setSubmitError(null);
    try {
      const created = await createMission({
        title: title.trim(),
        objective: objective.trim(),
        channel_id: channelId || undefined,
        description: description.trim() || undefined,
        autonomy_level: autonomyLevel,
        priority,
      });
      router.push(`/missions/${created.id}`);
    } catch (requestError: unknown) {
      setSubmitError(requestError instanceof Error ? requestError.message : "Failed to create mission.");
      setSubmitting(false);
    }
  };

  return (
    <>
      {channelError && <Alert tone="warning" title="Channel list unavailable">{channelError} A standalone mission can still be created.</Alert>}
      {submitError && <Alert tone="danger" title="Mission could not be created">{submitError}</Alert>}

      <form className="ui-form-shell" onSubmit={handleSubmit} noValidate>
        <PageSection title="Mission definition" description="State the operational outcome before choosing execution controls.">
          <FormField id="mission-title" label="Mission title" required>
            <input className="input" required value={title} onChange={(event) => setTitle(event.target.value)} />
          </FormField>
          <FormField id="mission-objective" label="Objective" description="The objective is passed to planning, research and content stages." required>
            <textarea rows={5} required value={objective} onChange={(event) => setObjective(event.target.value)} />
          </FormField>
          <FormField id="mission-description" label="Additional context">
            <textarea rows={3} value={description} onChange={(event) => setDescription(event.target.value)} />
          </FormField>
        </PageSection>

        <PageSection title="Execution context" description="Channel selection pins the active DNA revision when execution begins.">
          <FormField id="mission-channel" label="Operating channel" description="Leave empty for a standalone mission.">
            <select className="select" value={channelId} onChange={(event) => setChannelId(event.target.value)} disabled={channelsLoading}>
              <option value="">{channelsLoading ? "Loading active channels…" : "Standalone mission"}</option>
              {channels
                .filter((channel) =>
                  channel.id === channelId ||
                  channel.id === requestedChannelId ||
                  channel.id === selectedChannelId ||
                  isChannelVisible(channel, showInternalChannels),
                )
                .map((channel) => {
                  const { classification } = classifyChannel(channel);
                  const isInternal = isClassificationInternal(classification);
                  const badge = getProvenanceBadgeLabel(classification);
                  return (
                    <option key={channel.id} value={channel.id}>
                      {channel.name} — {channel.state}
                      {isInternal && badge ? ` [${badge}]` : ""}
                    </option>
                  );
                })}
            </select>
          </FormField>
          <div className="ui-form-grid">
            <FormField id="mission-autonomy" label="Autonomy level" required>
              <select className="select" value={autonomyLevel} onChange={(event) => setAutonomyLevel(event.target.value as AutonomyLevel)}>
                <option value="MANUAL">Manual</option>
                <option value="ASSISTED">Assisted</option>
                <option value="SUPERVISED">Supervised</option>
                <option value="AUTONOMOUS">Autonomous</option>
                <option value="STRATEGIC_AUTONOMOUS">Strategic autonomous</option>
              </select>
            </FormField>
            <FormField id="mission-priority" label="Priority" description="1 is standard; 10 is highest priority." required>
              <input className="input" type="number" min={1} max={10} required value={priority} onChange={(event) => setPriority(Number(event.target.value))} />
            </FormField>
          </div>
        </PageSection>

        <div className="ui-form-actions">
          <Link href="/missions" className="btn btn-secondary">Cancel</Link>
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            {submitting ? "Creating mission…" : "Create draft mission"}
          </button>
        </div>
      </form>
    </>
  );
}

export default function NewMissionPage() {
  return (
    <div className="ui-page-stack ui-form-page">
      <PageHeader
        eyebrow="Missions"
        title="Create mission"
        description="Define an objective, operating context and explicit autonomy level."
        actions={<Link href="/missions" className="btn btn-secondary">Back to missions</Link>}
      />
      <Suspense fallback={<LoadingState title="Loading mission form" />}>
        <CreateMissionForm />
      </Suspense>
    </div>
  );
}
