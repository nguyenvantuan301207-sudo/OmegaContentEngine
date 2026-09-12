export type NavigationGroup = "Create" | "Library" | "Grow" | "Manage";

export interface NavigationItem {
  label: string;
  href: string;
  icon: string;
  group: NavigationGroup;
}

export interface ChannelNavigationItem {
  key: "dna" | "branding" | "topics" | "research" | "content" | "production";
  label: string;
  href: string;
}

export interface BreadcrumbItem {
  label: string;
  href?: string;
}

export const globalNavigation: readonly NavigationItem[] = [
  { label: "Home", href: "/", icon: "⌂", group: "Create" },
  { label: "Missions", href: "/missions", icon: "◆", group: "Create" },
  { label: "Production", href: "/production", icon: "▶", group: "Create" },
  { label: "Assets", href: "/assets", icon: "▧", group: "Library" },
  { label: "Publisher", href: "/publisher", icon: "↑", group: "Grow" },
  { label: "Analytics", href: "/analytics", icon: "⌁", group: "Grow" },
  { label: "Learning", href: "/learning", icon: "◇", group: "Grow" },
  { label: "Channels", href: "/channels", icon: "◎", group: "Manage" },
  { label: "Schedule", href: "/schedule", icon: "◷", group: "Manage" },
  { label: "Autopilot", href: "/autopilot", icon: "∞", group: "Manage" },
  { label: "Settings", href: "/settings", icon: "⚙", group: "Manage" },
];

export function getChannelNavigation(channelId: string): readonly ChannelNavigationItem[] {
  if (!channelId) return [];

  const root = `/channels/${channelId}`;
  return [
    { key: "dna", label: "DNA", href: root },
    { key: "branding", label: "Branding", href: `${root}/branding` },
    { key: "topics", label: "Topics", href: `${root}/topics` },
    { key: "research", label: "Research", href: `${root}/research` },
    { key: "content", label: "Content", href: `${root}/content` },
    { key: "production", label: "Production", href: `${root}/production` },
  ];
}

const globalRouteLabels = new Map(globalNavigation.map((item) => [item.href, item.label]));

export function getBreadcrumbs(pathname: string, channelName?: string): BreadcrumbItem[] {
  const exactLabel = globalRouteLabels.get(pathname);
  if (exactLabel) return [{ label: exactLabel }];

  if (pathname === "/channels/new") {
    return [{ label: "Channels", href: "/channels" }, { label: "New channel" }];
  }

  const channelMatch = pathname.match(/^\/channels\/[^/]+(?:\/(branding|topics|research|content|production))?$/);
  if (channelMatch) {
    const section = channelMatch[1];
    const items: BreadcrumbItem[] = [
      { label: "Channels", href: "/channels" },
      { label: channelName || "Channel" },
    ];
    if (section) items.push({ label: section[0].toUpperCase() + section.slice(1) });
    return items;
  }

  if (pathname === "/missions/new") {
    return [{ label: "Missions", href: "/missions" }, { label: "New mission" }];
  }

  if (/^\/missions\/[^/]+$/.test(pathname)) {
    return [{ label: "Missions", href: "/missions" }, { label: "Mission details" }];
  }

  return [{ label: "Workspace" }];
}
