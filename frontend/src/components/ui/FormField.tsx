import { cloneElement, type ReactElement } from "react";

interface FieldControlProps {
  id?: string;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
}

export interface FormFieldProps {
  id: string;
  label: string;
  children: ReactElement<FieldControlProps>;
  description?: string;
  error?: string;
  required?: boolean;
}

export function FormField({ id, label, children, description, error, required }: FormFieldProps) {
  const descriptionId = description ? `${id}-description` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ") || undefined;
  const control = cloneElement(children, {
    id,
    "aria-describedby": describedBy,
    "aria-invalid": Boolean(error),
  });

  return (
    <div className="ui-form-field">
      <label className="form-label" htmlFor={id}>
        {label}{required && <span aria-hidden="true"> *</span>}
      </label>
      {control}
      {description && <p id={descriptionId} className="ui-field-description">{description}</p>}
      {error && <p id={errorId} className="ui-field-error" role="alert">{error}</p>}
    </div>
  );
}
