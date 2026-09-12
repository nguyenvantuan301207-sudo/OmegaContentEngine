import type { ReactNode } from "react";
import { StatusBadge } from "@/components/ui";
import { statusTone } from "@/lib/presentation";

export { statusTone } from "@/lib/presentation";

export interface WorkflowMetric {
  label: string;
  value: ReactNode;
  status?: string | null;
}

export function WorkflowStatusSummary({
  metrics,
}: {
  metrics: WorkflowMetric[];
}) {
  return (
    <dl className="workflow-summary">
      {metrics.map((metric) => (
        <div key={metric.label}>
          <dt>{metric.label}</dt>
          <dd>
            {metric.status ? (
              <StatusBadge tone={statusTone(metric.status)}>
                {metric.value}
              </StatusBadge>
            ) : (
              metric.value
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export interface WorkflowTab<T extends string> {
  id: T;
  label: string;
  count?: number;
}

export function WorkflowTabs<T extends string>({
  tabs,
  active,
  onChange,
  label,
}: {
  tabs: WorkflowTab<T>[];
  active: T;
  onChange: (tab: T) => void;
  label: string;
}) {
  return (
    <div className="workflow-tabs" role="group" aria-label={label}>
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          aria-pressed={active === tab.id}
          className={active === tab.id ? "active" : ""}
          onClick={() => onChange(tab.id)}
        >
          {tab.label}
          {typeof tab.count === "number" ? <span>{tab.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function EntityList({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: ReactNode;
}) {
  return (
    <aside className="workflow-entity-list" aria-label={title}>
      <header>
        <h2>{title}</h2>
        {description ? <p>{description}</p> : null}
      </header>
      <div className="workflow-entity-items">{children}</div>
    </aside>
  );
}

export function EntityListButton({
  selected,
  title,
  meta,
  status,
  onClick,
}: {
  selected: boolean;
  title: string;
  meta?: ReactNode;
  status?: string | null;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      className={`workflow-entity-button ${selected ? "active" : ""}`}
      aria-pressed={selected}
      onClick={onClick}
    >
      <span className="workflow-entity-heading">
        <strong>{title}</strong>
        {status ? (
          <StatusBadge tone={statusTone(status)}>{status}</StatusBadge>
        ) : null}
      </span>
      {meta ? <span className="workflow-entity-meta">{meta}</span> : null}
    </button>
  );
}

export function TechnicalDetails({
  children,
  label = "Technical details",
  data,
}: {
  children?: ReactNode;
  label?: string;
  data?: unknown;
}) {
  return (
    <details className="ui-technical-details">
      <summary>{label}</summary>
      <div>
        {children}
        {data !== undefined ? <pre>{JSON.stringify(data, null, 2)}</pre> : null}
      </div>
    </details>
  );
}

export interface WorkflowProgressStep {
  label: string;
  state: "complete" | "current" | "pending" | "failed";
  detail?: string;
}

export function WorkflowProgress({
  steps,
  label,
}: {
  steps: WorkflowProgressStep[];
  label: string;
}) {
  return (
    <ol className="workflow-progress" aria-label={label}>
      {steps.map((step, index) => (
        <li
          key={step.label}
          className={step.state}
          aria-current={step.state === "current" ? "step" : undefined}
        >
          <span aria-hidden="true">{index + 1}</span>
          <div>
            <strong>{step.label}</strong>
            {step.detail ? <small>{step.detail}</small> : null}
          </div>
        </li>
      ))}
    </ol>
  );
}
