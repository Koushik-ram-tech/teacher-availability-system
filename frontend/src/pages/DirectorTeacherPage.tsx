import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';

import { Shell } from '../components/Shell';
import { getTeacher, getTimetableDay } from '../services/api';
import { groupPeriods } from '../groupPeriods';
import {
  DAY_LABELS,
  DAY_NAMES,
  FALLBACK_PERIODS,
  PROTOTYPE_ACADEMIC_YEAR,
  entryLabel,
  formatTimeRange,
  type DayName,
  type DayPeriod,
} from '../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function currentDayName(): DayName {
  const jsDay = new Date().getDay();
  const index = jsDay === 0 ? 0 : jsDay - 1;
  return DAY_NAMES[Math.min(index, DAY_NAMES.length - 1)];
}

function is404(err: unknown): boolean {
  return axios.isAxiosError(err) && err.response?.status === 404;
}

// ---------------------------------------------------------------------------
// Availability row — FREE, OCCUPIED, BREAK, or continuation
// ---------------------------------------------------------------------------

function SlotAvailabilityRow({ period, hasConfirmed }: { period: DayPeriod; hasConfirmed: boolean }) {
  if (period.kind === 'BREAK') {
    return (
      <div className="avail-row avail-row--break">
        <span className="avail-time">{formatTimeRange(period.start_time, period.end_time)}</span>
        <span className="avail-code avail-code--break">—</span>
        <div className="avail-status avail-status--break">
          <span className="avail-label-break">{period.label}</span>
        </div>
      </div>
    );
  }

  const occupied = Boolean(period.entry);

  return (
    <div className={`avail-row ${
      !hasConfirmed ? 'avail-row--unknown'
      : occupied ? 'avail-row--occupied'
      : 'avail-row--free'
    }`}>
      <span className="avail-time">{formatTimeRange(period.start_time, period.end_time)}</span>
      <span className="avail-code">{period.code}</span>

      {!hasConfirmed ? (
        <div className="avail-status avail-status--unknown">
          <span className="avail-unknown-badge">—</span>
          <span className="avail-unknown-label">No confirmed data</span>
        </div>
      ) : occupied ? (
        <div className="avail-status avail-status--occupied">
          <span className={`avail-entry-type entry-type entry-type--${period.entry!.entry_type.toLowerCase()}`}>
            {period.entry!.entry_type}
          </span>
          <span className="avail-subject">{entryLabel(period.entry!)}</span>
          {(period.entry!.section || period.entry!.room) && (
            <span className="avail-meta">
              {[period.entry!.section, period.entry!.room].filter(Boolean).join(' · ')}
            </span>
          )}
        </div>
      ) : (
        <div className="avail-status avail-status--free">
          <span className="avail-free-badge">FREE</span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Daily grid — multi-slot entries appear once; slot-level counts unchanged
// ---------------------------------------------------------------------------

function DailyGrid({
  periods,
  hasConfirmed,
}: {
  periods: DayPeriod[];
  hasConfirmed: boolean;
}) {
  // Availability counts are ALWAYS computed from the flat slot-level periods
  // (not from the grouped rows) so a LAB spanning S4+S5 counts as 2 occupied.
  const slotCount = periods.filter((p) => p.kind === 'SLOT').length;
  const occupied = periods.filter((p) => p.kind === 'SLOT' && p.entry).length;
  const free = slotCount - occupied;

  // Group periods into visual rows (multi-slot entries merged into one row).
  const rows = groupPeriods(periods);

  return (
    <div className="avail-grid">
      <div className="avail-summary-row">
        {hasConfirmed ? (
          <>
            <span className="avail-summary-chip avail-summary-chip--free">{free} Free</span>
            <span className="avail-summary-chip avail-summary-chip--occupied">{occupied} Occupied</span>
            <span className="avail-summary-chip avail-summary-chip--total">{slotCount} Total slots</span>
          </>
        ) : (
          <span className="avail-summary-chip avail-summary-chip--unknown">Availability unknown — no confirmed timetable</span>
        )}
      </div>
      <div className="avail-list">
        {rows.map((row, i) => {
          // BREAK rows — unchanged
          if (row.kind === 'break') {
            return (
              <SlotAvailabilityRow
                key={`break-${i}`}
                period={row.period}
                hasConfirmed={hasConfirmed}
              />
            );
          }

          // CONTINUATION rows — subtle connector, never shows activity label again
          if (row.kind === 'continuation') {
            return (
              <div
                key={row.period.code ?? `cont-${i}`}
                className={`avail-row ${
                  !hasConfirmed ? 'avail-row--unknown' : 'avail-row--occupied avail-row--continuation'
                }`}
                aria-hidden="true"
              >
                <span className="avail-time">{formatTimeRange(row.period.start_time, row.period.end_time)}</span>
                <span className="avail-code">{row.period.code}</span>
                <div className="avail-status avail-status--occupied">
                  {!hasConfirmed ? (
                    <><span className="avail-unknown-badge">—</span><span className="avail-unknown-label">No confirmed data</span></>
                  ) : (
                    <span className="avail-continuation-marker">↑ continued</span>
                  )}
                </div>
              </div>
            );
          }

          // FREE rows — unchanged
          if (row.kind === 'free') {
            return (
              <SlotAvailabilityRow
                key={row.period.code ?? `free-${i}`}
                period={row.period}
                hasConfirmed={hasConfirmed}
              />
            );
          }

          // PRIMARY row — full activity with merged time range
          const entry = row.period.entry!;
          const isMultiSlot = row.slotSpan.length > 1;

          return (
            <div
              key={row.period.code ?? `primary-${i}`}
              className="avail-row avail-row--occupied"
            >
              <span className="avail-time">
                {formatTimeRange(row.displayStart, row.displayEnd)}
                {isMultiSlot && (
                  <span className="avail-slot-span"> {row.slotSpan.join(' – ')}</span>
                )}
              </span>
              <span className="avail-code">{row.slotSpan[0] ?? row.period.code}</span>
              <div className="avail-status avail-status--occupied">
                {!hasConfirmed ? (
                  <><span className="avail-unknown-badge">—</span><span className="avail-unknown-label">No confirmed data</span></>
                ) : (
                  <>
                    <span className={`avail-entry-type entry-type entry-type--${entry.entry_type.toLowerCase()}`}>
                      {entry.entry_type}
                    </span>
                    <span className="avail-subject">{entryLabel(entry)}</span>
                    {(entry.section || entry.room) && (
                      <span className="avail-meta">
                        {[entry.section, entry.room].filter(Boolean).join(' · ')}
                      </span>
                    )}
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Weekly timetable matrix  — S1-S9 vs Mon-Sat
// Multi-slot entries: first slot shows the activity; subsequent slots show ↕
// ---------------------------------------------------------------------------

const SLOT_CODES_ORDERED = ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9'];

const SLOT_TIME_LABELS: Record<string, string> = {
  S1: '8:00',
  S2: '8:55',
  S3: '9:50',
  S4: '11:15',
  S5: '12:10',
  S6: '14:00',
  S7: '14:55',
  S8: '15:50',
  S9: '16:45',
};

interface WeeklyData {
  day: DayName;
  periods: DayPeriod[];
  hasConfirmed: boolean;  // false when this day had a 404 (no confirmed timetable)
}

function WeeklyMatrix({
  weekData,
  activeDay,
  onDayClick,
}: {
  weekData: WeeklyData[];
  activeDay: DayName;
  onDayClick: (d: DayName) => void;
}) {
  // Build lookup: day -> slotCode -> period
  const lookup = new Map<string, Map<string, DayPeriod | null>>();
  for (const { day, periods } of weekData) {
    const slotMap = new Map<string, DayPeriod | null>();
    for (const p of periods) {
      if (p.kind === 'SLOT' && p.code) slotMap.set(p.code, p);
    }
    lookup.set(day, slotMap);
  }

  // Track which entry IDs have already been rendered in each column (day).
  // This lets us detect continuation slots without mutating the data.
  // Map: day -> Set<entry.id>
  const renderedEntries = new Map<string, Set<string>>();
  for (const { day } of weekData) renderedEntries.set(day, new Set());

  const daysWithData = weekData.map((w) => w.day);

  return (
    <div className="weekly-matrix-wrap">
      <table className="weekly-matrix" aria-label="Weekly timetable matrix">
        <thead>
          <tr>
            <th className="wm-slot-header">Slot</th>
            {daysWithData.map((d) => (
              <th
                key={d}
                className={`wm-day-header ${d === activeDay ? 'wm-day-header--active' : ''}`}
              >
                <button className="wm-day-btn" onClick={() => onDayClick(d)}>
                  {DAY_LABELS[d].slice(0, 3)}
                </button>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {SLOT_CODES_ORDERED.map((code) => (
            <tr key={code} className="wm-row">
              <td className="wm-slot-cell">
                <span className="wm-slot-code">{code}</span>
                <span className="wm-slot-time">{SLOT_TIME_LABELS[code]}</span>
              </td>
              {daysWithData.map((day) => {
                const dayData = weekData.find((w) => w.day === day)!;
                const period = lookup.get(day)?.get(code) ?? null;
                const isActive = day === activeDay;

                // No confirmed timetable for this day
                if (!dayData.hasConfirmed) {
                  return (
                    <td key={day} className={`wm-cell wm-cell--unknown ${isActive ? 'wm-cell--day-active' : ''}`}>
                      <span className="wm-no-data">—</span>
                    </td>
                  );
                }

                if (!period) {
                  return (
                    <td key={day} className={`wm-cell wm-cell--unknown ${isActive ? 'wm-cell--day-active' : ''}`}>
                      <span className="wm-no-data">—</span>
                    </td>
                  );
                }

                if (period.entry) {
                  const entryId = period.entry.id;
                  const dayRendered = renderedEntries.get(day)!;

                  if (dayRendered.has(entryId)) {
                    // Continuation slot — show a subtle indicator, not the full label
                    return (
                      <td
                        key={day}
                        className={`wm-cell wm-cell--occupied wm-cell--continuation ${isActive ? 'wm-cell--day-active' : ''}`}
                        aria-hidden="true"
                        title={`${entryLabel(period.entry)} (continued)`}
                      >
                        <span className="wm-continuation">↕</span>
                      </td>
                    );
                  }

                  dayRendered.add(entryId);
                  return (
                    <td key={day} className={`wm-cell wm-cell--occupied ${isActive ? 'wm-cell--day-active' : ''}`}>
                      <span className={`wm-entry-type entry-type entry-type--${period.entry.entry_type.toLowerCase()}`}>
                        {period.entry.entry_type}
                      </span>
                      <span className="wm-subject">{entryLabel(period.entry)}</span>
                      {period.entry.section && (
                        <span className="wm-section">{period.entry.section}</span>
                      )}
                      {period.entry.slot_codes.length > 1 && (
                        <span className="wm-slot-span">{period.entry.slot_codes.join('–')}</span>
                      )}
                    </td>
                  );
                }

                return (
                  <td key={day} className={`wm-cell wm-cell--free ${isActive ? 'wm-cell--day-active' : ''}`}>
                    <span className="wm-free">FREE</span>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function DirectorTeacherPage() {
  const { teacherId } = useParams<{ teacherId: string }>();
  const [day, setDay] = useState<DayName>(currentDayName());
  const [academicYear, setAcademicYear] = useState(PROTOTYPE_ACADEMIC_YEAR);
  const [debouncedAcademicYear, setDebouncedAcademicYear] = useState(PROTOTYPE_ACADEMIC_YEAR);
  const [showWeekly, setShowWeekly] = useState(false);

  useEffect(() => {
    const trimmed = academicYear.trim();
    const handle = setTimeout(() => setDebouncedAcademicYear(trimmed), 400);
    return () => clearTimeout(handle);
  }, [academicYear]);

  // Teacher profile
  const teacherQuery = useQuery({
    queryKey: ['teacher', teacherId],
    queryFn: () => getTeacher(teacherId!),
    enabled: Boolean(teacherId),
  });

  // Single-day timetable (CONFIRMED)
  const dayQuery = useQuery({
    queryKey: ['timetable-day', teacherId, day, debouncedAcademicYear, 'CONFIRMED'],
    queryFn: () => getTimetableDay(teacherId!, day, debouncedAcademicYear, 'CONFIRMED'),
    enabled: Boolean(teacherId) && Boolean(debouncedAcademicYear),
    retry: false,
  });

  // Weekly: load all 6 days when weekly view is open
  // Each query is declared at the top level (rules-of-hooks compliant).
  // DAY_NAMES = [monday, tuesday, wednesday, thursday, friday, saturday].
  const wqEnabled = showWeekly && Boolean(teacherId) && Boolean(debouncedAcademicYear);
  const wqOptions = (d: DayName) => ({
    queryKey: ['timetable-day', teacherId, d, debouncedAcademicYear, 'CONFIRMED'] as const,
    queryFn: () => getTimetableDay(teacherId!, d, debouncedAcademicYear, 'CONFIRMED'),
    enabled: wqEnabled,
    retry: false,
  });
  const wq0 = useQuery(wqOptions('monday'));
  const wq1 = useQuery(wqOptions('tuesday'));
  const wq2 = useQuery(wqOptions('wednesday'));
  const wq3 = useQuery(wqOptions('thursday'));
  const wq4 = useQuery(wqOptions('friday'));
  const wq5 = useQuery(wqOptions('saturday'));
  const weeklyQueries = [wq0, wq1, wq2, wq3, wq4, wq5];

  const noConfirmedTimetable = is404(dayQuery.error);

  // Derive periods for the selected day.
  // When confirmed data exists, use it. When 404, use FALLBACK_PERIODS so the
  // grid layout is consistent, but pass hasConfirmed=false so every slot
  // renders as "—" (unknown), never as FREE.
  const activePeriods: DayPeriod[] =
    dayQuery.data?.periods ??
    (noConfirmedTimetable ? FALLBACK_PERIODS.map((p) => ({ ...p, entry: null })) : []);
  const activeHasConfirmed = dayQuery.isSuccess;

  // Build weekly matrix data.
  // hasConfirmed is true only when the query succeeded (not 404 or other error).
  const weekData: WeeklyData[] = DAY_NAMES.map((d, i) => ({
    day: d,
    hasConfirmed: weeklyQueries[i].isSuccess,
    periods:
      weeklyQueries[i].data?.periods ??
      FALLBACK_PERIODS.map((p) => ({ ...p, entry: null })),
  }));

  return (
    <Shell>
      <section className="page-card">
        <Link className="back-link" to="/director">← Search</Link>

        {/* Teacher header */}
        <div className="dir-teacher-header">
          {teacherQuery.data ? (
            <>
              <div className="dir-teacher-header__avatar">
                {teacherQuery.data.acronym.slice(0, 2)}
              </div>
              <div className="dir-teacher-header__info">
                <h1 className="dir-teacher-header__name">
                  {teacherQuery.data.name}
                  <span className="dir-teacher-header__acronym">({teacherQuery.data.acronym})</span>
                </h1>
                <p className="dir-teacher-header__meta">
                  {teacherQuery.data.level} &nbsp;·&nbsp;
                  {teacherQuery.data.program.name} &nbsp;·&nbsp;
                  {teacherQuery.data.department}
                </p>
              </div>
              <span className="status-pill">CONFIRMED</span>
            </>
          ) : teacherQuery.isLoading ? (
            <p className="muted">Loading teacher…</p>
          ) : (
            <p className="error-text">Could not load this teacher.</p>
          )}
        </div>

        {/* Controls row */}
        <div className="dir-controls-row">
          <label className="academic-year-field" style={{ marginBottom: 0 }}>
            Academic year
            <input
              id="director-academic-year"
              required
              value={academicYear}
              onChange={(e) => setAcademicYear(e.target.value)}
              placeholder="e.g. 2025-2026"
            />
          </label>

          <div className="dir-view-toggle">
            <button
              className={`dir-view-btn ${!showWeekly ? 'dir-view-btn--active' : ''}`}
              onClick={() => setShowWeekly(false)}
              id="btn-daily-view"
            >
              Daily
            </button>
            <button
              className={`dir-view-btn ${showWeekly ? 'dir-view-btn--active' : ''}`}
              onClick={() => setShowWeekly(true)}
              id="btn-weekly-view"
            >
              Weekly
            </button>
          </div>
        </div>

        {/* Day tabs — visible in both views */}
        <div className="day-tabs" role="tablist" aria-label="Select day">
          {DAY_NAMES.map((name) => (
            <button
              key={name}
              role="tab"
              aria-selected={name === day}
              type="button"
              className={`day-tab ${name === day ? 'day-tab--active' : ''}`}
              onClick={() => { setDay(name); setShowWeekly(false); }}
              id={`day-tab-${name}`}
            >
              {DAY_LABELS[name]}
            </button>
          ))}
        </div>

        {/* Content */}
        {!debouncedAcademicYear && (
          <p className="muted">Enter an academic year above to view the schedule.</p>
        )}

        {debouncedAcademicYear && !showWeekly && (
          <>
            {dayQuery.isLoading && <p className="muted">Loading schedule…</p>}

            {noConfirmedTimetable && (
              <div className="dir-no-timetable">
                <span aria-hidden className="dir-no-timetable__icon">📭</span>
                <p>
                  No <strong>confirmed</strong> timetable for this teacher in{' '}
                  <strong>{debouncedAcademicYear}</strong>. Showing all slots as free.
                </p>
                <p className="dir-no-timetable__hint">
                  A draft may exist — only the Director can see confirmed schedules here.
                </p>
              </div>
            )}

            {dayQuery.isError && !noConfirmedTimetable && (
              <p className="error-text">
                Could not load schedule for {DAY_LABELS[day]}. Please try again.
              </p>
            )}

            {(dayQuery.isSuccess || noConfirmedTimetable) && activePeriods.length > 0 && (
              <>
                <h2 className="dir-section-title">
                  {DAY_LABELS[day]} availability
                  {noConfirmedTimetable && (
                    <span className="dir-no-data-badge">No confirmed data</span>
                  )}
                </h2>
                <DailyGrid periods={activePeriods} hasConfirmed={activeHasConfirmed} />
              </>
            )}
          </>
        )}

        {debouncedAcademicYear && showWeekly && (
          <>
            <h2 className="dir-section-title">Weekly timetable — {debouncedAcademicYear}</h2>
            {weeklyQueries.every((q) => q.isLoading) ? (
              <p className="muted">Loading weekly schedule…</p>
            ) : (
              <WeeklyMatrix
                weekData={weekData}
                activeDay={day}
                onDayClick={(d) => { setDay(d); setShowWeekly(false); }}
              />
            )}
          </>
        )}
      </section>
    </Shell>
  );
}
