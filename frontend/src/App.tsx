import { Link, Route, Routes } from 'react-router-dom';

function Home() {
  return (
    <main className="shell">
      <section className="hero">
        <p className="eyebrow">Department prototype</p>
        <h1>Teacher Availability System</h1>
        <p className="subtitle">
          Maintain faculty timetables and let the Director quickly see schedules and free periods.
        </p>

        <div className="panel-grid">
          <Link className="panel-card" to="/teacher">
            <span className="panel-kicker">Teacher</span>
            <h2>Manage timetable</h2>
            <p>Enter or review weekly schedule information.</p>
          </Link>

          <Link className="panel-card" to="/director">
            <span className="panel-kicker">Director</span>
            <h2>Find availability</h2>
            <p>Search a teacher and view daily or weekly availability.</p>
          </Link>
        </div>
      </section>
    </main>
  );
}

function TeacherPage() {
  return (
    <main className="shell">
      <section className="page-card">
        <Link className="back-link" to="/">← Home</Link>
        <p className="eyebrow">Teacher panel</p>
        <h1>Teacher timetable</h1>
        <p className="subtitle">Core timetable editor will be implemented in the first vertical slice.</p>
      </section>
    </main>
  );
}

function DirectorPage() {
  return (
    <main className="shell">
      <section className="page-card">
        <Link className="back-link" to="/">← Home</Link>
        <p className="eyebrow">Director panel</p>
        <h1>Teacher availability</h1>
        <p className="subtitle">Search and schedule views will be wired to FastAPI next.</p>
      </section>
    </main>
  );
}

function NotFound() {
  return (
    <main className="shell">
      <section className="page-card">
        <h1>Page not found</h1>
        <Link className="back-link" to="/">Return home</Link>
      </section>
    </main>
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
