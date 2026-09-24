import React, { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { Shell } from '../components/Shell';
import { searchTeachers, getTeacherAvailability, getResourceAvailabilityByCode } from '../services/api';
import type { Teacher, TeacherAvailability, ResourceAvailability, AvailabilityStatus } from '../types';

const DEBOUNCE_MS = 300;

// Working days (Mon-Sat, Sunday excluded)
const WORKING_DAYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'] as const;
const DAY_LABELS: Record<string, string> = {
  monday: 'Mon',
  tuesday: 'Tue',
  wednesday: 'Wed',
  thursday: 'Thu',
  friday: 'Fri',
  saturday: 'Sat',
};

// Slot structure with time information
const SLOTS = [
  { code: 'S1', time: '08:00–08:55', isBreak: false },
  { code: 'S2', time: '08:55–09:50', isBreak: false },
  { code: 'S3', time: '09:50–10:45', isBreak: false },
  { code: 'BREAK', label: 'Break', time: '10:45–11:15', isBreak: true },
  { code: 'S4', time: '11:15–12:10', isBreak: false },
  { code: 'S5', time: '12:10–13:05', isBreak: false },
  { code: 'LUNCH', label: 'Lunch', time: '13:05–14:00', isBreak: true },
  { code: 'S6', time: '14:00–14:55', isBreak: false },
  { code: 'S7', time: '14:55–15:50', isBreak: false },
  { code: 'S8', time: '15:50–16:45', isBreak: false },
  { code: 'S9', time: '16:45–17:40', isBreak: false },
] as const;

type SearchMode = 'teacher' | 'resource';

function AvailabilityGrid({
  data,
  mode,
}: {
  data: TeacherAvailability | ResourceAvailability;
  mode: SearchMode;
}) {
  const [selectedSlot, setSelectedSlot] = useState<{
    day: string;
    slot: string;
    info: { subject?: string | null; section?: string | null; room?: string | null };
  } | null>(null);

  const getSlotStatus = (day: string, slotCode: string): AvailabilityStatus | null => {
    const dayData = data.days[day];
    if (!dayData) return null;
    const slot = dayData.slots[slotCode];
    return slot?.status ?? null;
  };

  const getSlotInfo = (day: string, slotCode: string) => {
    const dayData = data.days[day];
    if (!dayData) return null;
    const slot = dayData.slots[slotCode];
    if (!slot || slot.status !== 'OCCUPIED') return null;
    return {
      subject: slot.subject_or_activity,
      section: slot.section,
      room: slot.room,
    };
  };

  const handleSlotClick = (day: string, slotCode: string) => {
    const info = getSlotInfo(day, slotCode);
    if (info) {
      setSelectedSlot({ day, slot: slotCode, info });
    }
  };

  // Build human-readable aria-label for a status cell.
  const cellAriaLabel = (day: string, slotCode: string, status: AvailabilityStatus | null): string => {
    const dayLabel = DAY_LABELS[day] ?? day;
    if (!status || (mode === 'teacher' && status === 'FREE')) {
      return `${dayLabel} ${slotCode}: no scheduled activity`;
    }
    if (status === 'FREE') return `${dayLabel} ${slotCode}: free`;
    if (status === 'OCCUPIED') return `${dayLabel} ${slotCode}: occupied`;
    return `${dayLabel} ${slotCode}: unknown`;
  };

  const renderStatusCell = (day: string, slotCode: string) => {
    const status = getSlotStatus(day, slotCode);

    // Teacher view: FREE slots render as a neutral dash — no "FREE" text.
    // Resource view: FREE renders as normal status text.
    if (!status || (mode === 'teacher' && status === 'FREE')) {
      return (
        <td
          className="avail-cell avail-cell--free-teacher"
          aria-label={cellAriaLabel(day, slotCode, status)}
        >
          <span className="avail-status-text" aria-hidden>—</span>
        </td>
      );
    }

    const hasInfo = status === 'OCCUPIED' && getSlotInfo(day, slotCode);
    const cellClass = `avail-cell avail-cell--${status.toLowerCase()}${hasInfo ? ' avail-cell--clickable' : ''}`;

    return (
      <td
        className={cellClass}
        onClick={hasInfo ? () => handleSlotClick(day, slotCode) : undefined}
        role={hasInfo ? 'button' : undefined}
        tabIndex={hasInfo ? 0 : undefined}
        aria-label={cellAriaLabel(day, slotCode, status)}
        onKeyDown={
          hasInfo
            ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  handleSlotClick(day, slotCode);
                }
              }
            : undefined
        }
      >
        <span className="avail-status-text">{status}</span>
      </td>
    );
  };

  return (
    <div className="avail-grid-container">
      <div className="avail-info-panel">
        {mode === 'teacher' && 'teacher_name' in data && (
          <div className="avail-info">
            <h3>{data.teacher_name}</h3>
            <p className="muted">
              {data.teacher_acronym}
              {data.teacher_department && ` · ${data.teacher_department}`}
            </p>
            <p className="muted">Academic Year: {data.academic_year}</p>
          </div>
        )}
        {mode === 'resource' && 'resource_name' in data && (
          <div className="avail-info">
            <h3>{data.resource_name}</h3>
            <p className="muted">
              {data.resource_type}
            </p>
            <p className="muted">Academic Year: {data.academic_year}</p>
          </div>
        )}
      </div>

      <div className="avail-table-wrapper">
        <table className="avail-table">
          <thead>
            <tr>
              <th className="avail-header-slot">Slot</th>
              <th className="avail-header-time">Time</th>
              {WORKING_DAYS.map((day) => (
                <th key={day} className="avail-header-day">
                  {DAY_LABELS[day]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {SLOTS.map((slot) =>
              slot.isBreak ? (
                <tr key={slot.code} className="avail-row-break">
                  <td className="avail-cell-break-label" colSpan={2}>
                    {slot.label}
                  </td>
                  <td className="avail-cell-break" colSpan={WORKING_DAYS.length}>
                    {slot.time}
                  </td>
                </tr>
              ) : (
                <tr key={slot.code}>
                  <td className="avail-cell-slot">{slot.code}</td>
                  <td className="avail-cell-time">{slot.time}</td>
                  {WORKING_DAYS.map((day) => (
                    <React.Fragment key={`${day}-${slot.code}`}>
                      {renderStatusCell(day, slot.code)}
                    </React.Fragment>
                  ))}
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>

      {selectedSlot && (
        <div className="avail-detail-modal" onClick={() => setSelectedSlot(null)}>
          <div className="avail-detail-card" onClick={(e) => e.stopPropagation()}>
            <button className="avail-detail-close" onClick={() => setSelectedSlot(null)} aria-label="Close">
              ✕
            </button>
            <h4>
              {DAY_LABELS[selectedSlot.day]} · {selectedSlot.slot}
            </h4>
            <dl className="avail-detail-list">
              {selectedSlot.info.subject && (
                <>
                  <dt>Subject/Activity</dt>
                  <dd>{selectedSlot.info.subject}</dd>
                </>
              )}
              {selectedSlot.info.section && (
                <>
                  <dt>Section</dt>
                  <dd>{selectedSlot.info.section}</dd>
                </>
              )}
              {selectedSlot.info.room && (
                <>
                  <dt>Room</dt>
                  <dd>{selectedSlot.info.room}</dd>
                </>
              )}
            </dl>
          </div>
        </div>
      )}

      <div className="avail-legend">
        {/* Teacher mode: FREE slots are intentionally not shown — they appear as a neutral dash.
            Resource mode: FREE is a meaningful status that must remain visible. */}
        {mode === 'resource' && (
          <span className="avail-legend-item">
            <span className="avail-legend-box avail-legend-box--free" />
            FREE
          </span>
        )}
        <span className="avail-legend-item">
          <span className="avail-legend-box avail-legend-box--occupied" />
          OCCUPIED
        </span>
        {mode === 'teacher' && (
          <span className="avail-legend-item">
            <span className="avail-legend-box avail-legend-box--free-teacher" />
            No activity
          </span>
        )}
        <span className="avail-legend-item">
          <span className="avail-legend-box avail-legend-box--unknown" />
          UNKNOWN
        </span>
      </div>
    </div>
  );
}

function TeacherSearchPanel({
  onSelect,
  academicYear,
}: {
  onSelect: (teacher: Teacher) => void;
  academicYear: string;
}) {
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQuery(query.trim()), DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [query]);

  const searchQuery = useQuery({
    queryKey: ['teacher-search', debouncedQuery],
    queryFn: () => searchTeachers(debouncedQuery),
    enabled: debouncedQuery.length > 0,
  });

  const teachers = searchQuery.data ?? [];

  return (
    <div className="avail-search-panel">
      <div className="avail-search-box">
        <span className="avail-search-icon" aria-hidden>
          🔍
        </span>
        <input
          ref={inputRef}
          className="avail-search-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name or acronym…"
          autoFocus
          autoComplete="off"
          spellCheck={false}
          aria-label="Search teacher"
        />
        {query && (
          <button
            className="avail-search-clear"
            onClick={() => {
              setQuery('');
              setDebouncedQuery('');
              inputRef.current?.focus();
            }}
            aria-label="Clear search"
          >
            ✕
          </button>
        )}
      </div>

      {!debouncedQuery && (
        <div className="avail-empty-state">
          <span className="avail-empty-icon" aria-hidden>
            👤
          </span>
          <p>Search for a teacher by name or acronym</p>
        </div>
      )}

      {debouncedQuery && searchQuery.isLoading && (
        <div className="avail-empty-state">
          <p className="muted">Searching…</p>
        </div>
      )}

      {debouncedQuery && searchQuery.isError && (
        <div className="avail-empty-state">
          <p className="error-text">Search failed. Check backend connection.</p>
        </div>
      )}

      {debouncedQuery && searchQuery.isSuccess && teachers.length === 0 && (
        <div className="avail-empty-state">
          <span className="avail-empty-icon" aria-hidden>
            😶
          </span>
          <p>
            No teachers matched <strong>"{debouncedQuery}"</strong>
          </p>
        </div>
      )}

      {teachers.length > 0 && (
        <div className="avail-results">
          <p className="avail-results-count">
            {teachers.length} result{teachers.length !== 1 ? 's' : ''}
          </p>
          <div className="avail-teacher-list">
            {teachers.map((t) => (
              <button
                key={t.id}
                className="avail-teacher-card"
                onClick={() => onSelect(t)}
              >
                <div className="avail-teacher-avatar">{t.acronym.slice(0, 2)}</div>
                <div className="avail-teacher-info">
                  <strong>{t.name}</strong>
                  <span className="muted">
                    {t.acronym} · {t.department}
                  </span>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ResourceSearchPanel({
  onSelect,
  academicYear,
}: {
  onSelect: (code: string) => void;
  academicYear: string;
}) {
  const [code, setCode] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = code.trim();
    if (trimmed) {
      onSelect(trimmed);
    }
  };

  return (
    <div className="avail-search-panel">
      <form onSubmit={handleSubmit}>
        <div className="avail-search-box">
          <span className="avail-search-icon" aria-hidden>
            🏫
          </span>
          <input
            ref={inputRef}
            className="avail-search-input"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="Enter resource code (e.g., LAB1A, LAB 1A, I-A)…"
            autoFocus
            autoComplete="off"
            spellCheck={false}
            aria-label="Search resource"
          />
          {code && (
            <button
              type="button"
              className="avail-search-clear"
              onClick={() => {
                setCode('');
                inputRef.current?.focus();
              }}
              aria-label="Clear"
            >
              ✕
            </button>
          )}
        </div>
        <button type="submit" className="avail-search-submit" disabled={!code.trim()}>
          Search Resource
        </button>
      </form>

      <div className="avail-empty-state">
        <span className="avail-empty-icon" aria-hidden>
          🏫
        </span>
        <p>Enter a classroom, lab, or resource code</p>
        <p className="muted" style={{ fontSize: '0.875rem', marginTop: '0.5rem' }}>
          Examples: LAB1A, LAB 1A, I-A, II-B
        </p>
      </div>
    </div>
  );
}

export function AvailabilityPage() {
  const [mode, setMode] = useState<SearchMode>('teacher');
  const [academicYear, setAcademicYear] = useState('2026-2027');
  const [selectedTeacher, setSelectedTeacher] = useState<Teacher | null>(null);
  const [selectedResourceCode, setSelectedResourceCode] = useState<string | null>(null);

  const teacherAvailQuery = useQuery({
    queryKey: ['teacher-availability', selectedTeacher?.id, academicYear],
    queryFn: () => getTeacherAvailability(selectedTeacher!.id, academicYear),
    enabled: mode === 'teacher' && !!selectedTeacher,
  });

  const resourceAvailQuery = useQuery({
    queryKey: ['resource-availability', selectedResourceCode, academicYear],
    queryFn: () => getResourceAvailabilityByCode(selectedResourceCode!, academicYear),
    enabled: mode === 'resource' && !!selectedResourceCode,
    retry: false,
  });

  const handleModeChange = (newMode: SearchMode) => {
    setMode(newMode);
    setSelectedTeacher(null);
    setSelectedResourceCode(null);
  };

  const handleTeacherSelect = (teacher: Teacher) => {
    setSelectedTeacher(teacher);
  };

  const handleResourceSelect = (code: string) => {
    setSelectedResourceCode(code);
  };

  const isLoading =
    (mode === 'teacher' && teacherAvailQuery.isLoading) ||
    (mode === 'resource' && resourceAvailQuery.isLoading);

  const isError =
    (mode === 'teacher' && teacherAvailQuery.isError) ||
    (mode === 'resource' && resourceAvailQuery.isError);

  const availabilityData =
    mode === 'teacher' ? teacherAvailQuery.data : resourceAvailQuery.data;

  return (
    <Shell>
      <section className="page-card">
        <Link className="back-link" to="/">
          ← Home
        </Link>

        <div className="avail-hero">
          <span className="eyebrow">Availability</span>
          <h1>Teacher & Resource Availability</h1>
          <p className="subtitle">Check who is free and when — the primary director-facing tool</p>
        </div>

        <div className="avail-controls">
          <div className="avail-mode-tabs">
            <button
              className={`avail-mode-tab ${mode === 'teacher' ? 'avail-mode-tab--active' : ''}`}
              onClick={() => handleModeChange('teacher')}
            >
              Teacher
            </button>
            <button
              className={`avail-mode-tab ${mode === 'resource' ? 'avail-mode-tab--active' : ''}`}
              onClick={() => handleModeChange('resource')}
            >
              Classroom / Resource
            </button>
          </div>

          <div className="avail-year-selector">
            <label htmlFor="academic-year">Academic Year:</label>
            <select
              id="academic-year"
              value={academicYear}
              onChange={(e) => setAcademicYear(e.target.value)}
            >
              <option value="2026-2027">2026-2027</option>
              <option value="2026-2027">2026-Odd</option>
              <option value="2026-Even">2026-Even</option>
              <option value="2025-2026">2025-2026</option>
              <option value="2025-Odd">2025-Odd</option>
              <option value="2025-Even">2025-Even</option>
            </select>
          </div>
        </div>

        {!selectedTeacher && !selectedResourceCode && (
          <>
            {mode === 'teacher' && (
              <TeacherSearchPanel onSelect={handleTeacherSelect} academicYear={academicYear} />
            )}
            {mode === 'resource' && (
              <ResourceSearchPanel onSelect={handleResourceSelect} academicYear={academicYear} />
            )}
          </>
        )}

        {(selectedTeacher || selectedResourceCode) && (
          <div className="avail-result-section">
            <button
              className="avail-back-btn"
              onClick={() => {
                setSelectedTeacher(null);
                setSelectedResourceCode(null);
              }}
            >
              ← Search again
            </button>

            {isLoading && (
              <div className="avail-loading-state">
                <p>Loading availability…</p>
              </div>
            )}

            {isError && (
              <div className="avail-error-state">
                <p className="error-text">
                  {mode === 'teacher'
                    ? 'Failed to load teacher availability. The teacher may not have a confirmed timetable for this academic year.'
                    : 'Failed to load resource availability. The resource code may not exist or have no timetable data.'}
                </p>
              </div>
            )}

            {availabilityData && <AvailabilityGrid data={availabilityData} mode={mode} />}
          </div>
        )}
      </section>
    </Shell>
  );
}
