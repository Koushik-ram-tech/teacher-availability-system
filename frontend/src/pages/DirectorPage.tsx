import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { Shell } from '../components/Shell';
import { searchTeachers } from '../services/api';
import type { Teacher } from '../types';

const DEBOUNCE_MS = 300;

function TeacherCard({ teacher }: { teacher: Teacher }) {
  return (
    <Link
      className="dir-teacher-card"
      to={`/director/${teacher.id}`}
      id={`teacher-result-${teacher.id}`}
    >
      <div className="dir-teacher-card__avatar">
        {teacher.acronym.slice(0, 2)}
      </div>
      <div className="dir-teacher-card__info">
        <strong className="dir-teacher-card__name">{teacher.name}</strong>
        <span className="dir-teacher-card__meta">
          {teacher.acronym} &nbsp;·&nbsp; {teacher.level} &nbsp;·&nbsp; {teacher.program.name}
        </span>
      </div>
      <span className="dir-teacher-card__arrow">→</span>
    </Link>
  );
}

export function DirectorPage() {
  const [query, setQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQuery(query.trim()), DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [query]);

  const searchQuery = useQuery({
    queryKey: ['teacher-search', debouncedQuery],
    queryFn: () => searchTeachers(debouncedQuery),
    enabled: debouncedQuery.length > 0,
  });

  const teachers = searchQuery.data ?? [];

  return (
    <Shell>
      <section className="page-card">
        <Link className="back-link" to="/">← Home</Link>

        <div className="dir-hero">
          <span className="eyebrow">Director</span>
          <h1>Find a teacher</h1>
          <p className="subtitle">
            Search by name or acronym to view a teacher's confirmed schedule and availability.
          </p>
        </div>

        <div className="dir-search-wrap">
          <div className="dir-search-box">
            <span className="dir-search-icon" aria-hidden>🔍</span>
            <input
              id="director-search-input"
              ref={inputRef}
              className="dir-search-input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Name or acronym — e.g. Koushik, KR…"
              autoFocus
              autoComplete="off"
              spellCheck={false}
              aria-label="Search teacher"
            />
            {query && (
              <button
                className="dir-search-clear"
                onClick={() => { setQuery(''); setDebouncedQuery(''); inputRef.current?.focus(); }}
                aria-label="Clear search"
              >✕</button>
            )}
          </div>
        </div>

        {/* States */}
        {!debouncedQuery && (
          <div className="dir-empty-state">
            <span className="dir-empty-icon" aria-hidden>👤</span>
            <p>Start typing to search faculty.</p>
          </div>
        )}

        {debouncedQuery && searchQuery.isLoading && (
          <div className="dir-empty-state"><p className="muted">Searching…</p></div>
        )}

        {debouncedQuery && searchQuery.isError && (
          <div className="dir-empty-state">
            <p className="error-text">Search failed. Check the backend connection.</p>
          </div>
        )}

        {debouncedQuery && searchQuery.isSuccess && teachers.length === 0 && (
          <div className="dir-empty-state">
            <span className="dir-empty-icon" aria-hidden>😶</span>
            <p>No active teachers matched <strong>"{debouncedQuery}"</strong>.</p>
          </div>
        )}

        {teachers.length > 0 && (
          <div className="dir-results">
            <p className="dir-results-count">
              {teachers.length} result{teachers.length !== 1 ? 's' : ''}
            </p>
            <div className="dir-teacher-list">
              {teachers.map((t) => <TeacherCard key={t.id} teacher={t} />)}
            </div>
          </div>
        )}
      </section>
    </Shell>
  );
}
