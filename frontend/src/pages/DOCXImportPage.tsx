import { useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import axios from 'axios';

import { Shell } from '../components/Shell';
import { confirmImport, finalizeDOCXBlocks, resolveDOCXBlocks, uploadDOCX } from '../services/api';
import type {
  DOCXImportPreview,
  DOCXManualResolutionInput,
  DOCXResolvedActivity,
  DOCXUnresolvedBlock,
  ImportConfirmResult,
} from '../types';
import { DAY_LABELS } from '../types';

// ---------------------------------------------------------------------------
// Phase state machine
// ---------------------------------------------------------------------------
type Phase =
  | { kind: 'upload' }
  | { kind: 'previewing'; preview: DOCXImportPreview }
  | { kind: 'resolving'; preview: DOCXImportPreview; resolvingBlockId: string }
  | { kind: 'confirming'; preview: DOCXImportPreview }
  | { kind: 'success'; result: ImportConfirmResult; academicYear: string };

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function canConfirm(preview: DOCXImportPreview): boolean {
  // Confirmation is blocked ONLY when teacher or resource occupancy is genuinely ambiguous.
  // Activity semantic ambiguity alone does NOT block confirmation.
  return preview.unresolved_blocks.filter(b => b.resolution_required).length === 0;
}

function extractApiError(err: unknown): string {
  if (axios.isAxiosError(err)) {
    const data = err.response?.data;
    if (typeof data === 'object' && data !== null) {
      if ('detail' in data) {
        const detail = data.detail;
        if (typeof detail === 'string') return detail;
        if (typeof detail === 'object' && detail !== null && 'message' in detail) {
          return String(detail.message);
        }
        return JSON.stringify(detail);
      }
      if ('message' in data) return String(data.message);
    }
    if (err.response?.status === 422) return 'Validation errors prevent confirmation.';
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
  const steps = ['Upload', 'Review & Resolve', 'Done'];
  const active =
    phase.kind === 'upload'
      ? 0
      : phase.kind === 'previewing' || phase.kind === 'resolving' || phase.kind === 'confirming'
        ? 1
        : 2;
  return (
    <div className="import-steps" aria-label="Progress">
      {steps.map((label, i) => (
        <div
          key={label}
          className={`import-step ${i === active ? 'import-step--active' : i < active ? 'import-step--done' : ''}`}
        >
          <span className="import-step__num">{i < active ? '✓' : i + 1}</span>
          <span className="import-step__label">{label}</span>
          {i < steps.length - 1 && <span className="import-step__line" aria-hidden />}
        </div>
      ))}
    </div>
  );
}

function ErrorBanner({ errors }: { errors: DOCXImportPreview['errors'] }) {
  const [expanded, setExpanded] = useState(false);

  if (errors.length === 0) return null;

  // Group errors by code
  const grouped = errors.reduce((acc, err) => {
    if (!acc[err.code]) acc[err.code] = [];
    acc[err.code].push(err);
    return acc;
  }, {} as Record<string, typeof errors>);

  return (
    <div className="import-info-banner" role="status">
      <div className="import-banner__heading" style={{ cursor: 'pointer' }} onClick={() => setExpanded(!expanded)}>
        <span className="import-banner__icon">ℹ️</span>
        <span>
          Issues requiring attention ({errors.length} total)
          <button
            style={{ marginLeft: '1rem', fontSize: '0.9em', padding: '2px 8px' }}
            onClick={(e) => {
              e.stopPropagation();
              setExpanded(!expanded);
            }}
          >
            {expanded ? 'Collapse' : 'View Details'}
          </button>
        </span>
      </div>

      {!expanded && (
        <div style={{ marginTop: '0.5rem' }}>
          <p style={{ marginBottom: '0.5rem' }}>Grouped by issue type:</p>
          <ul className="import-message-list" style={{ fontSize: '0.95em' }}>
            {Object.entries(grouped).map(([code, items]) => (
              <li key={code}>
                <strong>{code.replace(/_/g, ' ')}:</strong> {items.length} block{items.length > 1 ? 's' : ''}
              </li>
            ))}
          </ul>
        </div>
      )}

      {expanded && (
        <div style={{ marginTop: '1rem' }}>
          {Object.entries(grouped).map(([code, items]) => (
            <div key={code} style={{ marginBottom: '1rem' }}>
              <strong>{code.replace(/_/g, ' ')} ({items.length}):</strong>
              <ul className="import-message-list" style={{ marginTop: '0.5rem', fontSize: '0.9em' }}>
                {items.map((e, i) => (
                  <li key={i}>
                    {e.message}
                    {e.source_location && <span className="muted"> — {e.source_location}</span>}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function WarningBanner({ warnings }: { warnings: DOCXImportPreview['warnings'] }) {
  const [expanded, setExpanded] = useState(false);

  if (warnings.length === 0) return null;

  return (
    <div className="import-warning-banner" role="status">
      <div className="import-banner__heading" style={{ cursor: 'pointer' }} onClick={() => setExpanded(!expanded)}>
        <span className="import-banner__icon">⚠</span>
        <span>
          {warnings.length === 1 ? '1 advisory note' : `${warnings.length} advisory notes`}
          <button
            style={{ marginLeft: '1rem', fontSize: '0.9em', padding: '2px 8px' }}
            onClick={(e) => {
              e.stopPropagation();
              setExpanded(!expanded);
            }}
          >
            {expanded ? 'Collapse' : 'View Details'}
          </button>
        </span>
      </div>

      {expanded && (
        <ul className="import-message-list" style={{ marginTop: '0.5rem' }}>
          {warnings.map((w, i) => (
            <li key={i}>
              <strong>[{w.code}]</strong> {w.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function FacultyLegendTable({ legend }: { legend: DOCXImportPreview['faculty_legend'] }) {
  const [expanded, setExpanded] = useState(false);

  if (legend.length === 0) return <p className="muted">No faculty legend found.</p>;

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          background: 'none',
          border: 'none',
          color: '#4a90e2',
          cursor: 'pointer',
          fontSize: '0.95em',
          marginBottom: '0.5rem',
          padding: 0,
        }}
      >
        {expanded ? '▼ Hide' : '▶ Show'} Faculty Legend ({legend.length} entries)
      </button>

      {expanded && (
        <div className="import-table-wrap">
          <table className="import-table">
            <thead>
              <tr>
                <th>Acronym</th>
                <th>Full Name</th>
              </tr>
            </thead>
            <tbody>
              {legend.map((f) => (
                <tr key={f.acronym}>
                  <td><code>{f.acronym}</code></td>
                  <td>{f.full_name}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ResolvedActivitiesTable({ activities }: { activities: DOCXResolvedActivity[] }) {
  if (activities.length === 0) return <p className="muted">No resolved activities yet.</p>;

  // Group by day
  const byDay: Record<string, DOCXResolvedActivity[]> = {};
  for (const act of activities) {
    if (!byDay[act.day]) byDay[act.day] = [];
    byDay[act.day].push(act);
  }

  const orderedDays = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'];
  const presentDays = orderedDays.filter((d) => (byDay[d]?.length ?? 0) > 0);

  const [activeDay, setActiveDay] = useState(presentDays[0] || 'monday');

  const rows = byDay[activeDay] || [];

  return (
    <div>
      {presentDays.length > 0 && (
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
      )}

      <div className="import-table-wrap">
        <table className="import-table">
          <thead>
            <tr>
              <th>Section</th>
              <th>Slots</th>
              <th>Subject/Activity</th>
              <th>Teacher</th>
              <th>Resource</th>
              <th>Source</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className={r.is_manually_resolved ? 'import-row--manual' : ''}>
                <td>{r.section}</td>
                <td>{r.slots.join(', ')}</td>
                <td>{r.subject_or_activity}</td>
                <td><code>{r.teacher_acronym}</code></td>
                <td>{r.resource_code || <span className="muted">—</span>}</td>
                <td className="muted" style={{ fontSize: '0.85em' }}>{r.source_location}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// Status badge helpers
function OccupancyBadge({ status, kind }: { status: string; kind: 'teacher' | 'resource' | 'activity' }) {
  let icon = '';
  let className = '';
  let label = status;

  if (kind === 'activity') {
    if (status === 'RESOLVED') { icon = '✓'; className = 'badge-ok'; label = 'Resolved'; }
    else if (status === 'AMBIGUOUS') { icon = '⚠'; className = 'badge-warn'; label = 'Ambiguous (informational)'; }
    else { icon = '—'; className = 'badge-muted'; label = 'Not specified'; }
  } else {
    if (status === 'DETERMINISTIC') { icon = '✓'; className = 'badge-ok'; label = 'Determined'; }
    else if (status === 'AMBIGUOUS') { icon = '⚠'; className = 'badge-error'; label = 'Ambiguous'; }
    else { icon = '—'; className = 'badge-muted'; label = 'None'; }
  }

  return (
    <span className={`occ-badge ${className}`} title={status}>
      {icon} {label}
    </span>
  );
}

function UnresolvedBlocksTable({
  blocks,
  onResolve,
  onFinalize,
}: {
  blocks: DOCXUnresolvedBlock[];
  onResolve: (block: DOCXUnresolvedBlock) => void;
  onFinalize: (blockIds: string[]) => void;
}) {
  const [selectedBlocks, setSelectedBlocks] = useState<Set<string>>(new Set());

  if (blocks.length === 0) {
    return <p className="muted">All blocks have been resolved! You may now confirm.</p>;
  }

  function toggleSelect(blockId: string) {
    const next = new Set(selectedBlocks);
    if (next.has(blockId)) {
      next.delete(blockId);
    } else {
      next.add(blockId);
    }
    setSelectedBlocks(next);
  }

  function handleFinalizeSelected() {
    if (selectedBlocks.size === 0) return;
    const confirmed = window.confirm(
      `Finalize ${selectedBlocks.size} block(s)?\n\n` +
      `These blocks will be marked as excluded and removed from the import. ` +
      `This action will allow confirmation to proceed but the blocks will not be imported.`
    );
    if (confirmed) {
      onFinalize(Array.from(selectedBlocks));
      setSelectedBlocks(new Set());
    }
  }

  const needingAction = blocks.filter(b => b.resolution_required);
  const informationalOnly = blocks.filter(b => !b.resolution_required);

  return (
    <div>
      {needingAction.length === 0 && informationalOnly.length > 0 && (
        <div className="import-info-banner" role="status" style={{ marginBottom: '1rem' }}>
          <span className="import-banner__icon">ℹ️</span>
          <span>
            <strong>{informationalOnly.length} block{informationalOnly.length > 1 ? 's' : ''} have ambiguous activity text</strong> but teacher and resource occupancy is already determined.
            These do <em>not</em> block confirmation — they are shown for information only.
          </span>
        </div>
      )}

      <div className="import-table-wrap">
        <table className="import-table">
          <thead>
            <tr>
              <th style={{ width: '40px' }}>
                <input
                  type="checkbox"
                  checked={selectedBlocks.size === blocks.length}
                  onChange={(e) => {
                    if (e.target.checked) {
                      setSelectedBlocks(new Set(blocks.map(b => b.block_id)));
                    } else {
                      setSelectedBlocks(new Set());
                    }
                  }}
                  aria-label="Select all"
                />
              </th>
              <th>Day / Section / Slots</th>
              <th>Activity<br/><span style={{fontSize:'0.8em',fontWeight:'normal',color:'var(--color-muted)'}}>informational</span></th>
              <th>Teacher Occupancy</th>
              <th>Resource Occupancy</th>
              <th>Blocks Confirm?</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {blocks.map((block) => (
              <tr
                key={block.block_id}
                className={block.resolution_required ? 'import-row--warn' : 'import-row--info'}
              >
                <td>
                  <input
                    type="checkbox"
                    checked={selectedBlocks.has(block.block_id)}
                    onChange={() => toggleSelect(block.block_id)}
                    aria-label={`Select block ${block.block_id}`}
                  />
                </td>
                <td>
                  <strong>{capitalize(block.day)}</strong><br/>
                  {block.section}<br/>
                  <code style={{fontSize:'0.85em'}}>{block.slots.join('+')}</code>
                </td>
                <td>
                  <OccupancyBadge status={block.activity_semantic_status} kind="activity" />
                  {block.activity_candidates.length > 0 && (
                    <div style={{ marginTop: '0.25rem', fontSize: '0.85em', color: 'var(--color-muted)' }}>
                      {block.activity_candidates.map((a, i) => (
                        <div key={i}><code>{a.code}</code></div>
                      ))}
                    </div>
                  )}
                </td>
                <td>
                  <OccupancyBadge status={block.teacher_occupancy_status} kind="teacher" />
                  {block.is_student_managed ? (
                    <div style={{ marginTop: '0.25rem', fontSize: '0.85em' }}>
                      <span
                        title="Teacher absence is intentional for this activity"
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '0.25rem',
                          color: 'var(--color-muted)',
                          fontStyle: 'italic'
                        }}
                      >
                        🎓 None (student-managed activity)
                      </span>
                    </div>
                  ) : block.teacher_candidates.length > 0 ? (
                    <ul className="candidate-list" style={{ marginTop: '0.25rem' }}>
                      {block.teacher_candidates.map((t, i) => (
                        <li key={i}>
                          <code>{t.acronym}</code>
                          {t.name && <span className="muted"> ({t.name})</span>}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </td>
                <td>
                  <OccupancyBadge status={block.resource_occupancy_status} kind="resource" />
                  {block.resource_candidates.length > 0 && (
                    <ul className="candidate-list" style={{ marginTop: '0.25rem' }}>
                      {block.resource_candidates.map((r, i) => (
                        <li key={i}><code>{r.code}</code></li>
                      ))}
                    </ul>
                  )}
                </td>
                <td style={{ textAlign: 'center' }}>
                  {block.resolution_required
                    ? <span className="badge-error" title="Blocks confirmation">✗ Blocked</span>
                    : <span className="badge-ok" title="Does not block confirmation">✓ OK</span>}
                </td>
                <td>
                  {block.resolution_required && (
                    <button
                      className="import-secondary-btn"
                      style={{ fontSize: '0.9em', padding: '4px 8px' }}
                      onClick={() => onResolve(block)}
                    >
                      Resolve
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selectedBlocks.size > 0 && (
        <div style={{ marginTop: '1rem', display: 'flex', gap: '1rem', alignItems: 'center' }}>
          <span className="muted">{selectedBlocks.size} block(s) selected</span>
          <button
            className="import-secondary-btn"
            onClick={handleFinalizeSelected}
          >
            Finalize/Exclude Selected
          </button>
          <span className="muted" style={{ fontSize: '0.9em' }}>
            (Marks as excluded — will not be imported)
          </span>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Upload phase
// ---------------------------------------------------------------------------

function UploadPhase({
  onPreview,
}: {
  onPreview: (preview: DOCXImportPreview) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [academicYear, setAcademicYear] = useState('');
  const [department, setDepartment] = useState('Computer Applications');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null;
    setError(null);
    if (!f) {
      setFile(null);
      return;
    }
    if (!f.name.endsWith('.docx')) {
      setError('Only .docx files are accepted. Please select a valid DOCX file.');
      setFile(null);
      e.target.value = '';
      return;
    }
    setFile(f);
  }

  async function handleUpload() {
    if (!file) return;
    const trimmedYear = academicYear.trim();
    const trimmedDept = department.trim();

    if (!trimmedYear) {
      setError('Academic year is required (e.g. 2026-2027).');
      return;
    }
    if (!/^\d{4}-\d{4}$/.test(trimmedYear)) {
      setError('Academic year must be in YYYY-YYYY format (e.g. 2026-2027).');
      return;
    }
    if (!trimmedDept) {
      setError('Department is required (e.g. Computer Applications).');
      return;
    }

    setLoading(true);
    setError(null);
    try {
      const preview = await uploadDOCX(file, trimmedYear, trimmedDept);
      onPreview(preview);
    } catch (err) {
      setError(extractApiError(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="import-upload-zone">
      <div className="import-upload-icon" aria-hidden>📄</div>
      <h2 className="import-upload-heading">Import DOCX Timetable</h2>
      <p className="import-upload-sub">
        Enter the academic year and department, then select the DOCX timetable file.
        <br />
        The system will parse it and show you a full preview before saving anything.
      </p>

      {/* Academic year field */}
      <div className="import-year-field">
        <label htmlFor="import-academic-year" className="import-year-label">
          Academic Year
        </label>
        <input
          id="import-academic-year"
          type="text"
          className="import-year-input"
          value={academicYear}
          onChange={(e) => {
            setAcademicYear(e.target.value);
            setError(null);
          }}
          placeholder="e.g. 2026-2027"
          disabled={loading}
          aria-describedby="import-year-hint"
        />
        <span id="import-year-hint" className="import-year-hint">
          Format: YYYY-YYYY (e.g. 2026-2027)
        </span>
      </div>

      {/* Department field */}
      <div className="import-year-field">
        <label htmlFor="import-department" className="import-year-label">
          Department
        </label>
        <input
          id="import-department"
          type="text"
          className="import-year-input"
          value={department}
          onChange={(e) => {
            setDepartment(e.target.value);
            setError(null);
          }}
          placeholder="e.g. Computer Applications"
          disabled={loading}
        />
      </div>

      <div className="import-file-area">
        <input
          id="docx-file-input"
          ref={inputRef}
          type="file"
          accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
          onChange={handleFileChange}
          className="import-file-input"
          disabled={loading}
          aria-label="Select DOCX file"
        />
        {file ? (
          <div className="import-file-chosen">
            <span className="import-file-icon">📄</span>
            <span className="import-file-name">{file.name}</span>
            <span className="import-file-size">({(file.size / 1024).toFixed(1)} KB)</span>
            <button
              className="import-file-clear"
              onClick={() => {
                setFile(null);
                if (inputRef.current) inputRef.current.value = '';
              }}
              aria-label="Remove selected file"
              disabled={loading}
            >
              ✕
            </button>
          </div>
        ) : (
          <label htmlFor="docx-file-input" className="import-file-label">
            <span className="import-file-label__icon">📁</span>
            Click to choose a .docx file
          </label>
        )}
      </div>

      {error && <p className="error-text import-upload-error">{error}</p>}

      <button
        className="import-primary-btn"
        disabled={!file || !academicYear.trim() || !department.trim() || loading}
        onClick={handleUpload}
        id="upload-btn"
      >
        {loading ? 'Parsing…' : 'Parse & Preview'}
      </button>

      <div className="import-format-hint">
        <p className="import-format-hint__title">Expected DOCX format</p>
        <ul>
          <li>Faculty legend table (Table 1)</li>
          <li>Main timetable table with days, sections, and slots</li>
          <li>Activities may include teacher acronyms and resources in parentheses</li>
        </ul>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Preview phase
// ---------------------------------------------------------------------------

function PreviewPhase({
  preview,
  onRefresh,
  onConfirm,
  onDiscard,
  confirming,
  confirmError,
}: {
  preview: DOCXImportPreview;
  onRefresh: (updated: DOCXImportPreview) => void;
  onConfirm: () => void;
  onDiscard: () => void;
  confirming: boolean;
  confirmError: string | null;
}) {
  const [resolvingBlock, setResolvingBlock] = useState<DOCXUnresolvedBlock | null>(null);
  const [resolutionInputs, setResolutionInputs] = useState<Partial<DOCXManualResolutionInput>>({});
  const [resolving, setResolving] = useState(false);
  const [finalizing, setFinalizing] = useState(false);

  const confirmable = canConfirm(preview);

  // Status message based on parser status
  const getStatusMessage = () => {
    if (preview.parser_status === 'COMPLETE') {
      return 'The timetable was parsed successfully. All blocks are resolved.';
    }
    if (preview.parser_status === 'PARTIAL') {
      return 'The timetable was parsed successfully. Some blocks require review before confirmation.';
    }
    return 'The timetable parsing encountered issues. Please review.';
  };

  const getStatusClass = () => {
    if (preview.parser_status === 'COMPLETE') return 'ok';
    if (preview.parser_status === 'PARTIAL') return 'warning';
    return 'bad';
  };

  async function handleResolve() {
    // For student-managed blocks (Placement, VAC, etc.), teacher is optional.
    // For faculty-managed blocks, teacher is required.
    const teacherRequired = resolvingBlock && !resolvingBlock.is_student_managed;
    if (!resolvingBlock || !resolutionInputs.selected_activity) {
      return;
    }
    if (teacherRequired && !resolutionInputs.selected_teacher) {
      return;
    }

    const resolution: DOCXManualResolutionInput = {
      block_id: resolvingBlock.block_id,
      selected_activity: resolutionInputs.selected_activity,
      selected_teacher: resolutionInputs.selected_teacher ?? null,
      selected_resource: resolutionInputs.selected_resource || null,
      entry_type: resolutionInputs.entry_type || 'CLASS',
    };

    setResolving(true);
    try {
      const result = await resolveDOCXBlocks(preview.import_id, [resolution]);
      // Update preview with new resolved activities
      const updatedPreview = {
        ...preview,
        resolved_activities: [...preview.resolved_activities, ...result.new_resolved_activities],
        resolved_count: preview.resolved_count + result.applied_count,
        manually_resolved_count: preview.manually_resolved_count + result.applied_count,
        unresolved_count: result.remaining_unresolved,
        unresolved_blocks: preview.unresolved_blocks.filter(b => b.block_id !== resolvingBlock.block_id)
      };
      onRefresh(updatedPreview);
      setResolvingBlock(null);
      setResolutionInputs({});
    } catch (err) {
      alert(extractApiError(err));
    } finally {
      setResolving(false);
    }
  }

  async function handleFinalize(blockIds: string[]) {
    setFinalizing(true);
    try {
      const updated = await finalizeDOCXBlocks(preview.import_id, blockIds);
      onRefresh(updated);
    } catch (err) {
      alert(extractApiError(err));
    } finally {
      setFinalizing(false);
    }
  }

  return (
    <div className="import-preview">
      {/* Status message */}
      <div className={`import-status-message import-status-message--${getStatusClass()}`}>
        <strong>Import Status: {preview.parser_status}</strong>
        <p style={{ margin: '0.5rem 0 0 0', fontSize: '0.95em' }}>{getStatusMessage()}</p>
      </div>

      {/* Summary bar */}
      <div className="import-summary-bar">
        <div className="import-summary-item">
          <span className="import-summary-label">Filename</span>
          <span className="import-summary-value">{preview.filename}</span>
        </div>
        <div className="import-summary-item">
          <span className="import-summary-label">Total blocks</span>
          <span className="import-summary-value">{preview.total_blocks}</span>
        </div>
        <div className="import-summary-item">
          <span className="import-summary-label">Resolved</span>
          <span className="import-summary-value status-pill ok">{preview.resolved_count}</span>
        </div>
        <div className="import-summary-item">
          <span className="import-summary-label">Need Review</span>
          <span className="import-summary-value status-pill warning">{preview.unresolved_count}</span>
        </div>
      </div>

      {/* Errors */}
      <ErrorBanner errors={preview.errors} />

      {/* Warnings */}
      <WarningBanner warnings={preview.warnings} />

      {/* Faculty legend */}
      <section className="import-section">
        <h3 className="import-section-title">Faculty Legend ({preview.faculty_legend.length})</h3>
        <FacultyLegendTable legend={preview.faculty_legend} />
      </section>

      {/* Resolved activities */}
      <section className="import-section">
        <h3 className="import-section-title">Resolved Activities ({preview.resolved_count})</h3>
        <ResolvedActivitiesTable activities={preview.resolved_activities} />
      </section>

      {/* Unresolved blocks */}
      <section className="import-section">
        <h3 className="import-section-title">
          Blocks Requiring Review ({preview.unresolved_count})
        </h3>
        {preview.unresolved_count > 0 && (
          <p className="muted" style={{ marginBottom: '1rem' }}>
            These blocks contain ambiguous information. Resolve them manually or finalize as excluded.
          </p>
        )}
        <UnresolvedBlocksTable
          blocks={preview.unresolved_blocks}
          onResolve={(block) => setResolvingBlock(block)}
          onFinalize={handleFinalize}
        />
      </section>

      {/* Resolution modal */}
      {resolvingBlock && (
        <div className="modal-overlay" onClick={() => setResolvingBlock(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <h3>Resolve Block: {resolvingBlock.section} - {resolvingBlock.slots.join(', ')}</h3>

            <div style={{ marginTop: '1rem' }}>
              <label>
                <strong>Activity:</strong>
                <select
                  value={resolutionInputs.selected_activity || ''}
                  onChange={(e) => setResolutionInputs({ ...resolutionInputs, selected_activity: e.target.value })}
                  style={{ display: 'block', width: '100%', marginTop: '0.5rem', padding: '0.5rem' }}
                >
                  <option value="">-- Select activity --</option>
                  {resolvingBlock.activity_candidates.map((a, i) => (
                    <option key={i} value={a.code}>
                      {a.code}
                    </option>
                  ))}
                </select>
              </label>

              <label style={{ marginTop: '1rem', display: 'block' }}>
                <strong>Teacher:{resolvingBlock.is_student_managed ? '' : ' *'}</strong>
                {resolvingBlock.is_student_managed ? (
                  <div
                    style={{
                      marginTop: '0.5rem',
                      padding: '0.75rem',
                      background: 'var(--color-bg-subtle, #f0f9ff)',
                      border: '1px solid var(--color-border-info, #bae6fd)',
                      borderRadius: '6px',
                      fontSize: '0.9em',
                      color: 'var(--color-info, #0369a1)',
                    }}
                  >
                    🎓 <strong>Student-managed activity</strong> — no faculty teacher assigned by design.
                    <br />
                    <span style={{ color: 'var(--color-muted)', marginTop: '0.25rem', display: 'block' }}>
                      Teacher assignment is not required for this block.
                    </span>
                  </div>
                ) : (
                  <select
                    value={resolutionInputs.selected_teacher || ''}
                    onChange={(e) => setResolutionInputs({ ...resolutionInputs, selected_teacher: e.target.value })}
                    style={{ display: 'block', width: '100%', marginTop: '0.5rem', padding: '0.5rem' }}
                  >
                    <option value="">-- Select teacher --</option>
                    {resolvingBlock.teacher_candidates.map((t, i) => (
                      <option key={i} value={t.acronym}>
                        {t.acronym} {t.name && `(${t.name})`}
                      </option>
                    ))}
                  </select>
                )}
              </label>

              {resolvingBlock.resource_candidates.length > 0 && (
                <label style={{ marginTop: '1rem', display: 'block' }}>
                  <strong>Resource {resolvingBlock.is_student_managed ? '(required — resolves ambiguity)' : '(optional)'}:</strong>
                  <select
                    value={resolutionInputs.selected_resource || ''}
                    onChange={(e) => setResolutionInputs({ ...resolutionInputs, selected_resource: e.target.value })}
                    style={{ display: 'block', width: '100%', marginTop: '0.5rem', padding: '0.5rem' }}
                  >
                    <option value="">-- Select resource --</option>
                    {resolvingBlock.resource_candidates.map((r, i) => (
                      <option key={i} value={r.code}>
                        {r.code}
                      </option>
                    ))}
                  </select>
                </label>
              )}

              <label style={{ marginTop: '1rem', display: 'block' }}>
                <strong>Entry Type:</strong>
                <select
                  value={resolutionInputs.entry_type || 'CLASS'}
                  onChange={(e) => setResolutionInputs({ ...resolutionInputs, entry_type: e.target.value })}
                  style={{ display: 'block', width: '100%', marginTop: '0.5rem', padding: '0.5rem' }}
                >
                  <option value="CLASS">CLASS</option>
                  <option value="LAB">LAB</option>
                  <option value="OTHER">OTHER</option>
                </select>
              </label>
            </div>

            <div style={{ marginTop: '1.5rem', display: 'flex', gap: '1rem', justifyContent: 'flex-end' }}>
              <button className="import-secondary-btn" onClick={() => setResolvingBlock(null)} disabled={resolving}>
                Cancel
              </button>
              <button
                className="import-primary-btn"
                onClick={handleResolve}
                disabled={
                  !resolutionInputs.selected_activity ||
                  (!resolvingBlock.is_student_managed && !resolutionInputs.selected_teacher) ||
                  resolving
                }
              >
                {resolving ? 'Resolving…' : 'Apply Resolution'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Confirm error */}
      {confirmError && (
        <div className="import-error-banner" role="alert">
          <p className="import-banner__heading">
            <span className="import-banner__icon">✕</span> Could not confirm
          </p>
          <p>{confirmError}</p>
        </div>
      )}

      {/* Action row */}
      <div className="import-action-row">
        <button className="import-secondary-btn" onClick={onDiscard} disabled={confirming || finalizing}>
          ✕ Discard & re-upload
        </button>

        {confirmable ? (
          <button className="import-primary-btn" onClick={onConfirm} disabled={confirming || finalizing} id="confirm-btn">
            {confirming ? 'Saving…' : '✓ Confirm & Save'}
          </button>
        ) : (
          <div className="import-blocked-msg">
            <strong>{preview.unresolved_count} blocks still require review.</strong>
            <br />
            <span style={{ fontSize: '0.9em' }}>
              Resolve them manually or finalize/exclude them to proceed with confirmation.
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Success phase
// ---------------------------------------------------------------------------

function SuccessPhase({ result }: { result: ImportConfirmResult }) {
  const navigate = useNavigate();
  return (
    <div className="import-success">
      <div className="import-success__icon" aria-hidden>
        ✅
      </div>
      <h2 className="import-success__heading">DOCX Timetables Saved</h2>
      <p className="import-success__sub">
        The DOCX import completed successfully. Draft timetables are now stored and ready for review.
      </p>

      <div className="import-success-stats">
        <div className="import-stat-card">
          <span className="import-stat-num">{result.teachers_created.length + result.teachers_reused.length}</span>
          <span className="import-stat-label">Teachers</span>
          {result.teachers_created.length > 0 && (
            <span className="import-stat-detail">{result.teachers_created.length} new</span>
          )}
          {result.teachers_reused.length > 0 && (
            <span className="import-stat-detail">{result.teachers_reused.length} reused</span>
          )}
        </div>
        <div className="import-stat-card">
          <span className="import-stat-num">{result.timetables_created.length + result.timetables_replaced.length}</span>
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
        <strong>Note:</strong> Timetables are saved as <em>Draft</em>. The Director can search for individual teachers
        to review their schedule.
      </div>

      <div className="import-success-actions">
        <button className="import-primary-btn" onClick={() => navigate('/import/docx')}>
          Import another DOCX
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

export function DOCXImportPage() {
  const [phase, setPhase] = useState<Phase>({ kind: 'upload' });
  const [confirming, setConfirming] = useState(false);
  const [confirmError, setConfirmError] = useState<string | null>(null);

  async function handleConfirm(preview: DOCXImportPreview) {
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

  function handleDiscard() {
    setPhase({ kind: 'upload' });
    setConfirmError(null);
  }

  function handleRefresh(updated: DOCXImportPreview) {
    if (phase.kind === 'previewing' || phase.kind === 'resolving') {
      setPhase({ kind: 'previewing', preview: updated });
    }
  }

  return (
    <Shell>
      <section className="page-card">
        {/* Header */}
        <div className="page-heading">
          <div>
            <span className="eyebrow">DOCX Import</span>
            <h1 className="import-page-title">Import DOCX Timetable</h1>
          </div>
          <Link to="/" className="back-link">
            ← Home
          </Link>
        </div>

        {/* Workflow steps */}
        <StepIndicator phase={phase} />

        {/* Phase content */}
        {phase.kind === 'upload' && <UploadPhase onPreview={(p) => setPhase({ kind: 'previewing', preview: p })} />}

        {(phase.kind === 'previewing' || phase.kind === 'resolving' || phase.kind === 'confirming') && (
          <PreviewPhase
            preview={phase.preview}
            onRefresh={handleRefresh}
            confirming={confirming}
            confirmError={confirmError}
            onConfirm={() => handleConfirm(phase.preview)}
            onDiscard={handleDiscard}
          />
        )}

        {phase.kind === 'success' && <SuccessPhase result={phase.result} />}
      </section>
    </Shell>
  );
}
