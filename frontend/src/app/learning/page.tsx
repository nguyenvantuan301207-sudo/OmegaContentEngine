"use client";

import { useOperatorContext } from "@/lib/operator-context";
import { LearningInsightsCard } from "@/components/LearningInsightsCard";
import { ChannelContextBar } from "@/components/ChannelContextBar";
import { EmptyState, LoadingState, PageHeader, PageSection, StatusBadge } from "@/components/ui";

export default function LearningPage() {
  const { selectedChannel, selectedChannelId, channelsLoading } = useOperatorContext();

  return (
    <div className="ui-page-stack">
      <ChannelContextBar currentTab="learning" />
      <PageHeader
        eyebrow="Intelligence"
        title="OMEGA learnings"
        description={selectedChannel ? `Persisted knowledge and hypothesis evidence for ${selectedChannel.name}.` : "Select a channel to inspect learning evidence."}
      />
      <PageSection
        title="Channel learning state"
        description="Insights remain traceable to persisted observations and validated hypotheses."
        actions={selectedChannel && <StatusBadge tone={selectedChannel.state === "ACTIVE" ? "success" : "warning"}>{selectedChannel.state}</StatusBadge>}
      >
        {channelsLoading ? <LoadingState title="Loading channel context" /> : selectedChannelId ? (
          <div className="ui-route-card"><LearningInsightsCard channelId={selectedChannelId} /></div>
        ) : <EmptyState title="No channel selected" description="Select an available channel to load learning evidence." />}
      </PageSection>
    </div>
  );
}
