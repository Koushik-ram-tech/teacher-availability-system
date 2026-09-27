import { getAcademicYearOptions } from '../academicYears';

interface AcademicYearSelectProps {
  id: string;
  value: string;
  onChange: (value: string) => void;
  className?: string;
  required?: boolean;
  disabled?: boolean;
}

export function AcademicYearSelect({
  id,
  value,
  onChange,
  className,
  required = false,
  disabled = false,
}: AcademicYearSelectProps) {
  const options = getAcademicYearOptions();
  const availableOptions = options.includes(value) || !value ? options : [...options, value].sort();

  return (
    <select
      id={id}
      className={className}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      required={required}
      disabled={disabled}
      aria-label="Academic year"
    >
      {!value && <option value="">Select academic year</option>}
      {availableOptions.map((academicYear) => (
        <option key={academicYear} value={academicYear}>{academicYear}</option>
      ))}
    </select>
  );
}