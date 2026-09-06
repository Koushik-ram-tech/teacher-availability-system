export default function Home() {
  return (
    <main style={{ padding: 32, fontFamily: "Arial, sans-serif" }}>
      <h1>Teacher Availability System</h1>
      <p>Department timetable prototype.</p>
      <div style={{ display: "flex", gap: 16, marginTop: 24 }}>
        <a href="/teacher">Teacher Panel</a>
        <a href="/director">Director Panel</a>
      </div>
    </main>
  );
}
