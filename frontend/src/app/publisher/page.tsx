"use client";

import { useCallback, useEffect, useState } from "react";
import { listPlatformAccounts, listPublishIntents, type PlatformAccount, type PublishIntent } from "@/lib/api";
import { useOperatorContext } from "@/lib/operator-context";
import { ChannelContextBar } from "@/components/ChannelContextBar";
import { PublishingStatusCard } from "@/components/PublishingStatusCard";
import { Alert, EmptyState, LoadingState, PageHeader, PageSection, StatusBadge, type StatusTone } from "@/components/ui";

function intentTone(state: string): StatusTone {
  if (state === "PUBLISHED") return "success";
  if (state === "FAILED" || state === "CANCELLED") return "danger";
  if (state === "APPROVED" || state === "CLAIMED") return "info";
  return "neutral";
}

export default function PublisherPage() {
  const { selectedChannelId, selectedChannel } = useOperatorContext();
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [intents, setIntents] = useState<PublishIntent[]>([]);
  const [selectedIntent, setSelectedIntent] = useState<PublishIntent | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    if (!selectedChannelId) {
      setAccounts([]);
      setIntents([]);
      setSelectedIntent(null);
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const [accountRecords, intentRecords] = await Promise.all([
        listPlatformAccounts(selectedChannelId),
        listPublishIntents({ channel_id: selectedChannelId }),
      ]);
      setAccounts(accountRecords);
      setIntents(intentRecords);
      const requestedIntentId = new URLSearchParams(window.location.search).get("intent_id");
      setSelectedIntent(intentRecords.find((intent) => intent.id === requestedIntentId) || intentRecords[0] || null);
    } catch (requestError: unknown) {
      setError(requestError instanceof Error ? requestError.message : "Failed to load publishing state.");
    } finally {
      setLoading(false);
    }
  }, [selectedChannelId]);

  useEffect(() => {
    void loadData();
  }, [loadData]);

  const activeAccount = accounts.find((account) => account.status === "ACTIVE") || accounts[0] || null;

  return (
    <div className="ui-page-stack">
      <ChannelContextBar currentTab="publisher" />
      <PageHeader
        eyebrow="Operations"
        title="Publish studio"
        description={selectedChannel ? `Account readiness, approvals and publish intents for ${selectedChannel.name}.` : "Select a channel to inspect publishing state."}
        actions={<button type="button" className="btn btn-secondary" onClick={() => void loadData()} disabled={loading}>Refresh</button>}
      />

      {error && <Alert tone="danger" title="Publishing state unavailable" actions={<button type="button" className="btn btn-secondary btn-sm" onClick={() => void loadData()}>Retry</button>}>{error}</Alert>}

      {loading ? <LoadingState title="Loading publisher" /> : !selectedChannelId ? (
        <EmptyState title="No channel selected" description="Select an available channel before managing publication." />
      ) : !error && (
        <>
          <PageSection title="Publishing controls" description="Actions operate on the explicitly selected persisted intent.">
            <PublishingStatusCard
              channelId={selectedChannelId}
              account={activeAccount}
              latestIntent={selectedIntent}
              latestAttempt={null}
              uploadProgress={null}
              onRefresh={loadData}
            />
          </PageSection>

          <PageSection title="Publish intents" description={`${intents.length} persisted intent${intents.length === 1 ? "" : "s"} for this channel.`}>
            {intents.length === 0 ? (
              <EmptyState title="No publish intents" description="Prepare a production artifact before creating a publish intent." />
            ) : (
              <div className="table-container ui-table-card">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Intent</th>
                      <th>State</th>
                      <th>Privacy</th>
                      <th>Created</th>
                      <th><span className="sr-only">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {intents.map((intent) => {
                      const selected = selectedIntent?.id === intent.id;
                      return (
                        <tr key={intent.id} aria-selected={selected}>
                          <td data-label="Intent">
                            <strong className="ui-table-title">{intent.title}</strong>
                            <span className="ui-table-subtitle text-mono">Revision {intent.revision_number} · {intent.id}</span>
                          </td>
                          <td data-label="State"><StatusBadge tone={intentTone(intent.state)}>{intent.state}</StatusBadge></td>
                          <td data-label="Privacy">{intent.requested_privacy_status}</td>
                          <td data-label="Created"><time dateTime={intent.created_at}>{new Date(intent.created_at).toLocaleString()}</time></td>
                          <td className="ui-table-action">
                            <button type="button" className={`btn btn-sm ${selected ? "btn-primary" : "btn-secondary"}`} onClick={() => setSelectedIntent(intent)} aria-pressed={selected}>
                              {selected ? "Selected" : "Select"}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </PageSection>
        </>
      )}
    </div>
  );
}
