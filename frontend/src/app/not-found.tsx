import Link from "next/link";
import { EmptyState } from "@/components/ui";

export default function NotFound() {
  return (
    <EmptyState
      title="Page not found"
      headingLevel="h1"
      description="The requested workspace route does not exist or is no longer available."
      action={
        <div className="ui-inline-actions">
          <Link href="/" className="btn btn-primary">
            Return to overview
          </Link>
          <Link href="/channels" className="btn btn-secondary">
            Browse channels
          </Link>
        </div>
      }
    />
  );
}
