/**
 * relative-time.tsx
 *
 * Shared relative-time formatting + auto-updating React component.
 *
 * Key correctness rule:
 *   The backend stores all datetimes with datetime.utcnow() — naive UTC, no "Z".
 *   JavaScript's Date constructor interprets bare strings WITHOUT a timezone as
 *   LOCAL time, which causes a ~5.5 h offset for IST users.
 *   We always normalise by appending "Z" before parsing so diff is correct
 *   regardless of the client's timezone.
 */
"use client";

import { useState, useEffect } from "react";

/** Normalise a backend datetime string to a proper UTC Date. */
function parseUTC(iso: string): Date {
  if (!iso) return new Date(0);
  // If the string already has timezone info (Z or +XX:XX), parse as-is.
  // Otherwise it came from Python's datetime.utcnow() — append Z.
  const normalised =
    iso.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(iso) ? iso : iso + "Z";
  return new Date(normalised);
}

/**
 * Format a UTC ISO timestamp as a human-friendly relative string.
 * Progression:  just now → X minutes ago → X hours ago → X days ago →
 *               X weeks ago → X months ago → X years ago
 */
export function formatRelative(iso: string): string {
  const diffMs = Date.now() - parseUTC(iso).getTime();
  const seconds = Math.floor(diffMs / 1000);

  if (seconds < 45)    return "just now";
  if (seconds < 90)    return "1 minute ago";

  const minutes = Math.floor(seconds / 60);
  if (minutes < 45)    return `${minutes} minutes ago`;
  if (minutes < 90)    return "1 hour ago";

  const hours = Math.floor(minutes / 60);
  if (hours < 22)      return `${hours} hours ago`;
  if (hours < 36)      return "1 day ago";

  const days = Math.floor(hours / 24);
  if (days < 6)        return `${days} days ago`;
  if (days < 10)       return "1 week ago";
  if (days < 25)       return `${Math.floor(days / 7)} weeks ago`;
  if (days < 45)       return "1 month ago";
  if (days < 345)      return `${Math.floor(days / 30)} months ago`;
  if (days < 548)      return "1 year ago";

  return `${Math.floor(days / 365)} years ago`;
}

/**
 * formatAbsolute — full date + time string for tooltips / event timestamps.
 * Correctly handles the same UTC normalisation.
 */
export function formatAbsolute(iso: string): string {
  if (!iso) return "";
  return parseUTC(iso).toLocaleString([], {
    month:  "short",
    day:    "numeric",
    hour:   "2-digit",
    minute: "2-digit",
  });
}

/**
 * RelativeTime — React component that auto-refreshes every 30 s.
 * Shows a tooltip with the absolute timestamp on hover.
 *
 * Usage:  <RelativeTime iso={ticket.updated_at} />
 */
export function RelativeTime({ iso, className }: { iso: string; className?: string }) {
  const [label, setLabel] = useState(() => formatRelative(iso));

  useEffect(() => {
    // Immediately recalculate on prop change
    setLabel(formatRelative(iso));

    // Re-render every 30 seconds so stale "X minutes ago" ticks forward
    const id = setInterval(() => setLabel(formatRelative(iso)), 30_000);
    return () => clearInterval(id);
  }, [iso]);

  return (
    <span
      className={className}
      title={formatAbsolute(iso)}
      style={{ cursor: "default" }}
    >
      {label}
    </span>
  );
}
