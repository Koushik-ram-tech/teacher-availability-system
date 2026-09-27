import type { ResourceCatalogItem } from '../types';

interface ResourceResolutionSelectProps {
  resources: ResourceCatalogItem[];
  value: string;
  onChange: (value: string) => void;
  loading?: boolean;
  error?: string | null;
  required?: boolean;
}

export function ResourceResolutionSelect({
  resources,
  value,
  onChange,
  loading = false,
  error,
  required = false,
}: ResourceResolutionSelectProps) {
  return (
    <label className="resource-resolution-select">
      <strong>Resource{required ? ' *' : ' (optional)'}:</strong>
      <select
        aria-label="Resource"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={loading || Boolean(error)}
        required={required}
      >
        <option value="">{loading ? 'Loading resources…' : '-- Select resource --'}</option>
        {resources.map((resource) => (
          <option key={resource.id} value={resource.code}>{resource.code}</option>
        ))}
      </select>
      {error && <span className="error-text" role="alert">{error}</span>}
      {!loading && !error && resources.length === 0 && (
        <span className="muted">No resources are available for these slots.</span>
      )}
    </label>
  );
}