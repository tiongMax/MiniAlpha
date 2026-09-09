import { useId, useState } from 'react'

interface Line {
  key: string
  label: string
  color: string
}
interface SeriesChartProps {
  title: string
  records: Record<string, unknown>[]
  lines: Line[]
  format?: 'number' | 'percent'
}

const WIDTH = 760
const HEIGHT = 240
const LEFT = 58
const RIGHT = 18
const TOP = 16
const BOTTOM = 28
const number = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 })
const percentage = new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 })

export function SeriesChart({ title, records, lines, format = 'number' }: SeriesChartProps) {
  const id = useId()
  const [cursor, setCursor] = useState<number | null>(null)
  const [hidden, setHidden] = useState<string[]>([])
  const points = records.filter(
    (record) =>
      typeof record.timestamp === 'string' && Number.isFinite(Date.parse(record.timestamp)),
  )
  const visible = lines.filter((line) => !hidden.includes(line.key))
  const values = points.flatMap((point) =>
    visible.flatMap((line) =>
      typeof point[line.key] === 'number' && Number.isFinite(point[line.key])
        ? [point[line.key] as number]
        : [],
    ),
  )
  if (points.length < 2 || !values.length) return null
  const minimum = Math.min(...values)
  const maximum = Math.max(...values)
  const padding = (maximum - minimum) * 0.08 || Math.max(Math.abs(maximum) * 0.05, 0.01)
  const low = minimum - padding
  const high = maximum + padding
  const firstTime = Date.parse(points[0].timestamp as string)
  const lastTime = Date.parse(points.at(-1)!.timestamp as string)
  const x = (point: Record<string, unknown>) =>
    LEFT +
    ((Date.parse(point.timestamp as string) - firstTime) / (lastTime - firstTime || 1)) *
      (WIDTH - LEFT - RIGHT)
  const y = (value: number) => TOP + ((high - value) / (high - low)) * (HEIGHT - TOP - BOTTOM)
  const selected = cursor === null ? points.length - 1 : Math.min(cursor, points.length - 1)
  const formatValue = (value: unknown) =>
    typeof value === 'number' && Number.isFinite(value)
      ? (format === 'percent' ? percentage : number).format(value)
      : '—'
  const date = (point: Record<string, unknown>) =>
    new Date(point.timestamp as string).toLocaleDateString()
  return (
    <figure className="series-chart" aria-labelledby={id}>
      <figcaption id={id}>{title}</figcaption>
      <div className="chart-legend">
        {lines.map((line) => (
          <button
            key={line.key}
            aria-pressed={!hidden.includes(line.key)}
            disabled={!hidden.includes(line.key) && visible.length === 1}
            onClick={() =>
              setHidden((current) =>
                current.includes(line.key)
                  ? current.filter((key) => key !== line.key)
                  : [...current, line.key],
              )
            }
          >
            <i style={{ background: line.color }} />
            {line.label}
            <strong>{formatValue(points[selected][line.key])}</strong>
          </button>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`${title}. Use the observation slider below for exact values.`}
        onPointerMove={(event) => {
          const bounds = event.currentTarget.getBoundingClientRect()
          const targetX = ((event.clientX - bounds.left) / bounds.width) * WIDTH
          let nearest = 0
          for (let index = 1; index < points.length; index += 1)
            if (Math.abs(x(points[index]) - targetX) < Math.abs(x(points[nearest]) - targetX))
              nearest = index
          setCursor(nearest)
        }}
      >
        {[0, 0.5, 1].map((fraction) => {
          const value = low + (high - low) * fraction
          return (
            <g key={fraction}>
              <line
                x1={LEFT}
                x2={WIDTH - RIGHT}
                y1={y(value)}
                y2={y(value)}
                stroke="#e6ece3"
                strokeDasharray="3 5"
              />
              <text x={LEFT - 8} y={y(value) + 4} textAnchor="end" fill="#7b8974" fontSize="10">
                {formatValue(value)}
              </text>
            </g>
          )
        })}
        {visible.map((line) => {
          let gap = true
          const path = points
            .map((point) => {
              const value = point[line.key]
              if (typeof value !== 'number' || !Number.isFinite(value)) {
                gap = true
                return ''
              }
              const command = `${gap ? 'M' : 'L'}${x(point).toFixed(2)},${y(value).toFixed(2)}`
              gap = false
              return command
            })
            .join(' ')
          return (
            <path
              key={line.key}
              d={path}
              fill="none"
              stroke={line.color}
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
          )
        })}
        <line
          x1={x(points[selected])}
          x2={x(points[selected])}
          y1={TOP}
          y2={HEIGHT - BOTTOM}
          stroke="#8ea483"
          strokeDasharray="4 4"
        />
        <text x={LEFT} y={HEIGHT - 5} fill="#7b8974" fontSize="10">
          {date(points[0])}
        </text>
        <text x={WIDTH - RIGHT} y={HEIGHT - 5} textAnchor="end" fill="#7b8974" fontSize="10">
          {date(points.at(-1)!)}
        </text>
      </svg>
      <label className="chart-slider">
        Observation: {date(points[selected])}
        <input
          aria-label={`${title} observation`}
          type="range"
          min={0}
          max={points.length - 1}
          value={selected}
          onChange={(event) => setCursor(Number(event.target.value))}
        />
      </label>
    </figure>
  )
}
