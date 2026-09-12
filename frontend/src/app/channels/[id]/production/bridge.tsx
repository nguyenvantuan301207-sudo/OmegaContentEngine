"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useOperatorContext } from "@/lib/operator-context";

export function ChannelProductionWorkspace({ channelId }: { channelId: string }) {
  const router = useRouter();
  const { setSelectedChannelId } = useOperatorContext();

  useEffect(() => {
    void setSelectedChannelId(channelId);
    router.replace("/production");
  }, [channelId, router, setSelectedChannelId]);

  return <div className="card pad"><span className="eyebrow">MISSION WORKSPACE</span><strong>Opening this channel in Production Studio…</strong></div>;
}
