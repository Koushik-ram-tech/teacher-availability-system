import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

export function Shell({ children }: { children: ReactNode }) {
  return (
    <main className="shell">
      <div className="app-bar">
        <Link to="/" className="brand">Teacher Availability</Link>
        <div className="nav-links">
          <Link to="/import">Import</Link>
          <Link to="/teacher">Teacher</Link>
          <Link to="/director">Director</Link>
        </div>
      </div>
      {children}
    </main>
  );
}
