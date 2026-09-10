import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';

import { DaySchedulePeriods } from '../components/DaySchedulePeriods';
import { Shell } from '../components/Shell';
import { confirmTimetable, createTimetable, getTeacher, getTimetableDay, updateTimetable } from '../services/api';
import {
  ACADEMIC_YEAR_REGEX,
  DAY_LABELS,
  DAY_NAMES,
  FALLBACK_PERIODS,
  PROTOTYPE_ACADEMIC_YEAR,
  VALID_LAB_PAIRS,
  draftEntriesToPeriods,
  entryLabel,
  isValidAcademicYear,
  isValidLabPair,
  periodsToEntryPayloads,
  type DayName,
  type DayPeriod,
  type EntryType,
  type ScheduleEntryPayload,
  type TimetableConfirmResponse,
  type TimetableWritePayload,
} from '../types';

const ENTRY_TYPES: EntryType[] = ['CLASS', 'LAB', 'OTHER'];

type WeekEntries = Partial<Record<DayName, ScheduleEntryPayload[]>>;
type WeekTemplates = Partial<Record<DayName, DayPeriod[]>>;

const emptyForm = {
  entry_type: 'CLASS' as EntryType,
  subject_or_activity: '',
  section: '',
  room: '',
  notes: '',
  slot_ids: [] as string[],
};

function isNotFound(error: unknown): boolean {
  return axios.isAxiosError(error) && error.response?.status === 404;
}

function extractErrorDetail(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error) && typeof error.response?.data?.detail === 'string') {
    return error.response.data.detail;
  }
  return fallback;
}

export function TeacherTimetablePage() {
  const { teacherId } = useParams<{ teacherId: string }>();
  const teacherQuery = useQuery({
    queryKey: ['teacher', teacherId],
    queryFn: () => getTeacher(teacherId!),
    enabled: Boolean(teacherId),
  });

  const [selectedDay, setSelectedDay] = useState<DayName>('monday');
  // Pre-filled with the prototype academic year so teachers don't need to
  // retype it every session. The value is still sent explicitly in every
  // API call — there is no server-side default.
  const [academicYear, setAcademicYear] = useState(PROTOTYPE_ACADEMIC_YEAR);
  const [debouncedAcademicYear, setDebouncedAcademicYear] = useState(PROTOTYPE_ACADEMIC_YEAR);
  const [weekEntries, setWeekEntries] = useState<WeekEntries>({});
  const [weekTemplates, setWeekTemplates] = useState<WeekTemplates>({});
  const [loadedAcademicYear, setLoadedAcademicYear] = useState<string | null>(null);
  const [loadState, setLoadState] = useState<'idle' | 'loading' | 'loaded' | 'error'>('idle');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [formError, setFormError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [saveError, setSaveError] = useState<string | null>(null);
  const [publishState, setPublishState] = useState<'idle' | 'publishing' | 'published' | 'error'>('idle');
  const [publishError, setPublishError] = useState<string | null>(null);
  const [publishResult, setPublishResult] = useState<TimetableConfirmResponse | null>(null);

  useEffect(() => {
    const trimmed = academicYear.trim();
    const handle = setTimeout(() => setDebouncedAcademicYear(trimmed), 400);
    return () => clearTimeout(handle);
  }, [academicYear]);

  useEffect(() => {
    if (!teacherId || !debouncedAcademicYear || !isValidAcademicYear(debouncedAcademicYear)) {
      setLoadState('idle');
      setLoadError(null);
      setWeekEntries({});
      setWeekTemplates({});
      setLoadedAcademicYear(null);
      return;
    }
    const currentTeacherId = teacherId;
    const currentAcademicYear = debouncedAcademicYear;
    let cancelled = false;

    async function loadWeek() {
      setLoadState('loading');
      setLoadError(null);
      // Whatever was previously loaded (if anything) belongs to a
      // different academic year than the one now being fetched. Drop it
      // immediately rather than leaving it sitting in state, editable and
      // saveable, while this fetch is in flight.
      setWeekEntries({});
      setWeekTemplates({});
      setLoadedAcademicYear(null);

      const entries: WeekEntries = {};
      const templates: WeekTemplates = {};

      try {
        await Promise.all(
          DAY_NAMES.map(async (day) => {
            try {
              const response = await getTimetableDay(currentTeacherId, day, currentAcademicYear, 'DRAFT');
              entries[day] = periodsToEntryPayloads(response.periods);
              templates[day] = response.periods.map((period) => ({ ...period, entry: null }));
            } catch (error) {
              if (isNotFound(error)) {
                // Only a 404 means "no DRAFT for this teacher/year/day
                // yet" -- a CONFIRMED timetable existing for the same
                // year does not satisfy this request. Start this day
                // from an empty, fixed grid instead of failing the page.
                entries[day] = [];
                templates[day] = FALLBACK_PERIODS;
                return;
              }
              // Any other failure is a real error, not "no timetable yet"
              // -- abort the whole-week load instead of silently
              // rendering this day as empty.
              throw error;
            }
          }),
        );

        if (!cancelled) {
          setWeekEntries(entries);
          setWeekTemplates(templates);
          setLoadedAcademicYear(currentAcademicYear);
          setLoadState('loaded');
        }
      } catch (error) {
        if (!cancelled) {
          setLoadState('error');
          setLoadError(extractErrorDetail(error, 'Could not load the current timetable from the API.'));
        }
      }
    }

    loadWeek();

    return () => {
      cancelled = true;
    };
  }, [teacherId, debouncedAcademicYear]);

  const template = weekTemplates[selectedDay] ?? FALLBACK_PERIODS;
  const entriesForDay = weekEntries[selectedDay] ?? [];
  const previewPeriods = useMemo(() => draftEntriesToPeriods(template, entriesForDay), [template, entriesForDay]);

  const availableSlotCodes = useMemo(
    () => template.filter((period) => period.kind === 'SLOT' && period.code).map((period) => period.code as string),
    [template],
  );

  function toggleSlotCode(code: string) {
    setForm((current) => ({
      ...current,
      slot_ids: current.slot_ids.includes(code)
        ? current.slot_ids.filter((existing) => existing !== code)
        : [...current.slot_ids, code],
    }));
  }

  function handleAddEntry(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError(null);

    if (form.slot_ids.length === 0) {
      setFormError('Select at least one slot.');
      return;
    }

    // LAB must occupy exactly two consecutive working slots (no break between).
    if (form.entry_type === 'LAB') {
      if (!isValidLabPair(form.slot_ids)) {
        const validExamples = VALID_LAB_PAIRS.map(([a, b]) => `${a}+${b}`).join(', ');
        setFormError(
          `A LAB must occupy exactly two consecutive working slots (no break between). ` +
          `Valid pairs: ${validExamples}.`,
        );
        return;
      }
    }
    // Client-side duplicate-slot check mirrors the backend's same-payload
    // validation, so an obviously-contradictory entry is caught before a
    // round trip rather than only at save time.
    const alreadyClaimed = (weekEntries[selectedDay] ?? []).some((existing) =>
      existing.slot_ids.some((code) => form.slot_ids.includes(code)),
    );
    if (alreadyClaimed) {
      setFormError('One or more of these slots is already claimed by another entry on this day.');
      return;
    }

    const newEntry: ScheduleEntryPayload = {
      slot_ids: form.slot_ids,
      entry_type: form.entry_type,
      // Store null when subject is blank; the API will persist the entry_type
      // as the DB fallback so the NOT NULL constraint is satisfied.
      subject_or_activity: form.subject_or_activity.trim() || null,
      section: form.section.trim() || null,
      room: form.room.trim() || null,
      notes: form.notes.trim() || null,
    };

    setWeekEntries((current) => ({
      ...current,
      [selectedDay]: [...(current[selectedDay] ?? []), newEntry],
    }));
    setForm(emptyForm);
  }

  function handleRemoveEntry(indexToRemove: number) {
    setWeekEntries((current) => ({
      ...current,
      [selectedDay]: (current[selectedDay] ?? []).filter((_, index) => index !== indexToRemove),
    }));
  }

  async function handleSave() {
    if (!teacherId) return;

    const trimmedYear = academicYear.trim();
    if (!trimmedYear) {
      setSaveState('error');
      setSaveError('Academic year is required (e.g. 2025-2026).');
      return;
    }
    if (!isValidAcademicYear(trimmedYear)) {
      setSaveState('error');
      setSaveError('Academic year must be in YYYY-YYYY format where the second year is one after the first (e.g. 2025-2026).');
      return;
    }
    // The Save button is already disabled unless this holds, but re-check
    // here too: weekEntries only ever reflects the year it was fetched
    // for (loadedAcademicYear), and that must be the exact year being
    // submitted -- never a previously loaded year's data going out under
    // a newly typed one.
    if (loadState !== 'loaded' || trimmedYear !== loadedAcademicYear) {
      setSaveState('error');
      setSaveError("This academic year's draft hasn't finished loading yet. Please wait and try again.");
      return;
    }

    setSaveState('saving');
    setSaveError(null);

    const payload: TimetableWritePayload = {
      academic_year: trimmedYear,
      days: weekEntries,
    };

    try {
      try {
        await updateTimetable(teacherId, payload);
      } catch (error) {
        if (isNotFound(error)) {
          await createTimetable(teacherId, payload);
        } else {
          throw error;
        }
      }
      setSaveState('saved');
      // Reset publish state when a new save happens so the Approve panel
      // reflects the freshly saved draft (not a prior confirmed receipt).
      setPublishState('idle');
      setPublishError(null);
      setPublishResult(null);
    } catch (error) {
      setSaveState('error');
      setSaveError(extractErrorDetail(error, 'Could not save the timetable. Please try again.'));
    }
  }

  async function handlePublish() {
    if (!teacherId) return;
    const trimmedYear = academicYear.trim();
    if (!trimmedYear || !isValidAcademicYear(trimmedYear)) return;

    setPublishState('publishing');
    setPublishError(null);

    try {
      const result = await confirmTimetable(teacherId, trimmedYear);
      setPublishResult(result);
      setPublishState('published');
    } catch (error) {
      setPublishState('error');
      if (axios.isAxiosError(error)) {
        const status = error.response?.status;
        const detail = error.response?.data?.detail;
        if (status === 409) {
          setPublishError(
            typeof detail === 'string'
              ? detail
              : 'A confirmed timetable for this academic year already exists.',
          );
        } else if (status === 404) {
          setPublishError('No saved draft was found. Save the draft first, then approve.');
        } else if (status === 422) {
          const msg =
            typeof detail === 'string'
              ? detail
              : typeof detail?.message === 'string'
              ? detail.message
              : 'The draft has validation errors and cannot be approved.';
          setPublishError(msg);
        } else {
          setPublishError(typeof detail === 'string' ? detail : 'Could not approve the timetable. Please try again.');
        }
      } else {
        setPublishError('Could not approve the timetable. Please try again.');
      }
    }
  }

  return (
    <Shell>
      <section className="page-card">
        <Link className="back-link" to="/teacher">
          ← Teachers
        </Link>
        <div className="page-heading">
          <div>
            <p className="eyebrow">Day 1 · Timetable editor</p>
            <h1>{teacherQuery.data ? `${teacherQuery.data.name} (${teacherQuery.data.acronym})` : 'Timetable'}</h1>
            <p className="subtitle">Build the weekly draft schedule. Saving does not confirm the timetable yet.</p>
          </div>
          <span className="status-pill">DRAFT</span>
        </div>

        {teacherQuery.isError && <p className="error-text">Could not load this teacher. Check the teacher ID.</p>}

        <label className="academic-year-field">
          Academic year
          <input
            required
            value={academicYear}
            onChange={(event) => setAcademicYear(event.target.value)}
            placeholder="e.g. 2025-2026"
            pattern="\d{4}-\d{4}"
            title="Format: YYYY-YYYY (e.g. 2025-2026)"
          />
          {academicYear.trim() && !isValidAcademicYear(academicYear) && (
            <span className="field-hint field-hint--error">Format must be YYYY-YYYY, e.g. 2025-2026</span>
          )}
        </label>

        <div className="day-tabs">
          {DAY_NAMES.map((day) => (
            <button
              key={day}
              type="button"
              className={`day-tab ${day === selectedDay ? 'day-tab--active' : ''}`}
              onClick={() => setSelectedDay(day)}
            >
              {DAY_LABELS[day]}
            </button>
          ))}
        </div>

        {loadState === 'idle' && (
          <p className="muted">Enter an academic year above to load or start that year's draft timetable.</p>
        )}
        {loadState === 'loading' && <p className="muted">Loading current timetable…</p>}
        {loadState === 'error' && (
          <p className="error-text">{loadError ?? 'Could not load the current timetable from the API.'}</p>
        )}

        {loadState === 'loaded' && (
          <>
            <DaySchedulePeriods periods={previewPeriods} />

            <div className="entry-list-block">
              <h2>Entries for {DAY_LABELS[selectedDay]}</h2>
              {entriesForDay.length === 0 && <p className="muted">No entries yet for this day.</p>}
              <ul className="entry-manage-list">
                {entriesForDay.map((entry, index) => (
                  <li key={index} className="entry-manage-row">
                    <span className={`entry-type entry-type--${entry.entry_type.toLowerCase()}`}>
                      {entry.entry_type}
                    </span>
                    <span>{entryLabel({ entry_type: entry.entry_type, subject_or_activity: entry.subject_or_activity })}</span>
                    <span className="muted">{entry.slot_ids.join(', ')}</span>
                    <button type="button" className="link-button" onClick={() => handleRemoveEntry(index)}>
                      Remove
                    </button>
                  </li>
                ))}
              </ul>
            </div>

            <form className="form-grid" onSubmit={handleAddEntry}>
              <label>
                Type
                <select
                  value={form.entry_type}
                  onChange={(event) => setForm({ ...form, entry_type: event.target.value as EntryType })}
                >
                  {ENTRY_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                Subject / activity (optional)
                <input
                  value={form.subject_or_activity}
                  onChange={(event) => setForm({ ...form, subject_or_activity: event.target.value })}
                  placeholder="e.g. Database Management Systems"
                />
              </label>

              <label>
                Section (optional)
                <input value={form.section} onChange={(event) => setForm({ ...form, section: event.target.value })} />
              </label>

              <label>
                Room (optional)
                <input value={form.room} onChange={(event) => setForm({ ...form, room: event.target.value })} />
              </label>

              <label>
                Notes (optional)
                <input value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} />
              </label>

              <div className="slot-picker">
                <span>Slots (select every slot this entry occupies)</span>
                <div className="slot-picker-grid">
                  {availableSlotCodes.map((code) => (
                    <label key={code} className="slot-checkbox">
                      <input
                        type="checkbox"
                        checked={form.slot_ids.includes(code)}
                        onChange={() => toggleSlotCode(code)}
                      />
                      {code}
                    </label>
                  ))}
                </div>
              </div>

              <div className="form-actions">
                <button type="submit">Add entry to {DAY_LABELS[selectedDay]}</button>
                {formError && <p className="error-text">{formError}</p>}
              </div>
            </form>

            <div className="form-actions save-block">
              <button
                type="button"
                onClick={handleSave}
                disabled={
                  saveState === 'saving' ||
                  loadState !== 'loaded' ||
                  academicYear.trim() !== loadedAcademicYear
                }
              >
                {saveState === 'saving' ? 'Saving…' : 'Save draft timetable'}
              </button>
              {loadState === 'loaded' && academicYear.trim() !== loadedAcademicYear && (
                <p className="muted">Academic year changed -- waiting to load {academicYear.trim()}'s draft.</p>
              )}
              {saveState === 'saved' && <div className="success-box">Draft timetable saved.</div>}
              {saveState === 'error' && saveError && <p className="error-text">{saveError}</p>}
            </div>

            {/* ── Approve / Publish panel ─────────────────────────────── */}
            {publishState !== 'published' && (
              <div className="approve-panel">
                <div className="approve-panel__header">
                  <span className="approve-panel__icon">✓</span>
                  <div>
                    <h3 className="approve-panel__title">Approve &amp; Publish timetable</h3>
                    <p className="approve-panel__subtitle">
                      Once approved, the Director can see this timetable as the official schedule.
                      You must save the draft before approving.
                    </p>
                  </div>
                </div>
                <button
                  id="btn-approve-timetable"
                  type="button"
                  className="btn-approve"
                  onClick={handlePublish}
                  disabled={
                    publishState === 'publishing' ||
                    saveState !== 'saved' ||
                    loadState !== 'loaded' ||
                    academicYear.trim() !== loadedAcademicYear
                  }
                >
                  {publishState === 'publishing' ? 'Approving…' : 'Approve & Publish'}
                </button>
                {saveState !== 'saved' && loadState === 'loaded' && (
                  <p className="approve-panel__hint muted">
                    Save the draft first, then approve.
                  </p>
                )}
                {publishState === 'error' && publishError && (
                  <p className="error-text approve-panel__error">{publishError}</p>
                )}
              </div>
            )}

            {publishState === 'published' && publishResult && (
              <div className="approve-success">
                <span className="approve-success__icon">🎉</span>
                <div>
                  <p className="approve-success__title">Timetable approved and published!</p>
                  <p className="approve-success__meta">
                    {publishResult.entry_count} entr{publishResult.entry_count === 1 ? 'y' : 'ies'} confirmed
                    &nbsp;·&nbsp;
                    {new Date(publishResult.confirmed_at).toLocaleString()}
                  </p>
                  <p className="approve-success__note muted">
                    The Director can now view this teacher's availability.
                  </p>
                </div>
              </div>
            )}
          </>
        )}
      </section>
    </Shell>
  );
}
