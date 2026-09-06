import { FormEvent, useMemo, useState } from 'react';
import { Link, Route, Routes } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';

import { createTeacher, getHealth, getPrograms, searchTeachers } from './services/api';
import type { Level, TeacherCreatePayload } from './types';

const initialForm: TeacherCreatePayload = {
  name: '',
  acronym: '',
  level: 'PG',
  program_id: '',
  semester: 1,
  department: 'Prototype Department',
};

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <main className="shell">
      <div className="app-bar">
        <Link to="/" className="brand">Teacher Availability</Link>
        <div className="nav-links">
          <Link to="/teacher">Teacher</Link>
          <Link to="/director">Director</Link>
        </div>
      </div>
      {children}
    </main>
  );
}

function Home() {
  const health = useQuery({ queryKey: ['health'], queryFn: getHealth, retry: 0 });

  return (
    <Shell>
      <section className="hero">
        <div className="hero-topline">
          <span className="eyebrow">Department prototype</span>
          <span className={`status-pill ${health.isSuccess ? 'ok' : health.isError ? 'bad' : ''}`}>
            {health.isSuccess ? 'API connected' : health.isError ? 'API offline' : 'Checking API…'}
          </span>
        </div>
        <h1>Teacher Availability System</h1>
        <p className="subtitle">
          Maintain faculty timetables and let the Director quickly answer one question:
          <strong> is this teacher free, and when?</strong>
        </p>

        <div className="panel-grid">
          <Link className="panel-card" to="/teacher">
            <span className="panel-kicker">Teacher</span>
            <h2>Manage timetable</h2>
            <p>Create faculty profile and prepare the weekly schedule. Timetable entry comes next.</p>
          </Link>

          <Link className="panel-card" to="/director">
            <span className="panel-kicker">Director</span>
            <h2>Find availability</h2>
            <p>Search faculty by name or acronym and inspect their current records.</p>
          </Link>
        </div>
      </section>
    </Shell>
  );
}

function TeacherPage() {
  const [form, setForm] = useState<TeacherCreatePayload>(initialForm);
  const programsQuery = useQuery({ queryKey: ['programs'], queryFn: getPrograms });
  const mutation = useMutation({ mutationFn: createTeacher });

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
        <Link className="back-link" to="/">← Home</Link>
        <div className="page-heading">
          <div>
            <p className="eyebrow">Day 1 · Teacher vertical slice</p>
            <h1>Create teacher profile</h1>
            <p className="subtitle">This form saves a real record into the Supabase PostgreSQL database through FastAPI.</p>
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
            {mutation.isError && <p className="error-text">Could not create teacher. Check the API response and database constraints.</p>}
            {mutation.isSuccess && (
              <div className="success-box">
                <strong>Teacher created.</strong> {mutation.data.name} ({mutation.data.acronym}) is now stored in PostgreSQL.
              </div>
            )}
          </div>
        </form>
      </section>
    </Shell>
  );
}

function DirectorPage() {
  const [query, setQuery] = useState('');
  const searchQuery = useQuery({
    queryKey: ['teacher-search', query],
    queryFn: () => searchTeachers(query),
    enabled: query.trim().length > 0,
  });

  return (
    <Shell>
      <section className="page-card">
        <Link className="back-link" to="/">← Home</Link>
        <p className="eyebrow">Day 1 · Director vertical slice</p>
        <h1>Find a teacher</h1>
        <p className="subtitle">Search the live PostgreSQL records by name or institutional acronym.</p>

        <label className="search-field">
          Search teacher
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Try Koushik, Ram, or KR"
          />
        </label>

        <div className="results-list">
          {query.trim() && searchQuery.isLoading && <p className="muted">Searching…</p>}
          {query.trim() && searchQuery.isError && <p className="error-text">Search failed. Check the backend connection.</p>}
          {query.trim() && searchQuery.isSuccess && searchQuery.data.length === 0 && (
            <p className="muted">No active teachers matched “{query}”.</p>
          )}
          {(searchQuery.data ?? []).map((teacher) => (
            <article className="result-card" key={teacher.id}>
              <div>
                <h2>{teacher.name}</h2>
                <p>{teacher.acronym} · {teacher.level} · {teacher.program.name} · Semester {teacher.semester}</p>
              </div>
              <span className="status-pill ok">Saved</span>
            </article>
          ))}
        </div>
      </section>
    </Shell>
  );
}

function NotFound() {
  return (
    <Shell>
      <section className="page-card">
        <h1>Page not found</h1>
        <Link className="back-link" to="/">Return home</Link>
      </section>
    </Shell>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/teacher" element={<TeacherPage />} />
      <Route path="/director" element={<DirectorPage />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
