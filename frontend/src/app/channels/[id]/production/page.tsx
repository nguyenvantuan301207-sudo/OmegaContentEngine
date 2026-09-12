import { ChannelProductionWorkspace } from "./bridge";

export default async function ProductionEnginePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ChannelProductionWorkspace channelId={id} />;
}
