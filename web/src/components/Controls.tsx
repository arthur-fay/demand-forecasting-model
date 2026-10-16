import { useId } from 'react';

/** A labelled radio group styled as a segmented control (arrow keys move the choice). */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
  hideLabel = false,
}: {
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
  hideLabel?: boolean;
}) {
  const name = useId();
  return (
    <fieldset className="filter-fieldset">
      <legend className={hideLabel ? 'visually-hidden' : 'filter-label'}>{label}</legend>
      <div className="segmented">
        {options.map((option) => (
          <label key={option.value}>
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={option.value === value}
              onChange={() => {
                onChange(option.value);
              }}
            />
            {option.label}
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export type View = 'chart' | 'table';

export function ViewToggle({
  value,
  onChange,
  label,
}: {
  value: View;
  onChange: (v: View) => void;
  label: string;
}) {
  return (
    <Segmented
      label={`${label}: show as`}
      hideLabel
      value={value}
      onChange={onChange}
      options={[
        { value: 'chart', label: 'Chart' },
        { value: 'table', label: 'Table' },
      ]}
    />
  );
}

export function Stat({
  label,
  value,
  unit,
  note,
  tags,
  kind = 'number',
}: {
  label: string;
  value: React.ReactNode;
  unit?: string;
  note?: React.ReactNode;
  tags?: React.ReactNode;
  /** `status`: the value is a status word, not a number. */
  kind?: 'number' | 'status';
}) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value" data-kind={kind}>
        {value}
        {unit !== undefined && <span className="unit">{unit}</span>}
      </span>
      {tags}
      {note !== undefined && <span className="stat-note">{note}</span>}
    </div>
  );
}
