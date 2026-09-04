/** `.seg`/`.seg-opt` — a small set of mutually-exclusive radio options rendered as a segmented control (paste-vs-URL, etc). */
export function SegmentedControl<T extends string>({
  name,
  value,
  options,
  onChange,
  style,
}: {
  name: string
  value: T
  options: { value: T; label: string; testId?: string }[]
  onChange: (value: T) => void
  style?: React.CSSProperties
}) {
  return (
    <div className="seg" style={style}>
      {options.map((option) => (
        <label className="seg-opt" key={option.value}>
          <input
            type="radio"
            name={name}
            data-testid={option.testId}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
          />
          {option.label}
        </label>
      ))}
    </div>
  )
}
