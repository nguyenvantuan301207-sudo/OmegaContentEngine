"use client";

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { Channel, getChannel, getChannels } from "@/lib/api";

export type ViewMode = "OPERATOR" | "DEVELOPMENT";

export const CANARY_CHANNEL_ID = "fa8813c9-9e7b-43d0-b76e-1bb323ad5a7a";
export const CANARY_CHANNEL_NAME = "FastAPI Masterclass";

interface OperatorContextType {
  mode: ViewMode;
  setMode: (mode: ViewMode) => void;
  toggleMode: () => void;
  canaryChannelId: string;
  canaryChannelName: string;
  channels: Channel[];
  selectedChannelId: string;
  selectedChannel: Channel | null;
  setSelectedChannelId: (id: string) => void;
  refreshChannels: () => Promise<void>;
}

const OperatorContext = createContext<OperatorContextType>({
  mode: "OPERATOR",
  setMode: () => {},
  toggleMode: () => {},
  canaryChannelId: CANARY_CHANNEL_ID,
  canaryChannelName: CANARY_CHANNEL_NAME,
  channels: [],
  selectedChannelId: CANARY_CHANNEL_ID,
  selectedChannel: null,
  setSelectedChannelId: () => {},
  refreshChannels: async () => {},
});

const STORAGE_KEY = "omega_console_view_mode";
const CHANNEL_STORAGE_KEY = "omega_selected_channel_id";

export function OperatorProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<ViewMode>("OPERATOR");
  const [channels, setChannels] = useState<Channel[]>([]);
  const [selectedChannelId, setSelectedChannelIdState] = useState<string>(CANARY_CHANNEL_ID);
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null);

  const loadChannels = useCallback(async () => {
    try {
      // Fetch available channels from API
      const chanList = await getChannels(undefined, undefined, 50, 0).catch(() => []);
      setChannels(chanList);

      let initialChannelId = CANARY_CHANNEL_ID;
      try {
        const stored = localStorage.getItem(CHANNEL_STORAGE_KEY);
        if (stored && stored.trim().length > 0) {
          initialChannelId = stored.trim();
        }
      } catch {
        // Ignore localStorage read errors
      }

      // Check if initialChannelId is valid in the list
      let matched: Channel | null = chanList.find((c) => c.id === initialChannelId) || null;

      // If matched channel is archived or not found, try to find an ACTIVE channel
      if (!matched || matched.state === "ARCHIVED") {
        const activeChan = chanList.find((c) => c.state === "ACTIVE");
        if (activeChan) {
          matched = activeChan;
          initialChannelId = activeChan.id;
        }
      }

      if (!matched && initialChannelId) {
        // Fetch it authoritatively directly
        matched = await getChannel(initialChannelId).catch(() => null);
        if (matched && !chanList.some((c) => c.id === matched?.id)) {
          setChannels([matched, ...chanList]);
        }
      }

      if (matched) {
        setSelectedChannelIdState(matched.id);
        setSelectedChannel(matched);
      } else if (chanList.length > 0) {
        setSelectedChannelIdState(chanList[0].id);
        setSelectedChannel(chanList[0]);
      }
    } catch {
      // Fail closed / gracefully
    }
  }, []);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === "OPERATOR" || stored === "DEVELOPMENT") {
        setModeState(stored);
      }
    } catch {
      // Ignore localStorage read errors
    }
    loadChannels();
  }, [loadChannels]);

  const setMode = (newMode: ViewMode) => {
    setModeState(newMode);
    try {
      localStorage.setItem(STORAGE_KEY, newMode);
    } catch {
      // Ignore localStorage write errors
    }
  };

  const setSelectedChannelId = useCallback(
    async (id: string) => {
      if (!id || id.trim().length === 0) return;
      const cleanId = id.trim();
      setSelectedChannelIdState(cleanId);
      try {
        localStorage.setItem(CHANNEL_STORAGE_KEY, cleanId);
      } catch {
        // Ignore localStorage write errors
      }

      // Find in current channels list or fetch directly
      const found = channels.find((c) => c.id === cleanId);
      if (found) {
        setSelectedChannel(found);
      } else {
        const fetched = await getChannel(cleanId).catch(() => null);
        if (fetched) {
          setSelectedChannel(fetched);
          setChannels((prev) => (prev.some((c) => c.id === fetched.id) ? prev : [fetched, ...prev]));
        }
      }
    },
    [channels]
  );

  const toggleMode = () => {
    setMode(mode === "OPERATOR" ? "DEVELOPMENT" : "OPERATOR");
  };

  return (
    <OperatorContext.Provider
      value={{
        mode,
        setMode,
        toggleMode,
        canaryChannelId: CANARY_CHANNEL_ID,
        canaryChannelName: CANARY_CHANNEL_NAME,
        channels,
        selectedChannelId,
        selectedChannel,
        setSelectedChannelId,
        refreshChannels: loadChannels,
      }}
    >
      {children}
    </OperatorContext.Provider>
  );
}

export function useOperatorContext(): OperatorContextType {
  return useContext(OperatorContext);
}
