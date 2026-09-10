import axios from 'axios';

import type {
  ImportConfirmResult,
  ImportPreview,
  Program,
  Teacher,
  TeacherCreatePayload,
  TimetableConfirmResponse,
  TimetableDayResponse,
  TimetableStatus,
  TimetableWritePayload,
  TimetableWriteResponse,
} from '../types';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1',
  timeout: 10_000,
});

export async function getPrograms(): Promise<Program[]> {
  const response = await api.get<Program[]>('/teachers/programs');
  return response.data;
}

export async function createTeacher(payload: TeacherCreatePayload): Promise<Teacher> {
  const response = await api.post<Teacher>('/teachers', payload);
  return response.data;
}

export async function listTeachers(): Promise<Teacher[]> {
  const response = await api.get<Teacher[]>('/teachers');
  return response.data;
}

export async function searchTeachers(query: string): Promise<Teacher[]> {
  const response = await api.get<Teacher[]>('/teachers/search', {
    params: { q: query },
  });
  return response.data;
}

export async function getTeacher(teacherId: string): Promise<Teacher> {
  const response = await api.get<Teacher>(`/teachers/${teacherId}`);
  return response.data;
}

export async function getHealth(): Promise<{ status: string; database: string }> {
  const response = await api.get<{ status: string; database: string }>('/health');
  return response.data;
}

export async function getTimetableDay(
  teacherId: string,
  day: string,
  academicYear: string,
  status: TimetableStatus,
): Promise<TimetableDayResponse> {
  const response = await api.get<TimetableDayResponse>(`/teachers/${teacherId}/timetable`, {
    params: { day, academic_year: academicYear, status },
  });
  return response.data;
}

export async function createTimetable(
  teacherId: string,
  payload: TimetableWritePayload,
): Promise<TimetableWriteResponse> {
  const response = await api.post<TimetableWriteResponse>(`/teachers/${teacherId}/timetable`, payload);
  return response.data;
}

export async function updateTimetable(
  teacherId: string,
  payload: TimetableWritePayload,
): Promise<TimetableWriteResponse> {
  const response = await api.put<TimetableWriteResponse>(`/teachers/${teacherId}/timetable`, payload);
  return response.data;
}

/**
 * Promote a teacher's DRAFT timetable to CONFIRMED for the given academic year.
 * Returns 404 if no DRAFT exists, 409 if already CONFIRMED, 422 on validation errors.
 */
export async function confirmTimetable(
  teacherId: string,
  academicYear: string,
): Promise<TimetableConfirmResponse> {
  const response = await api.post<TimetableConfirmResponse>(
    `/teachers/${teacherId}/timetable/confirm`,
    null,
    { params: { academic_year: academicYear } },
  );
  return response.data;
}

// ---------------------------------------------------------------------------
// Excel Import API
// ---------------------------------------------------------------------------

/**
 * Upload an .xlsx workbook for parsing + validation.
 * Returns a staged ImportPreview (nothing written to DB yet).
 *
 * @param file          The .xlsx file selected by the user.
 * @param academicYear  Academic year string (e.g. "2026-2027") supplied by the UI.
 */
export async function uploadExcel(file: File, academicYear: string): Promise<ImportPreview> {
  const form = new FormData();
  form.append('file', file);
  form.append('academic_year', academicYear.trim());
  const response = await api.post<ImportPreview>('/imports/excel', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 30_000, // larger file uploads may take longer
  });
  return response.data;
}

/**
 * Retrieve a previously staged import preview by its import_id.
 */
export async function getImport(importId: string): Promise<ImportPreview> {
  const response = await api.get<ImportPreview>(`/imports/${importId}`);
  return response.data;
}

/**
 * Confirm a staged import — persists teachers + DRAFT timetables.
 * The server revalidates before writing; returns summary counts on success.
 */
export async function confirmImport(importId: string): Promise<ImportConfirmResult> {
  const response = await api.post<ImportConfirmResult>(`/imports/${importId}/confirm`, null, {
    timeout: 30_000, // bulk insert operations may take longer
  });
  return response.data;
}

/**
 * Discard a staged import without persisting anything.
 */
export async function deleteImport(importId: string): Promise<void> {
  await api.delete(`/imports/${importId}`);
}
