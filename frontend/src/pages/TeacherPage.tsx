import { useMemo, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';

import { Shell } from '../components/Shell';
import { createTeacher, getPrograms, listTeachers } from '../services/api';
import type { Level, TeacherCreatePayload } from '../types';

const initialForm: TeacherCreatePayload = {
  name: '',
  acronym: '',
  level: 'PG',
  program_id: '',
  semester: 1,
  department: 'Prototype Department',
};

export function TeacherPage() {
  const [form, setForm] = useState<TeacherCreatePayload>(initialForm);
  const programsQuery = useQuery({ queryKey: ['programs'], queryFn: getPrograms });
  const teachersQuery = useQuery({ queryKey: ['teachers'], queryFn: listTeachers });
  const mutation = useMutation({
    mutationFn: createTeacher,
    onSuccess: () => {
      teachersQuery.refetch();
    },
  });

  const programs = programsQuery.data ?? [];
  const filteredPrograms = useMemo(
    () => programs.filter((program) => program.level === form.level),
    [programs, form.level],
  );

  function setLevel(level: Level) {
    const nextPrograms = programs.filter((program) => program.level === level);
    setForm((current) => ({
      ...current,
      level,
      program_id: nextPrograms[0]?.id ?? '',
    }));
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    mutation.reset();
    mutation.mutate({
      ...form,
      name: form.name.trim(),
      acronym: form.acronym.trim().toUpperCase(),
      department: form.department.trim(),
    });
  }

  return (
    <Shell>
      <section className="page-card">
        <Link className="back-link" to="/">
          ← Home
        </Link>
        <div className="page-heading">
          <div>
            <p className="eyebrow">Day 1 · Teacher vertical slice</p>
            <h1>Create teacher profile</h1>
            <p className="subtitle">This form saves a real record into PostgreSQL through FastAPI.</p>
          </div>
          <span className="status-pill">MVP</span>
        </div>

        <form className="form-grid" onSubmit={handleSubmit}>
          <label>
            Teacher name
            <input
              required
              value={form.name}
              onChange={(event) => setForm({ ...form, name: event.target.value })}
              placeholder="e.g. Dr. Anita Rao"
            />
          </label>

          <label>
            Institutional acronym
            <input
              required
              value={form.acronym}
              onChange={(event) => setForm({ ...form, acronym: event.target.value })}
              placeholder="e.g. AR"
              maxLength={50}
            />
          </label>

          <label>
            Level
            <select value={form.level} onChange={(event) => setLevel(event.target.value as Level)}>
              <option value="UG">UG</option>
              <option value="PG">PG</option>
            </select>
          </label>

          <label>
            Program / branch
            <select
              required
              value={form.program_id}
              onChange={(event) => setForm({ ...form, program_id: event.target.value })}
              disabled={programsQuery.isLoading || filteredPrograms.length === 0}
            >
              <option value="">Select program</option>
              {filteredPrograms.map((program) => (
                <option key={program.id} value={program.id}>
                  {program.name}
                </option>
              ))}
            </select>
          </label>

          <label>
            Semester
            <select
              value={form.semester}
              onChange={(event) => setForm({ ...form, semester: Number(event.target.value) })}
            >
              {Array.from({ length: 8 }, (_, index) => index + 1).map((semester) => (
                <option key={semester} value={semester}>
                  Semester {semester}
                </option>
              ))}
            </select>
          </label>

          <label>
            Department
            <input
              required
              value={form.department}
              onChange={(event) => setForm({ ...form, department: event.target.value })}
              placeholder="Prototype Department"
            />
          </label>

          <div className="form-actions">
            <button type="submit" disabled={mutation.isPending || !form.program_id}>
              {mutation.isPending ? 'Saving…' : 'Create teacher'}
            </button>
            {programsQuery.isError && <p className="error-text">Unable to load programs. Is the backend running?</p>}
            {mutation.isError && (
              <p className="error-text">Could not create teacher. Check the API response and database constraints.</p>
            )}
            {mutation.isSuccess && (
              <div className="success-box">
                <strong>Teacher created.</strong> {mutation.data.name} ({mutation.data.acronym}) is now stored in
                PostgreSQL.{' '}
                <Link to={`/teacher/${mutation.data.id}/timetable`}>Build their timetable →</Link>
              </div>
            )}
          </div>
        </form>
      </section>

      <section className="page-card">
        <div className="page-heading">
          <div>
            <p className="eyebrow">Existing teachers</p>
            <h1>Open a timetable</h1>
          </div>
        </div>

        {teachersQuery.isLoading && <p className="muted">Loading teachers…</p>}
        {teachersQuery.isError && <p className="error-text">Could not load the teacher list.</p>}
        {teachersQuery.isSuccess && teachersQuery.data.length === 0 && (
          <p className="muted">No teachers yet. Create one above to get started.</p>
        )}

        <div className="results-list">
          {(teachersQuery.data ?? []).map((teacher) => (
            <article className="result-card" key={teacher.id}>
              <div>
                <h2>{teacher.name}</h2>
                <p>
                  {teacher.acronym} · {teacher.level} · {teacher.program.name} · Semester {teacher.semester}
                </p>
              </div>
              <Link className="back-link" to={`/teacher/${teacher.id}/timetable`}>
                Edit timetable →
              </Link>
            </article>
          ))}
        </div>
      </section>
    </Shell>
  );
}
