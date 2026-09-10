import { useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import axios from 'axios';

import { Shell } from '../components/Shell';
import { confirmImport, deleteImport, uploadExcel } from '../services/api';
import type { ImportConfirmResult, ImportPreview, ScheduleImportRow, TeacherImportRow } from '../types';
import { DAY_LABELS, ACADEMIC_YEAR_REGEX, isValidAcademicYear } from '../types';

// ---------------------------------------------------------------------------
// Phase state machine
// ---------------------------------------------------------------------------
type Phase =
  | { kind: 'upload' }
  | { kind: 'previewing'; preview: ImportPreview }
  | { kind: 'confirming'; preview: ImportPreview }
  | { kind: 'success'; result: ImportConfirmResult; academicYear: string };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function hasFatalErrors(preview: ImportPreview): boolean {
  return preview.errors.length > 0;
}

function extractApiError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data;
    if (typeof data === 'object' && data !== null) {
      if ('detail' in data) return String(data.detail);
      if ('message' in data) return String(data.message);
    }
    if (err.response?.status === 422) return 'The workbook has validation errors. Review the preview.';
    if (err.message) return err.message;
  }
  if (err instanceof Error) return err.message;
  return 'An unexpected error occurred.';
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StepIndicator({ phase }: { phase: Phase }) {
  const steps = ['Upload', 'Preview', 'Done'];
  const active = phase.kind === 'upload' ? 0 : phase.kind === 'previewing' || phase.kind === 'confirming' ? 1 : 2;
  return (
    <div className="import-steps" aria-label="Progress">
      {steps.map((label, i) => (
        <div key={label} className={`import-step ${i === active ? 'import-step--active' : i < active ? 'import-step--done' : ''}`}>
          <span className="import-step__num">{i < active ? '✓' : i + 1}</span>
          <span className="import-step__label">{label}</span>
          {i < steps.length - 1 && <span className="import-step__line" aria-hidden />}
        </div>
      ))}
    </div>
  );
}

function ErrorBanner({ messages }: { messages: string[] }) {
  if (messages.length === 0) return null;
  return (
    <div className="import-error-banner" role="alert">
      <p className="import-banner__heading">
        <span className="import-banner__icon">✕</span>
        {messages.length === 1 ? '1 error must be fixed before importing' : `${messages.length} errors must be fixed before importing`}
      </p>
      <ul className="import-message-list">
        {messages.map((e, i) => <li key={i}>{e}</li>)}
      </ul>
    </div>
  );
}

function WarningBanner({ messages }: { messages: string[] }) {
  if (messages.length === 0) return null;
  return (
    <div className="import-warning-banner" role="status">
      <p className="import-banner__heading">
        <span className="import-banner__icon">⚠</span>
        {messages.length === 1 ? '1 advisory note' : `${messages.length} advisory notes`}
      </p>
      <ul className="import-message-list">
        {messages.map((w, i) => <li key={i}>{w}</li>)}
      </ul>
    </div>
  );
}

function TeacherPreviewTable({ teachers }: { teachers: TeacherImportRow[] }) {
  if (teachers.length === 0) return <p className="muted">No teachers found in workbook.</p>;
  return (
    <div className="import-table-wrap">
      <table className="import-table">
        <thead>
          <tr>
            <th>Action</th>
            <th>Name</th>
            <th>Acronym</th>
            <th>Level</th>
            <th>Program</th>
            <th>Department</th>
            <th>Notes</th>
          </tr>
        </thead>
        <tbody>
          {teachers.map((t) => (
            <tr key={t.row_ref} className={t.warnings.length > 0 ? 'import-row--warn' : ''}>
              <td>
                <span className={`import-action-pill import-action-pill--${t.action.toLowerCase()}`}>
                  {t.action}
                </span>
              </td>
              <td>{t.name}</td>
              <td><code>{t.acronym}</code></td>
              <td>{t.level}</td>
              <td>{t.program_name}</td>
              <td>{t.department}</td>
              <td className="import-notes-cell">
                {t.warnings.map((w, i) => (
                  <span key={i} className="import-inline-warn">⚠ {w}</span>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function SchedulePreviewTable({ days }: { days: ImportPreview['days'] }) {
  const orderedDays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
  const presentDays = orderedDays.filter((d) => (days[d]?.length ?? 0) > 0);

  if (presentDays.length === 0) return <p className="muted">No schedule rows found in workbook.</p>;

  const [activeDay, setActiveDay] = useState(presentDays[0]);

  const rows: ScheduleImportRow[] = days[activeDay] ?? [];

  return (
    <div>
      {/* Day tabs */}
      <div className="day-tabs" role="tablist">
        {presentDays.map((d) => (
          <button
            key={d}
            role="tab"
            aria-selected={d === activeDay}
            className={`day-tab ${d === activeDay ? 'day-tab--active' : ''}`}
            onClick={() => setActiveDay(d)}
          >
            {DAY_LABELS[d as keyof typeof DAY_LABELS] ?? capitalize(d)}
          </button>
        ))}
      </div>

      <div className="import-table-wrap">
        <table className="import-table">
          <thead>
            <tr>
              <th>Teacher</th>
              <th>Time</th>
              <th>Type</th>
              <th>Subject / Activity</th>
              <th>Section</th>
              <th>Room</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.row_refs[0]}>
                <td><code>{r.teacher_acronym}</code></td>
                <td className="import-time-cell">
                  {/* Show slot codes since there is no time_display field; backend resolved them from 'time' column */}
                  {r.slot_ids.join(' + ')}
                </td>
                <td>
                  <span className={`entry-type entry-type--${r.entry_type.toLowerCase()}`}>
                    {r.entry_type}
                  </span>
                </td>
                <td>{r.subject_or_activity ?? <span className="muted">—</span>}</td>
                <td>{r.section ?? <span className="muted">—</span>}</td>
                <td>{r.room ?? <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Upload phase
// ---------------------------------------------------------------------------

function UploadPhase({
  onPreview,
}: {
  onPreview: (preview: ImportPreview) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [academicYear, setAcademicYear] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null;
    setError(null);
    if (!f) { setFile(null); return; }
    if (!f.name.endsWith('.xlsx')) {
      setError('Only .xlsx files are accepted. Please select a valid Excel workbook.');
      setFile(null);
      e.target.value = '';
      return;
    }
    setFile(f);
  }

  async function handleUpload() {
    if (!file) return;
    const trimmedYear = academicYear.trim();
    if (!trimmedYear) {
      setError('Academic year is required (e.g. 2026-2027).');
      return;
    }
    if (!isValidAcademicYear(trimmedYear)) {
      setError('Academic year must be in YYYY-YYYY format where the second year is one after the first (e.g. 2026-2027).');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const preview = await uploadExcel(file, trimmedYear);
      onPreview(preview);
    } catch (err) {
      setError(extractApiError(err));
    } finally {
      setLoading(false);
    }
  }

  const yearValid = isValidAcademicYear(academicYear.trim());
  const yearTouched = academicYear.trim().length > 0;

  return (
    <div className="import-upload-zone">
      <div className="import-upload-icon" aria-hidden>📊</div>
      <h2 className="import-upload-heading">Import Timetable Workbook</h2>
      <p className="import-upload-sub">
        Enter the academic year, then select the Excel workbook (.xlsx).<br />
        The system will validate it and show you a full preview before saving anything.
      </p>

      {/* Academic year field */}
      <div className="import-year-field">
        <label htmlFor="import-academic-year" className="import-year-label">
          Academic Year
        </label>
        <input
          id="import-academic-year"
          type="text"
          className={`import-year-input ${yearTouched && !yearValid ? 'import-year-input--error' : ''}`}
          value={academicYear}
          onChange={(e) => { setAcademicYear(e.target.value); setError(null); }}
          placeholder="e.g. 2026-2027"
          pattern="\d{4}-\d{4}"
          disabled={loading}
          aria-describedby="import-year-hint"
        />
        {yearTouched && !yearValid && (
          <span id="import-year-hint" className="import-year-hint import-year-hint--error">
            Format: YYYY-YYYY where second year = first + 1 (e.g. 2026-2027)
          </span>
        )}
        {yearTouched && yearValid && (
          <span id="import-year-hint" className="import-year-hint import-year-hint--ok">✓ Valid</span>
        )}
      </div>

      <div className="import-file-area">
        <input
          id="xlsx-file-input"
          ref={inputRef}
          type="file"
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          onChange={handleFileChange}
          className="import-file-input"
          disabled={loading}
          aria-label="Select Excel workbook"
        />
        {file ? (
          <div className="import-file-chosen">
            <span className="import-file-icon">📄</span>
            <span className="import-file-name">{file.name}</span>
            <span className="import-file-size">({(file.size / 1024).toFixed(1)} KB)</span>
            <button
              className="import-file-clear"
              onClick={() => { setFile(null); if (inputRef.current) inputRef.current.value = ''; }}
              aria-label="Remove selected file"
              disabled={loading}
            >✕</button>
          </div>
        ) : (
          <label htmlFor="xlsx-file-input" className="import-file-label">
            <span className="import-file-label__icon">📁</span>
            Click to choose an .xlsx file
          </label>
        )}
      </div>

      {error && <p className="error-text import-upload-error">{error}</p>}

      <button
        className="import-primary-btn"
        disabled={!file || !yearValid || loading}
        onClick={handleUpload}
        id="upload-btn"
      >
        {loading ? 'Validating…' : 'Validate & Preview'}
      </button>

      <div className="import-format-hint">
        <p className="import-format-hint__title">Expected workbook format (2 sheets, no Metadata)</p>
        <div className="import-format-grid">
          <div>
            <p className="import-format-sheet">👩‍🏫 Teachers</p>
            <code>name · acronym · level · program · department · semester_scope</code>
          </div>
          <div>
            <p className="import-format-sheet">📅 Schedule</p>
            <code>teacher_acronym · day · type · time · subject_or_activity · section · room</code>
          </div>
        </div>
        <p className="import-format-time-eg">
          Time format example: <strong>8:00 AM - 8:55 AM</strong> &nbsp;·&nbsp; <strong>11:15 AM - 1:05 PM</strong>
        </p>
        <p className="import-format-time-eg">
          The academic year is entered above — do <strong>not</strong> include a Metadata sheet.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Preview phase
// ---------------------------------------------------------------------------

function PreviewPhase({
  preview,
  onConfirm,
  onDiscard,
  confirming,
  confirmError,
}: {
  preview: ImportPreview;
  onConfirm: () => void;
  onDiscard: () => void;
  confirming: boolean;
  confirmError: string | null;
}) {
  const fatal = hasFatalErrors(preview);

  // Collect teacher-level warnings into the global list for display
  const teacherWarnings = preview.teachers.flatMap((t) => t.warnings);
  const allWarnings = [...preview.warnings, ...teacherWarnings];

  return (
    <div className="import-preview">
      {/* Summary bar */}
      <div className="import-summary-bar">
        <div className="import-summary-item">
          <span className="import-summary-label">Academic year</span>
          <span className="import-summary-value">{preview.academic_year}</span>
        </div>
        <div className="import-summary-item">
          <span className="import-summary-label">Teachers</span>
          <span className="import-summary-value">{preview.teachers.length}</span>
        </div>
        <div className="import-summary-item">
          <span className="import-summary-label">Schedule rows</span>
          <span className="import-summary-value">
            {Object.values(preview.days).reduce((acc, rows) => acc + (rows?.length ?? 0), 0)}
          </span>
        </div>
        <div className="import-summary-item">
          <span className="import-summary-label">Status</span>
          <span className={`status-pill ${fatal ? 'bad' : 'ok'}`}>
            {fatal ? `${preview.errors.length} error${preview.errors.length > 1 ? 's' : ''}` : 'Ready to import'}
          </span>
        </div>
      </div>

      {/* Errors */}
      <ErrorBanner messages={preview.errors} />

      {/* Warnings */}
      <WarningBanner messages={allWarnings} />

      {/* Teacher table */}
      <section className="import-section">
        <h3 className="import-section-title">Teachers ({preview.teachers.length})</h3>
        <TeacherPreviewTable teachers={preview.teachers} />
      </section>

      {/* Schedule table */}
      <section className="import-section">
        <h3 className="import-section-title">Schedule</h3>
        <SchedulePreviewTable days={preview.days} />
      </section>

      {/* Confirm error */}
      {confirmError && (
        <div className="import-error-banner" role="alert">
          <p className="import-banner__heading"><span className="import-banner__icon">✕</span> Could not save</p>
          <p>{confirmError}</p>
        </div>
      )}

      {/* Action row */}
      <div className="import-action-row">
        <button
          className="import-secondary-btn"
          onClick={onDiscard}
          disabled={confirming}
          id="discard-btn"
        >
          ✕ Discard &amp; re-upload
        </button>

        {fatal ? (
          <div className="import-blocked-msg">
            Fix the errors above in your Excel file and re-upload to continue.
          </div>
        ) : (
          <button
            className="import-primary-btn"
            onClick={onConfirm}
            disabled={confirming}
            id="confirm-btn"
          >
            {confirming ? 'Saving…' : '✓ Confirm & Save'}
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Success phase
// ---------------------------------------------------------------------------

function SuccessPhase({
  result,
}: {
  result: ImportConfirmResult;
}) {
  const navigate = useNavigate();
  return (
    <div className="import-success">
      <div className="import-success__icon" aria-hidden>✅</div>
      <h2 className="import-success__heading">Timetables Saved</h2>
      <p className="import-success__sub">
        The import completed successfully. Draft timetables are now stored and ready for review.
      </p>

      <div className="import-success-stats">
        <div className="import-stat-card">
          <span className="import-stat-num">
            {result.teachers_created.length + result.teachers_reused.length}
          </span>
          <span className="import-stat-label">Teachers</span>
          {result.teachers_created.length > 0 && (
            <span className="import-stat-detail">{result.teachers_created.length} new</span>
          )}
          {result.teachers_reused.length > 0 && (
            <span className="import-stat-detail">{result.teachers_reused.length} reused</span>
          )}
        </div>
        <div className="import-stat-card">
          <span className="import-stat-num">
            {result.timetables_created.length + result.timetables_replaced.length}
          </span>
          <span className="import-stat-label">Draft timetables</span>
          {result.timetables_created.length > 0 && (
            <span className="import-stat-detail">{result.timetables_created.length} new</span>
          )}
          {result.timetables_replaced.length > 0 && (
            <span className="import-stat-detail">{result.timetables_replaced.length} replaced</span>
          )}
        </div>
        <div className="import-stat-card">
          <span className="import-stat-num">{result.academic_year}</span>
          <span className="import-stat-label">Academic year</span>
        </div>
      </div>

      <div className="import-success-note">
        <strong>Note:</strong> Timetables are saved as <em>Draft</em>. The Director can search for
        individual teachers to review their schedule.
      </div>

      <div className="import-success-actions">
        <button className="import-primary-btn" onClick={() => navigate('/import')}>
          Import another workbook
        </button>
        <Link className="import-secondary-btn" to="/director">
          Go to Director view →
        </Link>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function ExcelImportPage() {
  const [phase, setPhase] = useState<Phase>({ kind: 'upload' });
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  async function handleConfirm(preview: ImportPreview) {
    setConfirming(true);
    setConfirmError(null);
    try {
      const result = await confirmImport(preview.import_id);
      setPhase({ kind: 'success', result, academicYear: preview.academic_year });
    } catch (err) {
      setConfirmError(extractApiError(err));
    } finally {
      setConfirming(false);
    }
  }

  async function handleDiscard(preview: ImportPreview) {
    // Best-effort delete; don't block UI if it fails
    try { await deleteImport(preview.import_id); } catch { /* ignore */ }
    setPhase({ kind: 'upload' });
    setConfirmError(null);
  }

  return (
    <Shell>
      <section className="page-card">
        {/* Header */}
        <div className="page-heading">
          <div>
            <span className="eyebrow">Excel Import</span>
            <h1 className="import-page-title">Import Timetable</h1>
          </div>
          <Link to="/" className="back-link">← Home</Link>
        </div>

        {/* Workflow steps */}
        <StepIndicator phase={phase} />

        {/* Phase content */}
        {phase.kind === 'upload' && (
          <UploadPhase
            onPreview={(p) => setPhase({ kind: 'previewing', preview: p })}
          />
        )}

        {(phase.kind === 'previewing' || phase.kind === 'confirming') && (
          <PreviewPhase
            preview={phase.preview}
            confirming={confirming}
            confirmError={confirmError}
            onConfirm={() => handleConfirm(phase.preview)}
            onDiscard={() => handleDiscard(phase.preview)}
          />
        )}

        {phase.kind === 'success' && (
          <SuccessPhase result={phase.result} />
        )}
      </section>
    </Shell>
  );
}
