import type { ReactNode } from 'react';

import { groupPeriods } from '../groupPeriods';
import { entryLabel, formatTimeRange, type DayPeriod } from '../types';

interface DaySchedulePeriodsProps {
  periods: DayPeriod[];
  renderSlotActions?: (period: DayPeriod) => ReactNode;
}

/**
 * Renders a day's period list with multi-slot logical entries merged into a
 * single visual row.
 *
 * Grouping: uses entry.id as the stable merge key (provided by the API).
 * Slot-level DayPeriod[] is never mutated; slot occupancy semantics are
 * preserved by the caller.
 *
 * Each merged primary row shows:
 *   - Combined time range  (first-slot start_time → last-slot end_time)
 *   - Slot span label      (e.g. "S4 – S5")
 *   - Entry type badge
 *   - Subject / activity
 *   - Section · Room
 *
 * Continuation rows render as a visually subordinate connector — they do NOT
 * repeat the activity label.
 */
export function DaySchedulePeriods({ periods, renderSlotActions }: DaySchedulePeriodsProps) {
  const rows = groupPeriods(periods);

  return (
    <ol className="period-list">
      {rows.map((row, index) => {
        // ── BREAK ──────────────────────────────────────────────────────────
        if (row.kind === 'break') {
          return (
            <li className="period-row period-row--break" key={`break-${index}`}>
              <span className="period-time">{formatTimeRange(row.period.start_time, row.period.end_time)}</span>
              <span className="period-label">{row.period.label}</span>
              <span className="period-status">Fixed break</span>
            </li>
          );
        }

        // ── FREE slot ──────────────────────────────────────────────────────
        if (row.kind === 'free') {
          return (
            <li className="period-row" key={row.period.code ?? index}>
              <span className="period-time">{formatTimeRange(row.period.start_time, row.period.end_time)}</span>
              <span className="period-label">{row.period.code}</span>
              <span className="period-status period-status--empty">No scheduled activity</span>
              {renderSlotActions ? <div className="period-actions">{renderSlotActions(row.period)}</div> : null}
            </li>
          );
        }

        // ── CONTINUATION slot — subtle connector, no repeated label ────────
        if (row.kind === 'continuation') {
          return (
            <li className="period-row period-row--continuation" key={row.period.code ?? index} aria-hidden="true">
              <span className="period-time">{formatTimeRange(row.period.start_time, row.period.end_time)}</span>
              <span className="period-label">{row.period.code}</span>
              <span className="period-continuation-marker">↑ continued</span>
            </li>
          );
        }

        // ── PRIMARY row — full activity display ────────────────────────────
        const entry = row.period.entry!;
        const isMultiSlot = row.slotSpan.length > 1;

        return (
          <li className={`period-row ${isMultiSlot ? 'period-row--multi' : ''}`} key={row.period.code ?? index}>
            <span className="period-time">
              {formatTimeRange(row.displayStart, row.displayEnd)}
              {isMultiSlot && (
                <span className="period-slot-span"> {row.slotSpan.join(' – ')}</span>
              )}
            </span>
            <span className="period-label">{row.slotSpan[0] ?? row.period.code}</span>
            <div className="period-entry">
              <span className={`entry-type entry-type--${entry.entry_type.toLowerCase()}`}>
                {entry.entry_type}
              </span>
              <span className="entry-subject">{entryLabel(entry)}</span>
              {(entry.section || entry.room) && (
                <span className="entry-meta">
                  {[entry.section, entry.room].filter(Boolean).join(' · ')}
                </span>
              )}
            </div>
            {renderSlotActions ? <div className="period-actions">{renderSlotActions(row.period)}</div> : null}
          </li>
        );
      })}
    </ol>
  );
}
