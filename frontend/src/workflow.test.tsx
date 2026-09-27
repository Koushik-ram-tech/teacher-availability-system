import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AcademicYearSelect } from './components/AcademicYearSelect';
import { ResourceResolutionSelect } from './components/ResourceResolutionSelect';
import { WeeklyMatrix } from './pages/DirectorTeacherPage';
import { TeacherTimetablePage } from './pages/TeacherTimetablePage';
import { getAcademicYearOptions } from './academicYears';
import { getAvailableResourceCandidates } from './resourceCandidates';
import { FALLBACK_PERIODS, type DOCXResolvedActivity, type DOCXUnresolvedBlock, type ResourceCatalogItem, type ScheduleEntryView } from './types';
import { formatSlotCodesRange, formatTime, formatTimeRange } from './time';

vi.mock('./services/api', () => ({
  getTeacher: vi.fn(async (id: string) => ({
    id,
    name: 'Imported Teacher',
    acronym: 'IT',
    level: 'PG',
    program_id: 'program-1',
    semester: 1,
    department: 'Computer Applications',
    is_active: true,
    program: { id: 'program-1', name: 'MCA', level: 'PG', is_active: true },
  })),
  getTimetableDay: vi.fn(async (_teacherId: string, day: string, academicYear: string, status: string) => {
    if (status === 'DRAFT') throw { isAxiosError: true, response: { status: 404 } };
    const entry = scheduleEntry('imported', ['S6', 'S7']);
    return {
      teacher_id: 'teacher-1',
      academic_year: academicYear,
      day,
      timetable_status: 'CONFIRMED',
      periods: FALLBACK_PERIODS.map((period) => ({
        ...period,
        entry: period.kind === 'SLOT' && ['S6', 'S7'].includes(period.code ?? '') ? entry : null,
      })),
    };
  }),
  confirmTimetable: vi.fn(),
  createTimetable: vi.fn(),
  updateTimetable: vi.fn(),
}));

const resourceCatalog: ResourceCatalogItem[] = [
  { id: 'ca1', code: 'CA1', name: 'CA1', resource_type: 'CLASSROOM' },
  { id: 'ca2', code: 'CA2', name: 'CA2', resource_type: 'CLASSROOM' },
  { id: 'unused', code: 'Unused Lab', name: 'Unused Lab', resource_type: 'LAB' },
];

function placementBlock(day: string): DOCXUnresolvedBlock {
  return {
    block_id: 'placement',
    day,
    section: 'I-B',
    slots: ['S6', 'S7', 'S8'],
    source_location: 'DOCX: row 1',
    activity_candidates: [],
    teacher_candidates: [],
    resource_candidates: [],
    activity_semantic_status: 'RESOLVED',
    teacher_occupancy_status: 'UNSPECIFIED',
    resource_occupancy_status: 'AMBIGUOUS',
    participation_policy: 'STUDENT_MANAGED',
    is_student_managed: true,
    ambiguity_reason: 'resource allocation ambiguous',
    resolution_required: true,
  };
}

function resolvedPlacement(day: string, resource: string): DOCXResolvedActivity {
  return {
    day,
    section: 'I-A',
    slots: ['S6', 'S7', 'S8'],
    teacher_acronym: '',
    subject_or_activity: 'Placement',
    resource_code: resource,
    resource_codes: [resource],
    source_location: 'DOCX: row 2',
    entry_type: 'OTHER',
    is_multi_slot: true,
    is_manually_resolved: true,
  };
}

describe('import workflow controls', () => {
  beforeEach(() => vi.useFakeTimers().setSystemTime(new Date('2026-09-27T12:00:00')));
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('offers the current and next two academic years', () => {
    expect(getAcademicYearOptions()).toEqual(['2026-2027', '2027-2028', '2028-2029']);
    render(<AcademicYearSelect id="academic-year" value="2026-2027" onChange={() => undefined} />);
    expect(screen.getAllByRole('option').map((option) => (option as HTMLOptionElement).value))
      .toEqual(['2026-2027', '2027-2028', '2028-2029']);
  });

  it('renders every canonical resource supplied by the catalog', () => {
    render(<ResourceResolutionSelect resources={resourceCatalog} value="" onChange={() => undefined} />);
    const options = screen.getAllByRole('option').map((option) => option.textContent);
    expect(options).toContain('CA1');
    expect(options).toContain('CA2');
    expect(options).toContain('Unused Lab');
  });

  it('filters a resolved resource only for same-day overlapping slots', () => {
    const thursday = getAvailableResourceCandidates(
      resourceCatalog,
      [resolvedPlacement('thursday', 'CA1')],
      placementBlock('thursday'),
    );
    const friday = getAvailableResourceCandidates(
      resourceCatalog,
      [resolvedPlacement('thursday', 'CA1')],
      placementBlock('friday'),
    );
    expect(thursday.map((resource) => resource.code)).toEqual(['CA2', 'Unused Lab']);
    expect(friday.map((resource) => resource.code)).toEqual(['CA1', 'CA2', 'Unused Lab']);
  });

  it('formats clock times and multi-slot ranges in 12-hour form', () => {
    expect(formatTime('08:00')).toBe('8:00 AM');
    expect(formatTime('09:50:00')).toBe('9:50 AM');
    expect(formatTime('14:00')).toBe('2:00 PM');
    expect(formatTimeRange('15:50', '16:45')).toBe('3:50 PM – 4:45 PM');
    expect(formatSlotCodesRange(['S6', 'S7'])).toBe('2:00 PM – 3:50 PM');
  });
});

function scheduleEntry(id: string, slotCodes: string[]): ScheduleEntryView {
  return {
    id,
    entry_type: 'CLASS',
    subject_or_activity: `Activity ${id}`,
    section: 'I-A',
    slot_codes: slotCodes,
  };
}

describe('weekly timetable visualization', () => {
  it('renders one spanning cell with its full range for a multi-slot entry', () => {
    const entry = scheduleEntry('multi', ['S6', 'S7']);
    const periods = FALLBACK_PERIODS.map((period) => ({
      ...period,
      entry: period.kind === 'SLOT' && ['S6', 'S7'].includes(period.code ?? '') ? entry : null,
    }));
    const { container } = render(
      <WeeklyMatrix
        weekData={[{ day: 'monday', periods, hasConfirmed: true }]}
        activeDay="monday"
        onDayClick={() => undefined}
      />,
    );

    const cell = screen.getByText('Activity multi').closest('td');
    expect(cell?.getAttribute('rowspan')).toBe('2');
    expect(cell?.textContent).toContain('2:00 PM – 3:50 PM');
    expect(container.querySelectorAll('.wm-cell--continuation')).toHaveLength(0);
  });

  it('keeps different activities in adjacent slots as separate cells', () => {
    const first = scheduleEntry('first', ['S6']);
    const second = scheduleEntry('second', ['S7']);
    const periods = FALLBACK_PERIODS.map((period) => ({
      ...period,
      entry: period.kind !== 'SLOT'
        ? null
        : period.code === 'S6'
          ? first
          : period.code === 'S7'
            ? second
            : null,
    }));
    render(
      <WeeklyMatrix
        weekData={[{ day: 'monday', periods, hasConfirmed: true }]}
        activeDay="monday"
        onDayClick={() => undefined}
      />,
    );

    expect(screen.getByText('Activity first').closest('td')?.rowSpan).toBe(1);
    expect(screen.getByText('Activity second').closest('td')?.rowSpan).toBe(1);
  });
});

describe('teacher view after import confirmation', () => {
  afterEach(cleanup);

  it('loads the confirmed timetable read-only when no draft exists', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/teacher/teacher-1/timetable']}>
          <Routes>
            <Route path="/teacher/:teacherId/timetable" element={<TeacherTimetablePage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('This timetable was confirmed as part of the import and is read-only here.'))
      .toBeTruthy();
    expect(screen.getByText('CONFIRMED')).toBeTruthy();
    expect(screen.getByText('Imported Teacher (IT)')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Save draft timetable' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Approve & Publish' })).toBeNull();
  });
});