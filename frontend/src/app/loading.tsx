import { LoadingState } from "@/components/ui";

export default function Loading() {
  return (
    <LoadingState
      title="Loading workspace"
      description="Preparing the requested view."
      headingLevel="h1"
    />
  );
}
