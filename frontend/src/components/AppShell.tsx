"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";
import { applyPreferences, PREFERENCES_EVENT, readPreferences } from "@/lib/preferences";

export function AppShell({ children }: { children: React.ReactNode }) {
    const pathname = usePathname();
    const [navigationOpen, setNavigationOpen] = useState(false);

    useEffect(() => {
        setNavigationOpen(false);
    }, [pathname]);

    useEffect(() => {
        document.body.style.overflow = navigationOpen ? "hidden" : "";
        return () => {
            document.body.style.overflow = "";
        };
    }, [navigationOpen]);

    useEffect(() => {
        const media = window.matchMedia("(prefers-color-scheme: light)");
        const applyStored = () => applyPreferences(readPreferences());
        applyStored();
        window.addEventListener(PREFERENCES_EVENT, applyStored);
        window.addEventListener("storage", applyStored);
        media.addEventListener("change", applyStored);
        return () => {
            window.removeEventListener(PREFERENCES_EVENT, applyStored);
            window.removeEventListener("storage", applyStored);
            media.removeEventListener("change", applyStored);
        };
    }, []);

    return (
        <div className="app-shell">
            <a className="skip-link" href="#main-content">Skip to main content</a>
            <Sidebar open={navigationOpen} onClose={() => setNavigationOpen(false)} />
            {navigationOpen && (
                <button
                    type="button"
                    className="sidebar-scrim"
                    aria-label="Close navigation"
                    onClick={() => setNavigationOpen(false)}
                />
            )}
            <div className="app-main-wrapper">
                <TopBar onNavigationToggle={() => setNavigationOpen((open) => !open)} navigationOpen={navigationOpen} />
                <main className="app-content" id="main-content">{children}</main>
            </div>
        </div>
    );
}
