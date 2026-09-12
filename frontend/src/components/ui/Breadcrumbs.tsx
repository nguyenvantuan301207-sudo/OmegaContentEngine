import Link from "next/link";
import type { BreadcrumbItem } from "@/lib/navigation";

export interface BreadcrumbsProps {
  items: readonly BreadcrumbItem[];
}

export function Breadcrumbs({ items }: BreadcrumbsProps) {
  return (
    <nav className="ui-breadcrumbs" aria-label="Breadcrumb">
      <ol>
        {items.map((item, index) => {
          const current = index === items.length - 1;
          return (
            <li key={`${item.label}-${index}`}>
              {index > 0 && <span className="ui-breadcrumb-separator" aria-hidden="true">/</span>}
              {item.href && !current ? <Link href={item.href}>{item.label}</Link> : <span aria-current={current ? "page" : undefined}>{item.label}</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
