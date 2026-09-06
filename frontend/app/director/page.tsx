export default function DirectorPage() {
  return (
    <main style={{ padding: 32, fontFamily: "Arial, sans-serif" }}>
      <h1>Director Panel</h1>
      <p>Day 1 scaffold — teacher search and availability will be connected to the API next.</p>
      <section style={{ maxWidth: 720, marginTop: 24 }}>
        <label>
          Search teacher by name or acronym
          <input
            style={{ display: "block", width: "100%", marginTop: 6, padding: 8 }}
            placeholder="e.g. Koushik or KR"
          />
        </label>
      </section>
    </main>
  );
}
