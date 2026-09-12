import type { ReactNode } from "react";
import Link from "next/link";
import type { Channel, Mission } from "@/lib/api";
import { statusTone } from "@/lib/presentation";
import { StatusBadge } from "./StatusBadge";

export function ProductMetricCard({
  label,
  value,
  detail,
  tone = "neutral",
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: "neutral" | "info" | "success" | "warning" | "danger";
}) {
  return (
    <article className={`product-metric product-metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {detail ? <small>{detail}</small> : null}
    </article>
  );
}

export function QuickActionCard({
  href,
  icon,
  title,
  description,
}: {
  href: string;
  icon: string;
  title: string;
  description: string;
}) {
  return (
    <Link className="quick-action-card" href={href}>
      <span className="quick-action-icon" aria-hidden="true">{icon}</span>
      <span>
        <strong>{title}</strong>
        <small>{description}</small>
      </span>
      <span className="quick-action-arrow" aria-hidden="true">→</span>
    </Link>
  );
}

export function MissionCard({
  mission,
  channelName,
}: {
  mission: Mission;
  channelName?: string;
}) {
  // Determine segmented progress based on mission state
  const isTerminalSuccess = mission.state === "SUCCEEDED";
  const isRunning = mission.state === "RUNNING";
  const isFailed = mission.state === "FAILED";
  const isPaused = mission.state === "PAUSED";

  const stageName = isTerminalSuccess
    ? "Ready to publish"
    : isRunning
    ? "Production pipeline"
    : isFailed
    ? "Execution stopped"
    : isPaused
    ? "Needs attention"
    : "Planning";

  const progressLabel = isTerminalSuccess
    ? "Complete"
    : isRunning
    ? "In progress"
    : isFailed
    ? "Failed"
    : isPaused
    ? "Review"
    : "Queued";

  return (
    <article className="card mission">
      <div className="between">
        <span className="badge info">{mission.autonomy_level.replaceAll("_", " ")}</span>
        <StatusBadge tone={statusTone(mission.state)}>{mission.state}</StatusBadge>
      </div>

      <div className="mission-content" style={{ marginTop: "8px" }}>
        <h3 className="mission-title" style={{ fontSize: "15px", fontWeight: 700, margin: "6px 0 4px", color: "var(--text-primary)" }}>
          {mission.title}
        </h3>
        <div className="small muted" style={{ fontSize: "11px" }}>
          {channelName || "Channel"} · P{mission.priority} · {mission.objective ? (mission.objective.length > 55 ? mission.objective.slice(0, 55) + "…" : mission.objective) : "Autonomous workflow"}
        </div>
      </div>

      {/* Segmented mini workflow bar matching prototype */}
      <div className="mini" aria-label={`Stage: ${stageName}`}>
        <i className={isTerminalSuccess || isRunning || isPaused || isFailed ? "done" : "current"} />
        <i className={isTerminalSuccess || isRunning || isPaused ? "done" : isFailed ? "current" : ""} />
        <i className={isTerminalSuccess || isRunning ? "done" : isPaused ? "current" : ""} />
        <i className={isTerminalSuccess ? "done" : isRunning ? "current" : ""} />
        <i className={isTerminalSuccess ? "done" : ""} />
        <i className={isTerminalSuccess ? "done" : ""} />
      </div>

      <div className="between small">
        <span className="muted">{stageName}</span>
        <b style={{ color: isTerminalSuccess ? "var(--success)" : isRunning ? "var(--accent)" : isPaused ? "var(--warning)" : "var(--muted)" }}>
          {progressLabel}
        </b>
      </div>

      <Link
        href={`/missions/${mission.id}`}
        className="btn"
        style={{ marginTop: "12px", width: "100%", display: "block", textAlign: "center" }}
      >
        Open Workspace
      </Link>

      <details className="product-identity" style={{ marginTop: "8px" }}>
        <summary className="small muted">Technical ID</summary>
        <code>{mission.id}</code>
      </details>
    </article>
  );
}

export function ChannelCard({ channel }: { channel: Channel }) {
  const locale = [channel.primary_language, channel.target_region].filter(Boolean).join(" · ");
  return (
    <article className="product-record-card channel-product-card">
      <header>
        <div className="channel-product-mark" aria-hidden="true">{channel.name.slice(0, 2).toUpperCase()}</div>
        <StatusBadge tone={statusTone(channel.state)}>{channel.state}</StatusBadge>
      </header>
      <div className="product-record-copy">
        <span className="ui-eyebrow">{channel.platform} · {locale}</span>
        <h3>{channel.name}</h3>
        <p>{channel.description || "No channel description has been recorded."}</p>
      </div>
      <dl className="product-record-facts">
        <div><dt>Workspace</dt><dd>/{channel.slug}</dd></div>
        <div><dt>Strategy</dt><dd>{channel.dna?.content_strategy?.niche || "Not configured"}</dd></div>
        <div><dt>Timezone</dt><dd>{channel.timezone}</dd></div>
      </dl>
      <footer>
        <details className="product-identity"><summary>Technical ID</summary><code>{channel.id}</code></details>
        <Link href={`/channels/${channel.id}`} className="btn btn-secondary btn-sm">Open workspace</Link>
      </footer>
    </article>
  );
}

export function StudioPanel({
  title,
  eyebrow,
  children,
  actions,
  className = "",
}: {
  title: string;
  eyebrow?: string;
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`studio-panel ${className}`.trim()}>
      <header>
        <div>
          {eyebrow ? <span className="ui-eyebrow">{eyebrow}</span> : null}
          <h2>{title}</h2>
        </div>
        {actions ? <div className="ui-inline-actions">{actions}</div> : null}
      </header>
      <div className="studio-panel-body">{children}</div>
    </section>
  );
}
