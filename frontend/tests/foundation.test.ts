import assert from "node:assert/strict";
import test from "node:test";

import {
  formatCurrencyUsd,
  formatDecimal,
  toFiniteNumber,
} from "../src/lib/formatters.ts";
import {
  getBreadcrumbs,
  getChannelNavigation,
  globalNavigation,
} from "../src/lib/navigation.ts";
import { statusTone } from "../src/lib/presentation.ts";

test("statusTone classifies workflow outcomes", () => {
  assert.equal(statusTone("SUCCEEDED"), "success");
  assert.equal(statusTone("RUNNING"), "info");
  assert.equal(statusTone("INSUFFICIENT"), "warning");
  assert.equal(statusTone("BLOCKING"), "danger");
  assert.equal(statusTone("UNKNOWN"), "neutral");
});

test("numeric formatters safely handle API decimal values", () => {
  assert.equal(toFiniteNumber("12.50"), 12.5);
  assert.equal(toFiniteNumber("not-a-number"), null);
  assert.equal(formatCurrencyUsd("12.5"), "$12.50");
  assert.equal(formatDecimal(undefined), "N/A");
});

test("global navigation uses unique production routes", () => {
  const routes = globalNavigation.map((item) => item.href);
  assert.equal(new Set(routes).size, routes.length);
  assert.deepEqual(routes, [
    "/",
    "/missions",
    "/production",
    "/assets",
    "/publisher",
    "/analytics",
    "/learning",
    "/channels",
    "/schedule",
    "/autopilot",
    "/settings",
  ]);
});

test("channel navigation and breadcrumbs retain route context", () => {
  const channelId = "channel-123";
  const routes = getChannelNavigation(channelId);
  assert.equal(routes.length, 6);
  assert.equal(routes.at(-1)?.href, `/channels/${channelId}/production`);
  assert.deepEqual(
    getBreadcrumbs(`/channels/${channelId}/research`, "Demo channel"),
    [
      { label: "Channels", href: "/channels" },
      { label: "Demo channel" },
      { label: "Research" },
    ],
  );
});
