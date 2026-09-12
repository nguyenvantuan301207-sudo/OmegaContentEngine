"use client";

import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { Channel, getChannel, getChannels } from "@/lib/api";
import {
  DEFAULT_PREFERENCES,
  persistPreferences,
  PREFERENCES_EVENT,
  readPreferences,
} from "@/lib/preferences";
import {
  classifyChannel,
  isChannelVisible,
  isClassificationInternal,
  type ChannelClassification,
} from "@/lib/channel-classification";

export type ViewMode = "OPERATOR" | "DEVELOPMENT";

interface OperatorContextType {
  mode: ViewMode;
  setMode: (mode: ViewMode) => void;
  toggleMode: () => void;
  channels: Channel[];
  visibleChannels: Channel[];
  channelsLoading: boolean;
  channelError: string | null;
  selectedChannelId: string;
  selectedChannel: Channel | null;
  selectedChannelClassification: ChannelClassification;
  isSelectedChannelInternal: boolean;
  showInternalChannels: boolean;
  setShowInternalChannels: (show: boolean) => void;
  setSelectedChannelId: (id: string) => void;
  refreshChannels: () => Promise<void>;
}

const OperatorContext = createContext<OperatorContextType>({
  mode: "OPERATOR",
  setMode: () => {},
  toggleMode: () => {},
  channels: [],
  visibleChannels: [],
  channelsLoading: true,
  channelError: null,
  selectedChannelId: "",
  selectedChannel: null,
  selectedChannelClassification: "UNKNOWN",
  isSelectedChannelInternal: false,
  showInternalChannels: false,
  setShowInternalChannels: () => {},
  setSelectedChannelId: () => {},
  refreshChannels: async () => {},
});

const STORAGE_KEY = "omega_console_view_mode";
const CHANNEL_STORAGE_KEY = "omega_selected_channel_id";

export function OperatorProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<ViewMode>("OPERATOR");
  const [channels, setChannels] = useState<Channel[]>([]);
  const [channelsLoading, setChannelsLoading] = useState(true);
  const [channelError, setChannelError] = useState<string | null>(null);
  const [selectedChannelId, setSelectedChannelIdState] = useState("");
  const [selectedChannel, setSelectedChannel] = useState<Channel | null>(null);

  const loadChannels = useCallback(async () => {
    setChannelsLoading(true);
    setChannelError(null);
    try {
      const chanList = await getChannels(undefined, undefined, 50, 0);
      setChannels(chanList);

      let initialChannelId = "";
      try {
        const stored = localStorage.getItem(CHANNEL_STORAGE_KEY);
        if (stored && stored.trim().length > 0) {
          initialChannelId = stored.trim();
        }
      } catch {
        // Ignore localStorage read errors
      }

      // Check if initialChannelId is valid in the list
      let matched: Channel | null = initialChannelId
        ? chanList.find((c) => c.id === initialChannelId) || null
        : null;

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
        try {
          matched = await getChannel(initialChannelId);
        } catch {
          // A stale persisted selection is optional; the loaded channel list remains authoritative.
          initialChannelId = "";
        }
        if (matched && !chanList.some((c) => c.id === matched?.id)) {
          setChannels([matched, ...chanList]);
        }
      }

      if (matched) {
        setSelectedChannelIdState(matched.id);
        setSelectedChannel(matched);
      } else if (chanList.length > 0) {
        const fallback =
          chanList.find((channel) => channel.state === "ACTIVE") || chanList[0];
        setSelectedChannelIdState(fallback.id);
        setSelectedChannel(fallback);
      } else {
        setSelectedChannelIdState("");
        setSelectedChannel(null);
      }
    } catch (error: unknown) {
      setChannelError(
        error instanceof Error
          ? error.message
          : "Unable to load channel context.",
      );
    } finally {
      setChannelsLoading(false);
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
        try {
          const fetched = await getChannel(cleanId);
          setSelectedChannel(fetched);
          setChannels((prev) =>
            prev.some((c) => c.id === fetched.id) ? prev : [fetched, ...prev],
          );
          setChannelError(null);
        } catch (error: unknown) {
          setChannelError(
            error instanceof Error
              ? error.message
              : "Unable to select channel.",
          );
        }
      }
    },
    [channels],
  );

  const [showInternalChannels, setShowInternalChannelsState] = useState(false);

  useEffect(() => {
    const prefs = readPreferences();
    setShowInternalChannelsState(Boolean(prefs.showInternalChannels));

    const handlePrefs = (event: Event) => {
      const customEvent = event as CustomEvent<Partial<typeof DEFAULT_PREFERENCES>>;
      if (customEvent.detail && typeof customEvent.detail.showInternalChannels === "boolean") {
        setShowInternalChannelsState(customEvent.detail.showInternalChannels);
      }
    };
    window.addEventListener(PREFERENCES_EVENT, handlePrefs);
    return () => window.removeEventListener(PREFERENCES_EVENT, handlePrefs);
  }, []);

  const setShowInternalChannels = useCallback((show: boolean) => {
    setShowInternalChannelsState(show);
    const currentPrefs = readPreferences();
    persistPreferences({ ...currentPrefs, showInternalChannels: show });
  }, []);

  const selectedChannelClassification: ChannelClassification = useMemo(() => {
    if (!selectedChannel) return "UNKNOWN";
    return classifyChannel(selectedChannel).classification;
  }, [selectedChannel]);

  const isSelectedChannelInternal = useMemo(() => {
    return isClassificationInternal(selectedChannelClassification);
  }, [selectedChannelClassification]);

  const visibleChannels = useMemo(() => {
    return channels.filter((ch) => {
      // Keep selected channel addressable even if internal
      if (selectedChannelId && ch.id === selectedChannelId) return true;
      return isChannelVisible(ch, showInternalChannels);
    });
  }, [channels, selectedChannelId, showInternalChannels]);

  const toggleMode = () => {
    setMode(mode === "OPERATOR" ? "DEVELOPMENT" : "OPERATOR");
  };

  return (
    <OperatorContext.Provider
      value={{
        mode,
        setMode,
        toggleMode,
        channels,
        visibleChannels,
        channelsLoading,
        channelError,
        selectedChannelId,
        selectedChannel,
        selectedChannelClassification,
        isSelectedChannelInternal,
        showInternalChannels,
        setShowInternalChannels,
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
