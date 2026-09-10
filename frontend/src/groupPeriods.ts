/**
 * groupPeriods.ts
 *
 * Pure utility: collapses a flat DayPeriod[] (one element per institutional
 * slot) into "visual rows" for rendering, where a multi-slot logical entry
 * appears as ONE merged row instead of one row per slot.
 *
 * RULES
 * -----
 * 1. BREAK periods always become their own row (never merged).
 * 2. FREE slots (kind=SLOT, entry=null) always become their own row.
 * 3. Occupied SLOT periods are merged when they share the same entry.id.
 *    - The FIRST slot in the group becomes the "primary" row.
 *    - Subsequent slots for the same entry are "continuation" rows.
 * 4. The merged primary row's displayed time range spans from the first
 *    slot's start_time to the last slot's end_time.
 * 5. The slot-level DayPeriod[] is NOT mutated — it remains intact for
 *    availability counting and all other slot-level semantics.
 *
 * GROUPING KEY
 * ------------
 * entry.id  — already provided by the API. Two DayPeriod slots that belong
 * to the same logical schedule entry have the same entry.id.
 * We do NOT merge by subject name, type, or section.
 */

import type { DayPeriod } from './types';

export type PeriodRowKind = 'break' | 'free' | 'primary' | 'continuation';

export interface PeriodRow {
  kind: PeriodRowKind;
  /** The underlying period (the first slot for 'primary', that slot for others). */
  period: DayPeriod;
  /** For 'primary' rows: merged start time (first slot). Always period.start_time. */
  displayStart: string;
  /** For 'primary' rows: merged end time (last slot in the group). */
  displayEnd: string;
  /** For 'primary' rows: all slot codes this entry occupies, in order. */
  slotSpan: string[];
  /** For 'continuation' rows: the primary row's index in the output array. */
  primaryIndex?: number;
}

export function groupPeriods(periods: DayPeriod[]): PeriodRow[] {
  const rows: PeriodRow[] = [];
  // Track which entry IDs we have already opened a primary row for.
  // Maps entry.id -> index of its primary row in `rows`.
  const seen = new Map<string, number>();

  for (const period of periods) {
    // ── BREAK ──────────────────────────────────────────────────────────────
    if (period.kind === 'BREAK') {
      rows.push({
        kind: 'break',
        period,
        displayStart: period.start_time,
        displayEnd: period.end_time,
        slotSpan: [],
      });
      continue;
    }

    // ── FREE slot ──────────────────────────────────────────────────────────
    if (!period.entry) {
      rows.push({
        kind: 'free',
        period,
        displayStart: period.start_time,
        displayEnd: period.end_time,
        slotSpan: period.code ? [period.code] : [],
      });
      continue;
    }

    const entryId = period.entry.id;

    // ── Continuation slot (entry already started) ──────────────────────────
    if (seen.has(entryId)) {
      const primaryIdx = seen.get(entryId)!;
      // Extend the primary row's displayEnd and slotSpan.
      const primary = rows[primaryIdx];
      primary.displayEnd = period.end_time;
      if (period.code && !primary.slotSpan.includes(period.code)) {
        primary.slotSpan.push(period.code);
      }
      rows.push({
        kind: 'continuation',
        period,
        displayStart: period.start_time,
        displayEnd: period.end_time,
        slotSpan: [],
        primaryIndex: primaryIdx,
      });
      continue;
    }

    // ── Primary slot (first occurrence of this entry) ──────────────────────
    const primaryIdx = rows.length;
    seen.set(entryId, primaryIdx);
    rows.push({
      kind: 'primary',
      period,
      displayStart: period.start_time,
      displayEnd: period.end_time,
      slotSpan: period.code ? [period.code] : [],
    });
  }

  return rows;
}
