import { Link, Route, Routes } from 'react-router-dom';

import { Shell } from './components/Shell';
import { AvailabilityPage } from './pages/AvailabilityPage';
import { DirectorPage } from './pages/DirectorPage';
import { DirectorTeacherPage } from './pages/DirectorTeacherPage';
import { DOCXImportPage } from './pages/DOCXImportPage';
import { ExcelImportPage } from './pages/ExcelImportPage';
import { HomePage } from './pages/HomePage';
import { TeacherPage } from './pages/TeacherPage';
import { TeacherTimetablePage } from './pages/TeacherTimetablePage';

function NotFound() {
  return (
    <Shell>
      <section className="page-card">
        <h1>Page not found</h1>
        <Link className="back-link" to="/">
          Return home
        </Link>
      </section>
    </Shell>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/availability" element={<AvailabilityPage />} />
      <Route path="/import" element={<ExcelImportPage />} />
      <Route path="/import/docx" element={<DOCXImportPage />} />
      <Route path="/teacher" element={<TeacherPage />} />
      <Route path="/teacher/:teacherId/timetable" element={<TeacherTimetablePage />} />
      <Route path="/director" element={<DirectorPage />} />
      <Route path="/director/:teacherId" element={<DirectorTeacherPage />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
