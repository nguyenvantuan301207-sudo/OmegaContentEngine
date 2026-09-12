"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  createPublishIntent,
  getMissions,
  getMissionTasks,
  listPlatformAccounts,
  type Channel,
  type MediaArtifact,
  type Mission,
  type PlatformAccount,
  type PrivacyStatus,
  type ProductionRequest,
  type PublishIntent,
  type Task,
} from "@/lib/api";
import {
  Alert,
  Dialog,
  EmptyState,
  FormField,
  LoadingState,
  StatusBadge,
} from "@/components/ui";
import {
  TechnicalDetails,
  WorkflowStatusSummary,
  statusTone,
} from "@/components/workflow/WorkflowPrimitives";

interface PublishPreparationModalProps {
  channel: Channel;
  productionRequest: ProductionRequest;
  artifact: MediaArtifact;
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: (intent: PublishIntent) => void;
}

export function PublishPreparationModal({
  channel,
  productionRequest,
  artifact,
  isOpen,
  onClose,
  onSuccess,
}: PublishPreparationModalProps) {
  const [accounts, setAccounts] = useState<PlatformAccount[]>([]);
  const [missions, setMissions] = useState<Mission[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [accountId, setAccountId] = useState("");
  const [missionId, setMissionId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [privacy, setPrivacy] = useState<PrivacyStatus>("PRIVATE");
  const [categoryId, setCategoryId] = useState("28");
  const [madeForKids, setMadeForKids] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [intent, setIntent] = useState<PublishIntent | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    async function initialize() {
      setLoading(true);
      setError(null);
      setIntent(null);
      setTitle(`Episode: ${channel.name} Video`);
      setDescription(
        `Official release from ${channel.name}.\n\nProduced with OMEGA Content Engine.`,
      );
      setTags("omega, video, technology");
      try {
        const [accountData, missionData] = await Promise.all([
          listPlatformAccounts(channel.id),
          getMissions(),
        ]);
        const activeAccounts = accountData.filter(
          (account) => account.status === "ACTIVE",
        );
        const channelMissions = missionData.filter(
          (mission) => mission.channel_id === channel.id,
        );
        setAccounts(activeAccounts);
        setMissions(channelMissions);
        setAccountId(activeAccounts[0]?.id || "");
        const firstMission = channelMissions[0];
        setMissionId(firstMission?.id || "");
        if (firstMission) {
          const taskData = await getMissionTasks(firstMission.id);
          setTasks(taskData);
          setTaskId(taskData[0]?.id || "");
        } else {
          setTasks([]);
          setTaskId("");
        }
      } catch (reason: unknown) {
        setError(
          reason instanceof Error
            ? reason.message
            : "Failed to load publishing prerequisites.",
        );
      } finally {
        setLoading(false);
      }
    }
    void initialize();
  }, [channel.id, channel.name, isOpen]);

  async function changeMission(nextMissionId: string) {
    setMissionId(nextMissionId);
    setTaskId("");
    setError(null);
    try {
      const taskData = await getMissionTasks(nextMissionId);
      setTasks(taskData);
      setTaskId(taskData[0]?.id || "");
    } catch (reason: unknown) {
      setTasks([]);
      setError(
        reason instanceof Error
          ? reason.message
          : "Mission tasks could not be loaded.",
      );
    }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (channel.state === "ARCHIVED") {
      setError("Activate this channel before preparing publication.");
      return;
    }
    if (!accountId) {
      setError("Select an active platform account.");
      return;
    }
    if (!missionId || !taskId) {
      setError("A valid mission and task are required.");
      return;
    }
    if (!title.trim()) {
      setError("Video title is required.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const created = await createPublishIntent({
        mission_id: missionId,
        task_id: taskId,
        channel_id: channel.id,
        platform_account_id: accountId,
        media_artifact_id: artifact.id,
        media_artifact_checksum: artifact.content_hash,
        channel_dna_revision_id:
          productionRequest.channel_dna_revision_id || null,
        title: title.trim(),
        description: description.trim(),
        tags: tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
        requested_privacy_status: privacy,
        category_id: categoryId,
        made_for_kids: madeForKids,
      });
      setIntent(created);
      onSuccess?.(created);
    } catch (reason: unknown) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Failed to create publish intent.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={isOpen}
      title="Prepare video publication"
      description={`Construct a canonical PublishIntent snapshot for ${channel.name}.`}
      onClose={onClose}
      actions={
        intent ? (
          <button type="button" className="btn btn-primary" onClick={onClose}>
            Done
          </button>
        ) : (
          <>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={onClose}
            >
              Cancel
            </button>
            <button
              type="submit"
              form="publish-preparation-form"
              className="btn btn-primary"
              disabled={
                submitting ||
                loading ||
                accounts.length === 0 ||
                channel.state === "ARCHIVED"
              }
            >
              {submitting ? "Creating intent…" : "Create publish intent"}
            </button>
          </>
        )
      }
    >
      {error ? (
        <Alert tone="danger" title="Publishing prerequisites unavailable">
          {error}
        </Alert>
      ) : null}
      {intent ? (
        <div className="workflow-detail">
          <Alert tone="success" title="Publish intent created">
            The canonical intent snapshot is ready for the supported scheduling
            or publishing workflow.
          </Alert>
          <WorkflowStatusSummary
            metrics={[
              { label: "State", value: intent.state, status: intent.state },
              { label: "Privacy", value: intent.requested_privacy_status },
              { label: "Revision", value: `v${intent.revision_number}` },
              { label: "Title", value: intent.title },
            ]}
          />
          <div className="workflow-card-grid">
            <Link
              className="btn btn-secondary"
              href={`/schedule?channel_id=${channel.id}&intent_id=${intent.id}`}
            >
              Open scheduler
            </Link>
            <Link
              className="btn btn-primary"
              href={`/publisher?channel_id=${channel.id}&intent_id=${intent.id}`}
            >
              Open publisher
            </Link>
          </div>
          <TechnicalDetails
            data={{
              id: intent.id,
              checksum: intent.intent_checksum,
              artifact_id: artifact.id,
            }}
          />
        </div>
      ) : loading ? (
        <LoadingState
          title="Loading publishing context"
          description="Retrieving platform accounts, missions, and tasks."
        />
      ) : (
        <form
          id="publish-preparation-form"
          className="workflow-dialog-form"
          onSubmit={submit}
        >
          <WorkflowStatusSummary
            metrics={[
              { label: "Artifact", value: `v${artifact.version}` },
              {
                label: "Dimensions",
                value:
                  artifact.width && artifact.height
                    ? `${artifact.width}×${artifact.height}`
                    : "Not reported",
              },
              {
                label: "Duration",
                value: artifact.duration_ms
                  ? `${(artifact.duration_ms / 1000).toFixed(1)}s`
                  : "Not reported",
              },
            ]}
          />
          <TechnicalDetails
            label="Artifact checksum"
            data={{
              media_artifact_id: artifact.id,
              sha256: artifact.content_hash,
            }}
          />
          {accounts.length === 0 ? (
            <EmptyState
              title="No active platform account"
              description="Connect an account before creating a publish intent."
              action={
                <Link href="/publisher" className="btn btn-secondary">
                  Open publisher
                </Link>
              }
            />
          ) : (
            <FormField id="publish-account" label="Platform account" required>
              <select
                className="select"
                value={accountId}
                onChange={(event) => setAccountId(event.target.value)}
              >
                {accounts.map((account) => (
                  <option value={account.id} key={account.id}>
                    {account.account_display_name} · {account.platform}
                  </option>
                ))}
              </select>
            </FormField>
          )}
          <div className="ui-form-grid">
            <FormField id="publish-mission" label="Mission" required>
              <select
                className="select"
                value={missionId}
                onChange={(event) => void changeMission(event.target.value)}
              >
                {missions.map((mission) => (
                  <option value={mission.id} key={mission.id}>
                    {mission.id.slice(0, 8)} · {mission.state}
                  </option>
                ))}
              </select>
            </FormField>
            <FormField id="publish-task" label="Task" required>
              <select
                className="select"
                value={taskId}
                onChange={(event) => setTaskId(event.target.value)}
              >
                {tasks.map((task) => (
                  <option value={task.id} key={task.id}>
                    {task.task_type} · {task.id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </FormField>
          </div>
          <FormField id="publish-title" label="Video title" required>
            <input
              className="input"
              maxLength={255}
              value={title}
              onChange={(event) => setTitle(event.target.value)}
            />
          </FormField>
          <FormField id="publish-description" label="Description">
            <textarea
              className="input"
              maxLength={5000}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </FormField>
          <FormField
            id="publish-tags"
            label="Tags"
            description="Comma-separated"
          >
            <input
              className="input"
              value={tags}
              onChange={(event) => setTags(event.target.value)}
            />
          </FormField>
          <div className="ui-form-grid ui-form-grid-3">
            <FormField id="publish-privacy" label="Privacy">
              <select
                className="select"
                value={privacy}
                onChange={(event) =>
                  setPrivacy(event.target.value as PrivacyStatus)
                }
              >
                <option value="PRIVATE">Private</option>
                <option value="UNLISTED">Unlisted</option>
                <option value="PUBLIC">Public</option>
              </select>
            </FormField>
            <FormField id="publish-category" label="YouTube category">
              <select
                className="select"
                value={categoryId}
                onChange={(event) => setCategoryId(event.target.value)}
              >
                <option value="28">Science & Technology</option>
                <option value="27">Education</option>
                <option value="22">People & Blogs</option>
                <option value="24">Entertainment</option>
              </select>
            </FormField>
            <FormField id="publish-kids" label="Made for kids">
              <input
                className="workflow-checkbox"
                type="checkbox"
                checked={madeForKids}
                onChange={(event) => setMadeForKids(event.target.checked)}
              />
            </FormField>
          </div>
          <Alert
            tone={privacy === "PRIVATE" ? "success" : "warning"}
            title={`Requested privacy: ${privacy}`}
          >
            <StatusBadge tone={statusTone(privacy)}>{privacy}</StatusBadge> is
            recorded on the intent; execution remains a separate supported
            action.
          </Alert>
        </form>
      )}
    </Dialog>
  );
}
