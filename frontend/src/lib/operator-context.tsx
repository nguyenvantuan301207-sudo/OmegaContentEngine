"use client";

import React, { createContext, useContext, useEffect, useState } from "react";

export type ViewMode = "OPERATOR" | "DEVELOPMENT";

export const CANARY_CHANNEL_ID = "fa8813c9-9e7b-43d0-b76e-1bb323ad5a7a";
export const CANARY_CHANNEL_NAME = "FastAPI Masterclass";

interface OperatorContextType {
  mode: ViewMode;
  setMode: (mode: ViewMode) => void;
  toggleMode: () => void;
  canaryChannelId: string;
  canaryChannelName: string;
}

const OperatorContext = createContext<OperatorContextType>({
  mode: "OPERATOR",
  setMode: () => {},
  toggleMode: () => {},
  canaryChannelId: CANARY_CHANNEL_ID,
  canaryChannelName: CANARY_CHANNEL_NAME,
});

const STORAGE_KEY = "omega_console_view_mode";

export function OperatorProvider({ children }: { children: React.ReactNode }) {
  const [mode, setModeState] = useState<ViewMode>("OPERATOR");

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored === "OPERATOR" || stored === "DEVELOPMENT") {
        setModeState(stored);
      }
    } catch {
      // Ignore localStorage read errors in restricted contexts
    }
  }, []);

  const setMode = (newMode: ViewMode) => {
    setModeState(newMode);
    try {
      localStorage.setItem(STORAGE_KEY, newMode);
    } catch {
      // Ignore localStorage write errors
    }
  };

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
      }}
    >
      {children}
    </OperatorContext.Provider>
  );
}

export function useOperatorContext(): OperatorContextType {
  return useContext(OperatorContext);
}
