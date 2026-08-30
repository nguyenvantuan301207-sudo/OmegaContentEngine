"use client";

import { useEffect, useState } from "react";
import {
  NetworkRoute,
  RouteHealth,
  getRouteHealth,
  listNetworkRoutes,
} from "@/lib/api";

export function NetworkStatusCard() {
  const [routes, setRoutes] = useState<NetworkRoute[]>([]);
  const [health, setHealth] = useState<RouteHealth | null>(null);
  const [selectedRouteId, setSelectedRouteId] = useState<string>("");
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        const rts = await listNetworkRoutes();
        setRoutes(rts);
        if (rts.length > 0) {
          setSelectedRouteId(rts[0].id);
          const h = await getRouteHealth(rts[0].id);
          setHealth(h);
        }
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to load network status");
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  const handleRouteChange = async (routeId: string) => {
    setSelectedRouteId(routeId);
    try {
      const h = await getRouteHealth(routeId);
      setHealth(h);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to fetch route health");
    }
  };

  const selectedRoute = routes.find((r) => r.id === selectedRouteId);

  const getCircuitBadge = (state: string) => {
    switch (state) {
      case "CLOSED":
        return <span className="px-2 py-0.5 text-xs font-semibold rounded bg-emerald-950 text-emerald-400 border border-emerald-800">CLOSED (Operational)</span>;
      case "HALF_OPEN":
        return <span className="px-2 py-0.5 text-xs font-semibold rounded bg-amber-950 text-amber-400 border border-amber-800">HALF_OPEN (Testing)</span>;
      case "OPEN":
        return <span className="px-2 py-0.5 text-xs font-semibold rounded bg-rose-950 text-rose-400 border border-rose-800">OPEN (Blocked)</span>;
      default:
        return <span className="px-2 py-0.5 text-xs font-semibold rounded bg-zinc-800 text-zinc-400">UNKNOWN</span>;
    }
  };

  if (loading) {
    return <div className="p-4 bg-zinc-900 border border-zinc-800 rounded-lg text-zinc-400">Loading Network Status...</div>;
  }

  return (
    <div className="p-5 bg-zinc-900 border border-zinc-800 rounded-xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-base font-semibold text-zinc-100">Network Reliability & Egress</h3>
          <p className="text-xs text-zinc-400">OMEGA-009 Authoritative Route Health & Circuit Status</p>
        </div>
        {health && getCircuitBadge(health.circuit_state)}
      </div>

      {error && <div className="p-2 text-xs bg-rose-950/60 border border-rose-800 text-rose-300 rounded">{error}</div>}

      {routes.length === 0 ? (
        <div className="text-xs text-zinc-500 py-2">No egress network routes configured yet.</div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center space-x-2">
            <label htmlFor="route-selector" className="text-xs font-medium text-zinc-300">Route:</label>
            <select
              id="route-selector"
              value={selectedRouteId}
              onChange={(e) => handleRouteChange(e.target.value)}
              className="bg-zinc-800 border border-zinc-700 text-xs text-zinc-200 rounded px-2 py-1 focus:outline-none focus:border-zinc-500"
            >
              {routes.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name} ({r.route_type}) {r.is_enabled ? "" : "[Disabled]"}
                </option>
              ))}
            </select>
          </div>

          {selectedRoute && (
            <div className="grid grid-cols-2 gap-2 text-xs bg-zinc-950/60 p-3 rounded-lg border border-zinc-800/80">
              <div>
                <span className="text-zinc-500">Route Type:</span>{" "}
                <span className="text-zinc-200 font-mono">{selectedRoute.route_type}</span>
              </div>
              <div>
                <span className="text-zinc-500">TLS Verify:</span>{" "}
                <span className="text-emerald-400 font-semibold">{selectedRoute.tls_verify ? "Mandatory (Enforced)" : "Disabled"}</span>
              </div>
              <div>
                <span className="text-zinc-500">Config Version:</span>{" "}
                <span className="text-zinc-200 font-mono">v{selectedRoute.config_version}</span>
              </div>
              <div>
                <span className="text-zinc-500">Timeout:</span>{" "}
                <span className="text-zinc-200 font-mono">{selectedRoute.connect_timeout_seconds}s / {selectedRoute.read_timeout_seconds}s</span>
              </div>
            </div>
          )}

          {health && (
            <div className="text-xs space-y-1 text-zinc-400">
              <div className="flex justify-between">
                <span>Consecutive Failures:</span>
                <span className={health.consecutive_failures > 0 ? "text-rose-400 font-semibold" : "text-zinc-300"}>
                  {health.consecutive_failures}
                </span>
              </div>
              {health.cooldown_until && (
                <div className="flex justify-between text-amber-400">
                  <span>Circuit Cooldown Until:</span>
                  <span>{new Date(health.cooldown_until).toLocaleTimeString()}</span>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
