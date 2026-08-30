"use client";

import React, { useState } from "react";
import {
  createYouTubeAuthorizeUrl,
  disconnectPlatformAccount,
  executePublish,
  PlatformAccount,
  PublishAttempt,
  PublishIntent,
  UploadProgress,
} from "@/lib/api";

interface PublishingStatusCardProps {
  channelId: string;
  account: PlatformAccount | null;
  latestIntent?: PublishIntent | null;
  latestAttempt?: PublishAttempt | null;
  uploadProgress?: UploadProgress | null;
  onRefresh?: () => void;
}

export function PublishingStatusCard({
  channelId,
  account,
  latestIntent,
  latestAttempt,
  uploadProgress,
  onRefresh,
}: PublishingStatusCardProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleConnect = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await createYouTubeAuthorizeUrl(channelId);
      if (res.authorization_url) {
        window.location.href = res.authorization_url;
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to initiate YouTube authorization.");
      setLoading(false);
    }
  };

  const handleDisconnect = async () => {
    if (!account) return;
    if (!confirm("Are you sure you want to disconnect this YouTube channel account?")) return;

    try {
      setLoading(true);
      setError(null);
      await disconnectPlatformAccount(account.id, true);
      if (onRefresh) onRefresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to disconnect account.");
    } finally {
      setLoading(false);
    }
  };

  const handleExecute = async () => {
    if (!latestIntent) return;
    try {
      setLoading(true);
      setError(null);
      await executePublish(latestIntent.task_id);
      if (onRefresh) onRefresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to execute publication.");
    } finally {
      setLoading(false);
    }
  };

  const getStateColor = (state: string) => {
    switch (state) {
      case "PUBLISHED":
      case "SUCCEEDED":
        return "bg-emerald-950 text-emerald-400 border-emerald-800";
      case "UPLOADING":
      case "FINALIZING":
        return "bg-blue-950 text-blue-400 border-blue-800 animate-pulse";
      case "CLAIMED":
      case "APPROVED":
        return "bg-amber-950 text-amber-400 border-amber-800";
      case "RETRYABLE_FAILED":
      case "UNKNOWN":
        return "bg-orange-950 text-orange-400 border-orange-800";
      case "FAILED":
      case "PERMANENT_FAILED":
      case "BLOCKED_GUARDIAN":
        return "bg-red-950 text-red-400 border-red-800";
      default:
        return "bg-slate-900 text-slate-400 border-slate-700";
    }
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-xl space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-800 pb-3">
        <div className="flex items-center space-x-3">
          <div className="w-8 h-8 rounded-lg bg-red-600/20 text-red-500 flex items-center justify-center font-bold text-sm">
            ▶
          </div>
          <div>
            <h3 className="font-semibold text-slate-100 text-sm tracking-wide">YouTube Publisher</h3>
            <p className="text-xs text-slate-400">Official YouTube Data API v3 Distribution</p>
          </div>
        </div>

        {/* Account Status Badge */}
        {account ? (
          <div className="flex items-center space-x-2">
            <span
              className={`px-2.5 py-0.5 text-xs font-mono rounded-full border ${
                account.status === "ACTIVE"
                  ? "bg-emerald-950 text-emerald-400 border-emerald-800"
                  : "bg-red-950 text-red-400 border-red-800"
              }`}
            >
              {account.status}
            </span>
            <button
              onClick={handleDisconnect}
              disabled={loading}
              className="text-xs text-slate-400 hover:text-red-400 transition-colors"
            >
              Disconnect
            </button>
          </div>
        ) : (
          <button
            onClick={handleConnect}
            disabled={loading}
            className="px-3 py-1.5 bg-red-600 hover:bg-red-500 text-white text-xs font-medium rounded-lg shadow transition-colors"
          >
            {loading ? "Connecting..." : "Connect Channel"}
          </button>
        )}
      </div>

      {error && (
        <div className="p-3 bg-red-950/60 border border-red-800/80 rounded-lg text-xs text-red-300">
          {error}
        </div>
      )}

      {/* Account Details */}
      {account && (
        <div className="grid grid-cols-2 gap-3 text-xs bg-slate-950/50 p-3 rounded-lg border border-slate-800/50">
          <div>
            <span className="text-slate-500 block">Connected Channel</span>
            <span className="text-slate-200 font-medium">{account.account_display_name}</span>
          </div>
          <div>
            <span className="text-slate-500 block">External Account ID</span>
            <span className="text-slate-400 font-mono">{account.external_account_id}</span>
          </div>
        </div>
      )}

      {/* Publication Contract & Progress */}
      {latestIntent ? (
        <div className="space-y-3">
          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-400 font-medium">Publish Intent (v{latestIntent.revision_number})</span>
            <span className={`px-2 py-0.5 font-mono text-[10px] rounded border ${getStateColor(latestIntent.state)}`}>
              {latestIntent.state}
            </span>
          </div>

          <div className="space-y-1 bg-slate-950/80 p-3 rounded-lg border border-slate-800">
            <p className="text-xs font-medium text-slate-200 truncate">{latestIntent.title}</p>
            <div className="flex flex-wrap gap-2 text-[11px] text-slate-400 pt-1">
              <span>Requested: <strong className="text-slate-300">{latestIntent.requested_privacy_status}</strong></span>
              {latestAttempt?.effective_privacy_status && (
                <span>Effective: <strong className="text-emerald-400">{latestAttempt.effective_privacy_status}</strong></span>
              )}
              <span>Made for Kids: <strong className="text-slate-300">{latestIntent.made_for_kids ? "Yes" : "No"}</strong></span>
            </div>
          </div>

          {/* Upload Progress Bar */}
          {uploadProgress && !uploadProgress.is_complete && (
            <div className="space-y-1">
              <div className="flex justify-between text-[11px] text-slate-400">
                <span>Uploading Video Chunks</span>
                <span>{uploadProgress.progress_percentage}% ({Math.round(uploadProgress.bytes_uploaded / (1024 * 1024))}MB / {Math.round(uploadProgress.total_bytes / (1024 * 1024))}MB)</span>
              </div>
              <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-blue-500 transition-all duration-300"
                  style={{ width: `${uploadProgress.progress_percentage}%` }}
                />
              </div>
            </div>
          )}

          {/* Succeeded Result with YouTube URL */}
          {latestAttempt?.provider_video_id && (
            <div className="p-3 bg-emerald-950/40 border border-emerald-800/60 rounded-lg flex items-center justify-between text-xs">
              <div>
                <span className="text-emerald-400 font-semibold block">Published to YouTube</span>
                <span className="text-slate-400 font-mono text-[11px]">{latestAttempt.provider_video_id}</span>
              </div>
              {latestAttempt.provider_url && (
                <a
                  href={latestAttempt.provider_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="px-3 py-1 bg-emerald-600 hover:bg-emerald-500 text-slate-950 font-semibold rounded text-xs transition-colors"
                >
                  View on YouTube ↗
                </a>
              )}
            </div>
          )}

          {/* Failure & Diagnostic Reason */}
          {latestAttempt?.error_message && (
            <div className="p-3 bg-red-950/40 border border-red-800/60 rounded-lg text-xs space-y-1">
              <div className="flex items-center justify-between text-red-400 font-medium">
                <span>{latestAttempt.error_category || "Error"}</span>
                {latestAttempt.retry_after_seconds && (
                  <span className="text-slate-400 font-mono text-[10px]">Retry in {latestAttempt.retry_after_seconds}s</span>
                )}
              </div>
              <p className="text-slate-300 text-[11px]">{latestAttempt.error_message}</p>
            </div>
          )}

          {/* Manual Trigger Button for Approved Intent */}
          {latestIntent.state === "APPROVED" && (
            <button
              onClick={handleExecute}
              disabled={loading}
              className="w-full py-2 bg-blue-600 hover:bg-blue-500 text-white font-medium text-xs rounded-lg transition-colors shadow"
            >
              {loading ? "Executing..." : "Publish Now"}
            </button>
          )}
        </div>
      ) : (
        <div className="text-center py-4 text-xs text-slate-500 font-mono">
          No active publish intent for this channel.
        </div>
      )}
    </div>
  );
}
