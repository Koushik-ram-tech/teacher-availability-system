export type Level = 'UG' | 'PG';

export interface Program {
  id: string;
  name: string;
  level: Level;
  is_active: boolean;
}

export interface Teacher {
  id: string;
  name: string;
  acronym: string;
  level: Level;
  program_id: string;
  semester: number;
  department: string;
  is_active: boolean;
  program: Program;
}

export interface TeacherCreatePayload {
  name: string;
  acronym: string;
  level: Level;
  program_id: string;
  semester: number;
  department: string;
}

// ---------------------------------------------------------------------------
// Timetable (Day-1 slice)
//
// academic_year is required on every write, with no server-side default.
// The UI must collect it explicitly and send it verbatim on every save.
// The same value is also required on every read: multiple draft/confirmed
// timetables can exist for a teacher across different academic years, so
// a read must state which year it means rather than relying on any
// "most recent" or "current" resolution.
//
// PROTOTYPE_ACADEMIC_YEAR is the configured default for the prototype
// deployment. It is NOT derived from the current date and NOT used as a
// server-side default — it is only used to pre-fill the UI field so that
// teachers don't have to type it on every session.
// ---------------------------------------------------------------------------

/** Prototype default academic year shown in the UI. Change this here only;
 * the API/DB always store academic_year explicitly. */
export const PROTOTYPE_ACADEMIC_YEAR = '2025-2026';

export type EntryType = 'CLASS' | 'LAB' | 'OTHER';

// Monday-Saturday only. Sunday is intentionally not offered anywhere in
// the UI, matching docs/TIMETABLE_RULES.md ("Sunday is not a working day
// in the prototype").
export type DayName = 'monday' | 'tuesday' | 'wednesday' | 'thursday' | 'friday' | 'saturday';

export const DAY_NAMES: DayName[] = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];

export const DAY_LABELS: Record<DayName, string> = {
  monday: 'Monday',
  tuesday: 'Tuesday',
  wednesday: 'Wednesday',
  thursday: 'Thursday',
  friday: 'Friday',
  saturday: 'Saturday',
};

export type TimetableStatus = 'DRAFT' | 'CONFIRMED';

export interface ScheduleEntryPayload {
  slot_ids: string[];
  entry_type: EntryType;
  /** Subject or activity name. Optional — when omitted the entry_type is
   * used as the display label. The API stores a non-empty value in the DB
   * by falling back to the entry_type when this is blank. */
  subject_or_activity?: string | null;
  section?: string | null;
  room?: string | null;
  notes?: string | null;
}

export interface TimetableWritePayload {
  academic_year: string;
  days: Partial<Record<DayName, ScheduleEntryPayload[]>>;
}

export interface ScheduleEntryView {
  id: string;
  entry_type: EntryType;
  /** Subject or activity name stored in the DB. May equal the entry_type
   * string when the teacher did not provide one (DB fallback). */
  subject_or_activity?: string | null;
  section?: string | null;
  room?: string | null;
  notes?: string | null;
  slot_codes: string[];
}

/** Returns the display label for a schedule entry.
 * Falls back to the entry_type when subject_or_activity is empty or
 * matches the entry_type exactly (i.e. was stored as the DB fallback). */
export function entryLabel(entry: Pick<ScheduleEntryView, 'entry_type' | 'subject_or_activity'>): string {
  const subj = entry.subject_or_activity?.trim();
  if (!subj || subj === entry.entry_type) return entry.entry_type;
  return subj;
}

export interface TimetableWriteResponse {
  id: string;
  teacher_id: string;
  academic_year: string;
  status: TimetableStatus;
  source: 'MANUAL' | 'IMPORT';
  days: Partial<Record<DayName, ScheduleEntryView[]>>;
}

export interface DayPeriod {
  kind: 'SLOT' | 'BREAK';
  code?: string | null;
  label?: string | null;
  start_time: string;
  end_time: string;
  entry?: ScheduleEntryView | null;
}

export interface TimetableDayResponse {
  teacher_id: string;
  academic_year: string;
  day: DayName;
  timetable_status: TimetableStatus;
  periods: DayPeriod[];
}

// Client-side fallback grid, used only when a teacher has no saved
// timetable yet (GET .../timetable correctly returns 404 in that case, so
// there is no server response to render slot/break structure from).
// Actual slot codes/times always come from the backend once any draft
// exists; this is purely scaffolding so a brand-new teacher can start
// building their first draft from an empty grid instead of a blank screen.
export const FALLBACK_PERIODS: DayPeriod[] = [
  { kind: 'SLOT', code: 'S1', start_time: '08:00:00', end_time: '08:55:00', entry: null },
  { kind: 'SLOT', code: 'S2', start_time: '08:55:00', end_time: '09:50:00', entry: null },
  { kind: 'SLOT', code: 'S3', start_time: '09:50:00', end_time: '10:45:00', entry: null },
  { kind: 'BREAK', label: 'Morning break', start_time: '10:45:00', end_time: '11:15:00', entry: null },
  { kind: 'SLOT', code: 'S4', start_time: '11:15:00', end_time: '12:10:00', entry: null },
  { kind: 'SLOT', code: 'S5', start_time: '12:10:00', end_time: '13:05:00', entry: null },
  { kind: 'BREAK', label: 'Lunch', start_time: '13:05:00', end_time: '14:00:00', entry: null },
  { kind: 'SLOT', code: 'S6', start_time: '14:00:00', end_time: '14:55:00', entry: null },
  { kind: 'SLOT', code: 'S7', start_time: '14:55:00', end_time: '15:50:00', entry: null },
  { kind: 'SLOT', code: 'S8', start_time: '15:50:00', end_time: '16:45:00', entry: null },
  { kind: 'SLOT', code: 'S9', start_time: '16:45:00', end_time: '17:40:00', entry: null },
];

/** Format a HH:MM:SS backend time string as h:mm AM/PM for display. */
function formatTime(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number);
  const suffix = h < 12 ? 'AM' : 'PM';
  const hour12 = h === 0 ? 12 : h > 12 ? h - 12 : h;
  return `${hour12}:${String(m).padStart(2, '0')} ${suffix}`;
}

/** Formats a start–end time pair (HH:MM:SS) as "h:mm AM – h:mm PM" for display.
 * Does NOT change backend storage — times are still sent/received as HH:MM:SS. */
export function formatTimeRange(start: string, end: string): string {
  return `${formatTime(start)} – ${formatTime(end)}`;
}

// ---------------------------------------------------------------------------
// Lab slot validation
// A LAB must occupy exactly two consecutive working slots (no break between).
// Valid consecutive pairs derived from PERIOD_SEQUENCE:
//   S1+S2, S2+S3  (before morning break)
//   S4+S5         (after morning break, before lunch)
//   S6+S7, S7+S8, S8+S9  (after lunch)
// S3+S4 and S5+S6 are NOT valid because a break separates them.
// ---------------------------------------------------------------------------
export const VALID_LAB_PAIRS: ReadonlyArray<readonly [string, string]> = [
  ['S1', 'S2'],
  ['S2', 'S3'],
  ['S4', 'S5'],
  ['S6', 'S7'],
  ['S7', 'S8'],
  ['S8', 'S9'],
] as const;

/** Returns true if the given slot codes form a valid consecutive lab pair. */
export function isValidLabPair(slotIds: string[]): boolean {
  if (slotIds.length !== 2) return false;
  const [a, b] = slotIds;
  return VALID_LAB_PAIRS.some(
    ([x, y]) => (a === x && b === y) || (a === y && b === x),
  );
}

// ---------------------------------------------------------------------------
// Academic year format validation
// Required format: YYYY-YYYY  e.g. 2025-2026
// ---------------------------------------------------------------------------
export const ACADEMIC_YEAR_REGEX = /^\d{4}-\d{4}$/;

export function isValidAcademicYear(value: string): boolean {
  const trimmed = value.trim();
  if (!ACADEMIC_YEAR_REGEX.test(trimmed)) return false;
  const [start, end] = trimmed.split('-').map(Number);
  return end === start + 1;
}

/** Collapses a day's periods into the entry payload shape the write API
 * expects, merging a multi-slot entry (e.g. a lab spanning two periods)
 * back into a single entry with combined slot_ids instead of duplicating
 * it once per slot. */
export function periodsToEntryPayloads(periods: DayPeriod[]): ScheduleEntryPayload[] {
  const byEntryId = new Map<string, ScheduleEntryPayload>();
  for (const period of periods) {
    if (period.kind !== 'SLOT' || !period.entry || !period.code) continue;
    const existing = byEntryId.get(period.entry.id);
    if (existing) {
      existing.slot_ids.push(period.code);
    } else {
      byEntryId.set(period.entry.id, {
        slot_ids: [period.code],
        entry_type: period.entry.entry_type,
        subject_or_activity: period.entry.subject_or_activity,
        section: period.entry.section,
        room: period.entry.room,
        notes: period.entry.notes,
      });
    }
  }
  return Array.from(byEntryId.values());
}

/** Rebuilds a day's period grid (slot/break structure + times, taken from
 * an already-fetched template) with a fresh set of entries laid on top. */
export function applyEntriesToPeriods(templatePeriods: DayPeriod[], entries: ScheduleEntryView[]): DayPeriod[] {
  const entryByCode = new Map<string, ScheduleEntryView>();
  for (const entry of entries) {
    for (const code of entry.slot_codes) {
      entryByCode.set(code, entry);
    }
  }
  return templatePeriods.map((period) => {
    if (period.kind !== 'SLOT' || !period.code) return { ...period, entry: null };
    return { ...period, entry: entryByCode.get(period.code) ?? null };
  });
}

/** Same idea as applyEntriesToPeriods, but for not-yet-saved draft entries
 * (ScheduleEntryPayload has no id yet). Synthesizes a display-only id
 * purely so the shared DaySchedulePeriods component has something to
 * key/group on while editing. */
export function draftEntriesToPeriods(templatePeriods: DayPeriod[], entries: ScheduleEntryPayload[]): DayPeriod[] {
  const entryByCode = new Map<string, ScheduleEntryView & { __draftIndex: number }>();
  entries.forEach((entry, draftIndex) => {
    const view: ScheduleEntryView & { __draftIndex: number } = {
      id: `draft-${draftIndex}`,
      entry_type: entry.entry_type,
      subject_or_activity: entry.subject_or_activity,
      section: entry.section,
      room: entry.room,
      notes: entry.notes,
      slot_codes: entry.slot_ids,
      __draftIndex: draftIndex,
    };
    for (const code of entry.slot_ids) {
      entryByCode.set(code, view);
    }
  });
  return templatePeriods.map((period) => {
    if (period.kind !== 'SLOT' || !period.code) return { ...period, entry: null };
    return { ...period, entry: entryByCode.get(period.code) ?? null };
  });
}

// ---------------------------------------------------------------------------
// Excel Import API types  (POST /imports/excel, GET /imports/:id, POST /imports/:id/confirm)
// ---------------------------------------------------------------------------

/** A single teacher row as returned in the import preview. */
export interface TeacherImportRow {
  row_ref: string;            // e.g. "Teachers!2"
  /** CREATE = new teacher; REUSE = existing matched; CONFLICT = acronym clash */
  action: 'CREATE' | 'REUSE' | 'CONFLICT';
  name: string;
  acronym: string;
  level: string;
  program_name: string;
  semester: number;
  department: string;
  resolved_teacher_id: string | null;
  warnings: string[];
}

/** A single schedule entry row as returned in the import preview. */
export interface ScheduleImportRow {
  row_refs: string[];         // e.g. ["Schedule!3"] — list because LAB spans two source rows
  teacher_acronym: string;
  day: string;
  entry_type: string;
  slot_ids: string[];         // Internal S1–S9 codes resolved from the 'time' column
  subject_or_activity: string | null;
  section: string | null;
  room: string | null;
  notes: string | null;
  warnings: string[];
}

/** Full import preview — returned by POST /imports/excel and GET /imports/:id. */
export interface ImportPreview {
  import_id: string;
  academic_year: string;
  teachers: TeacherImportRow[];
  /** Schedule rows keyed by day name (e.g. 'monday'). */
  days: Record<string, ScheduleImportRow[]>;
  warnings: string[];
  errors: string[];
}

/** Returned by POST /imports/:id/confirm on success. */
export interface ImportConfirmResult {
  import_id: string;
  academic_year: string;
  /** UUIDs of newly created teachers */
  teachers_created: string[];
  /** UUIDs of existing teachers that were reused (no change) */
  teachers_reused: string[];
  /** UUIDs of newly created DRAFT timetables */
  timetables_created: string[];
  /** UUIDs of DRAFT timetables that replaced a prior draft */
  timetables_replaced: string[];
}

/** Returned by POST /teachers/{id}/timetable/confirm on success. */
export interface TimetableConfirmResponse {
  id: string;
  teacher_id: string;
  academic_year: string;
  status: 'CONFIRMED';
  source: 'MANUAL' | 'IMPORT';
  confirmed_at: string;   // ISO-8601 UTC timestamp (last_verified_at)
  entry_count: number;
}
