import axios from 'axios';

import type { Program, Teacher, TeacherCreatePayload } from '../types';

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

export async function searchTeachers(query: string): Promise<Teacher[]> {
  const response = await api.get<Teacher[]>('/teachers/search', {
    params: { q: query },
  });
  return response.data;
}

export async function getHealth(): Promise<{ status: string; database: string }> {
  const response = await api.get<{ status: string; database: string }>('/health');
  return response.data;
}
