import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { Shell } from '../components/Shell';
import { getHealth } from '../services/api';

export function HomePage() {
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

        <div className="panel-grid" style={{ gridTemplateColumns: 'repeat(3, minmax(0,1fr))' }}>
          <Link className="panel-card" to="/import">
            <span className="panel-kicker">New workflow</span>
            <h2>Import Timetable</h2>
            <p>Upload an Excel workbook to create or update draft timetables for all teachers at once.</p>
          </Link>

          <Link className="panel-card" to="/teacher">
            <span className="panel-kicker">Teacher</span>
            <h2>Manage timetable</h2>
            <p>Create a faculty profile and build the weekly schedule.</p>
          </Link>

          <Link className="panel-card" to="/director">
            <span className="panel-kicker">Director</span>
            <h2>Find availability</h2>
            <p>Search faculty by name or acronym and inspect their schedule.</p>
          </Link>
        </div>
      </section>
    </Shell>
  );
}
