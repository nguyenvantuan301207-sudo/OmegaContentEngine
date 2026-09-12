"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useOperatorContext } from "@/lib/operator-context";
import { getMissions, type Mission } from "@/lib/api";

interface CommandItem {
  id: string;
  category: "Route" | "Channel" | "Mission" | "Action";
  title: string;
  subtitle?: string;
  badge?: string;
  onSelect: () => void;
}

export function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const { channels, setSelectedChannelId } = useOperatorContext();
  const [query, setQuery] = useState("");
  const [missions, setMissions] = useState<Mission[]>([]);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      setQuery("");
      setSelectedIndex(0);
      void getMissions(50, 0)
        .then((data) => setMissions(data))
        .catch(() => setMissions([]));
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  const items: CommandItem[] = useMemo(() => {
    const list: CommandItem[] = [];

    // Core actions
    list.push(
      {
        id: "action-new-mission",
        category: "Action",
        title: "＋ New Mission",
        subtitle: "Launch autonomous topic → production workflow",
        onSelect: () => {
          onClose();
          router.push("/missions/new");
        },
      },
      {
        id: "action-new-channel",
        category: "Action",
        title: "＋ Create Channel",
        subtitle: "Add a new channel brand and DNA",
        onSelect: () => {
          onClose();
          router.push("/channels/new");
        },
      },
    );

    // Routes
    const appRoutes = [
      { path: "/", name: "Home", desc: "OMEGA product overview and active queue" },
      { path: "/missions", name: "Missions", desc: "Autonomous and supervised pipeline missions" },
      { path: "/production", name: "Production", desc: "Production studio hub, render pipelines, and jobs" },
      { path: "/publisher", name: "Publisher", desc: "Social publishing status and intent review" },
      { path: "/analytics", name: "Analytics", desc: "Performance intelligence and feedback loops" },
      { path: "/learning", name: "Learning", desc: "Institutional memory and strategy optimization" },
      { path: "/channels", name: "Channels", desc: "Channel workspaces, brand DNA and settings" },
      { path: "/schedule", name: "Schedule", desc: "Publishing calendar and reservations" },
      { path: "/assets", name: "Assets", desc: "Asset library, media artifacts, and provenance" },
      { path: "/autopilot", name: "Autopilot", desc: "Autonomous loop status, approvals, and stop conditions" },
      { path: "/settings", name: "Settings", desc: "Workspace configuration, appearance, and engine governance" },
    ];

    for (const r of appRoutes) {
      list.push({
        id: `route-${r.path}`,
        category: "Route",
        title: r.name,
        subtitle: r.desc,
        onSelect: () => {
          onClose();
          router.push(r.path);
        },
      });
    }

    // Channels
    for (const ch of channels.slice(0, 30)) {
      list.push({
        id: `channel-${ch.id}`,
        category: "Channel",
        title: ch.name,
        subtitle: `@${ch.slug} · ${ch.platform} · ${ch.primary_language}`,
        badge: ch.state,
        onSelect: () => {
          onClose();
          void setSelectedChannelId(ch.id);
          router.push(`/channels/${ch.id}`);
        },
      });
    }

    // Missions
    for (const m of missions.slice(0, 30)) {
      list.push({
        id: `mission-${m.id}`,
        category: "Mission",
        title: m.title,
        subtitle: `${m.objective ? m.objective.slice(0, 60) + "..." : ""} · ${m.autonomy_level.replaceAll("_", " ")}`,
        badge: m.state,
        onSelect: () => {
          onClose();
          router.push(`/missions/${m.id}`);
        },
      });
    }

    if (!query.trim()) return list;

    const q = query.toLowerCase();
    return list.filter(
      (item) =>
        item.title.toLowerCase().includes(q) ||
        item.subtitle?.toLowerCase().includes(q) ||
        item.category.toLowerCase().includes(q),
    );
  }, [channels, missions, onClose, query, router, setSelectedChannelId]);

  useEffect(() => {
    setSelectedIndex(0);
  }, [query]);

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      onClose();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelectedIndex((prev) => (prev + 1) % Math.max(1, items.length));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelectedIndex((prev) => (prev - 1 + items.length) % Math.max(1, items.length));
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (items[selectedIndex]) {
        items[selectedIndex].onSelect();
      }
    }
  };

  if (!open) return null;

  return (
    <div
      className="modal-bg open cmd-palette-backdrop"
      id="cmdModal"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Command Palette"
    >
      <div className="modal cmd-palette-modal">
        <div className="modal-head between">
          <div className="row">
            <span className="logo-mark" style={{ width: "24px", height: "24px", fontSize: "12px" }} aria-hidden="true">
              ⌘
            </span>
            <b>Command Palette</b>
          </div>
          <button type="button" className="btn ghost small" onClick={onClose} aria-label="Close command palette">
            Esc
          </button>
        </div>
        <div className="modal-body" style={{ padding: "12px 16px" }}>
          <div className="search cmd-search-bar" style={{ width: "100%", marginBottom: "12px" }}>
            <span aria-hidden="true">⌕</span>
            <input
              ref={inputRef}
              className="input cmd-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Search missions, channels, routes, or actions..."
              aria-label="Search command palette"
            />
          </div>

          <div className="cmd-results-list" role="listbox">
            {items.length === 0 ? (
              <div className="cmd-empty-state muted small" style={{ padding: "20px", textAlign: "center" }}>
                No results found for &ldquo;{query}&rdquo;
              </div>
            ) : (
              items.slice(0, 12).map((item, idx) => {
                const isSelected = idx === selectedIndex;
                return (
                  <div
                    key={item.id}
                    role="option"
                    aria-selected={isSelected}
                    className={`cmd-item cmd-palette-item ${isSelected ? "selected active" : ""}`}
                    onClick={item.onSelect}
                    onMouseEnter={() => setSelectedIndex(idx)}
                  >
                    <div className="cmd-item-left">
                      <span className="cmd-category-tag">{item.category}</span>
                      <div className="cmd-item-text">
                        <b>{item.title}</b>
                        {item.subtitle && <span className="small muted">{item.subtitle}</span>}
                      </div>
                    </div>
                    {item.badge && <span className="badge small">{item.badge}</span>}
                  </div>
                );
              })
            )}
          </div>
        </div>
        <div className="modal-foot between small muted">
          <span>Use ↑ ↓ to navigate, Enter to select, Esc to close</span>
          <span>{items.length} result{items.length === 1 ? "" : "s"}</span>
        </div>
      </div>
    </div>
  );
}
