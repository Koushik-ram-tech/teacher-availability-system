import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

import { AvailabilityGrid } from './pages/AvailabilityPage';
import type { ResourceAvailability, TeacherAvailability } from './types';

function teacherAvailability(overrides: Partial<TeacherAvailability> = {}): TeacherAvailability {
  return {
    teacher: { id: 't-1', name: 'RMR Teacher', acronym: 'RMR' },
    academic_year: '2025-2026',
    has_confirmed_timetable: true,
    days: {
      monday: {
        day: 'monday',
        slots: {
          S1: { slot_code: 'S1', status: 'FREE' },
          S2: { slot_code: 'S2', status: 'OCCUPIED', subject_or_activity: 'DBMS', section: 'I-A', room: 'CA1' },
        },
      },
    },
    ...overrides,
  };
}

function resourceAvailability(): ResourceAvailability {
  return {
    resource: { id: 'r-1', code: 'LAB1B', name: 'LAB1B' },
    academic_year: '2025-2026',
    days: {
      monday: {
        day: 'monday',
        slots: {
          S1: { slot_code: 'S1', status: 'OCCUPIED', subject_or_activity: 'Python Lab', room: 'LAB1B' },
          S2: { slot_code: 'S2', status: 'FREE' },
        },
      },
    },
  };
}

describe('AvailabilityGrid backend response shape', () => {
  afterEach(cleanup);

  it('renders the nested teacher name from the availability API', () => {
    render(<AvailabilityGrid data={teacherAvailability()} mode="teacher" />);
    expect(screen.getByRole('heading', { name: 'RMR Teacher' })).toBeTruthy();
    expect(screen.getByText('RMR')).toBeTruthy();
    expect(screen.getAllByText('OCCUPIED').length).toBeGreaterThan(0);
  });

  it('renders the nested resource name from the availability API', () => {
    render(<AvailabilityGrid data={resourceAvailability()} mode="resource" />);
    expect(screen.getByRole('heading', { name: 'LAB1B' })).toBeTruthy();
    expect(screen.getAllByText('OCCUPIED').length).toBeGreaterThan(0);
    expect(screen.getAllByText('FREE').length).toBeGreaterThan(0);
  });

  it('shows a notice when the teacher has no confirmed timetable', () => {
    const data = teacherAvailability({
      has_confirmed_timetable: false,
      days: {
        monday: {
          day: 'monday',
          slots: {
            S1: { slot_code: 'S1', status: 'UNKNOWN' },
          },
        },
      },
    });
    render(<AvailabilityGrid data={data} mode="teacher" />);
    expect(screen.getByText('No confirmed timetable for this year')).toBeTruthy();
    expect(screen.getAllByText('UNKNOWN').length).toBeGreaterThan(0);
  });
});
