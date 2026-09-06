const slots = [
  ["S1", "08:00", "08:55"],
  ["S2", "08:55", "09:50"],
  ["S3", "09:50", "10:45"],
  ["S4", "11:15", "12:10"],
  ["S5", "12:10", "13:05"],
  ["S6", "14:00", "14:55"],
  ["S7", "14:55", "15:50"],
  ["S8", "15:50", "16:45"],
  ["S9", "16:45", "17:40"],
] as const;

export default function TeacherPage() {
  return (
    <main style={{ padding: 32, fontFamily: "Arial, sans-serif" }}>
      <h1>Teacher Panel</h1>
      <p>Day 1 scaffold — timetable editor will be wired to the API next.</p>
      <section style={{ maxWidth: 720, marginTop: 24 }}>
        <label>
          Teacher name
          <input style={{ display: "block", width: "100%", marginTop: 6, padding: 8 }} placeholder="Enter name" />
        </label>
        <label style={{ display: "block", marginTop: 16 }}>
          Acronym
          <input style={{ display: "block", width: "100%", marginTop: 6, padding: 8 }} placeholder="e.g. KR" />
        </label>
        <label style={{ display: "block", marginTop: 16 }}>
          Level
          <select style={{ display: "block", marginTop: 6, padding: 8 }}>
            <option>UG</option>
            <option>PG</option>
          </select>
        </label>
      </section>

      <h2 style={{ marginTop: 32 }}>Monday</h2>
      <div style={{ display: "grid", gap: 8, maxWidth: 720 }}>
        {slots.map(([code, start, end]) => (
          <div key={code} style={{ border: "1px solid #ccc", padding: 12 }}>
            <strong>{code}</strong> — {start}–{end}
            <input
              aria-label={`${code} activity`}
              style={{ display: "block", width: "100%", marginTop: 8, padding: 8 }}
              placeholder="Subject / activity"
            />
          </div>
        ))}
      </div>
    </main>
  );
}
