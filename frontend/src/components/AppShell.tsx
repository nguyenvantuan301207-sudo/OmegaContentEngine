"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { TopBar } from "@/components/TopBar";

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

    return (
        <div className="app-shell">
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
