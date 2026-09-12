import type { ReactNode } from "react";

export interface PageSectionProps {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function PageSection({ title, description, actions, children, className = "" }: PageSectionProps) {
  return (
    <section className={`ui-page-section ${className}`.trim()}>
      {(title || description || actions) && (
        <div className="ui-section-header">
          <div>
            {title && <h2>{title}</h2>}
            {description && <p>{description}</p>}
          </div>
          {actions && <div className="ui-section-actions">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}
