import type { ReactNode } from "react";
import { Alert } from "./Alert";

interface StateProps {
  title: string;
  description?: string;
  action?: ReactNode;
  headingLevel?: "h1" | "h2";
}

export function EmptyState({
  title,
  description,
  action,
  headingLevel = "h2",
}: StateProps) {
  const Heading = headingLevel;
  return (
    <div className="ui-state ui-empty-state">
      <span className="ui-state-icon" aria-hidden="true">
        ◇
      </span>
      <Heading>{title}</Heading>
      {description && <p>{description}</p>}
      {action && <div className="ui-state-action">{action}</div>}
    </div>
  );
}

export function LoadingState({
  title = "Loading",
  description,
  headingLevel = "h2",
}: Partial<StateProps>) {
  const Heading = headingLevel;
  return (
    <div className="ui-state ui-loading-state" role="status" aria-live="polite">
      <span className="ui-loading-indicator" aria-hidden="true" />
      <Heading>{title}</Heading>
      {description && <p>{description}</p>}
    </div>
  );
}

export function ErrorState({
  title,
  description,
  action,
  headingLevel = "h2",
}: StateProps) {
  return (
    <Alert title={title} titleAs={headingLevel} tone="danger" actions={action}>
      {description || "The requested data could not be loaded."}
    </Alert>
  );
}
