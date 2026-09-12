import type { ReactNode } from "react";

export type AlertTone = "info" | "success" | "warning" | "danger";

export interface AlertProps {
  title?: string;
  children: ReactNode;
  tone?: AlertTone;
  actions?: ReactNode;
  titleAs?: "strong" | "h1" | "h2";
}

export function Alert({
  title,
  children,
  tone = "info",
  actions,
  titleAs = "strong",
}: AlertProps) {
  const Title = titleAs;
  return (
    <div
      className={`ui-alert ui-alert-${tone}`}
      role={tone === "danger" ? "alert" : "status"}
    >
      <div className="ui-alert-copy">
        {title && <Title>{title}</Title>}
        <div>{children}</div>
      </div>
      {actions && <div className="ui-alert-actions">{actions}</div>}
    </div>
  );
}
