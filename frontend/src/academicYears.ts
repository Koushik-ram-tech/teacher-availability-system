export function getCurrentAcademicYear(now: Date = new Date()): string {
  const year = now.getFullYear() - (now.getMonth() < 6 ? 1 : 0);
  return `${year}-${year + 1}`;
}

export function getAcademicYearOptions(now: Date = new Date()): string[] {
  const currentYear = Number(getCurrentAcademicYear(now).slice(0, 4));
  return Array.from({ length: 3 }, (_, index) => {
    const startYear = currentYear + index;
    return `${startYear}-${startYear + 1}`;
  });
}