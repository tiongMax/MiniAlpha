import { Building2, ChartNoAxesCombined, Database, Sparkles } from 'lucide-react'
import { useId } from 'react'
import type { Artifact } from '../types'

type RecordValue = Record<string, unknown>

interface CompanyOverviewData extends RecordValue {
  symbol: string
  company_name?: string | null
  exchange?: string | null
  currency?: string | null
  sector?: string | null
  industry?: string | null
  price?: number | null
  market_cap?: number | null
  trailing_pe?: number | null
  forward_pe?: number | null
  price_to_book?: number | null
  total_revenue?: number | null
  revenue_growth?: number | null
  operating_margin?: number | null
  profit_margin?: number | null
  dividend_yield?: number | null
  provider?: string
  retrieved_at?: string
}

interface PricePointData extends RecordValue {
  timestamp: string
  close: number
  volume?: number | null
}

interface PriceHistoryData extends RecordValue {
  symbol: string
  currency?: string | null
  period: string
  interval: string
  points: PricePointData[]
  provider?: string
  retrieved_at?: string
}

interface CompanyComparisonData extends RecordValue {
  records: CompanyOverviewData[]
  provider?: string
  retrieved_at?: string
}

interface FundamentalDatasetData extends RecordValue {
  symbol: string
  dataset: string
  currency?: string | null
  records: RecordValue[]
  provider?: string
  retrieved_at?: string
  source_urls?: string[]
}

const FUNDAMENTAL_ARTIFACTS = new Set([
  'financial_statements',
  'fundamental_ratios',
  'analyst_estimates',
  'sec_filings',
  'ownership',
  'insider_activity',
  'company_news',
])

const compactNumber = new Intl.NumberFormat(undefined, {
  notation: 'compact',
  maximumFractionDigits: 2,
})

function isRecord(value: unknown): value is RecordValue {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function companyData(artifact: Artifact): CompanyOverviewData | null {
  const data = artifact.data
  return artifact.artifact_type === 'company_overview' && isCompanyOverview(data)
    ? data
    : null
}

function isCompanyOverview(value: unknown): value is CompanyOverviewData {
  return isRecord(value) && typeof value.symbol === 'string'
}

function comparisonData(artifact: Artifact): CompanyComparisonData | null {
  const data = artifact.data
  if (artifact.artifact_type !== 'company_comparison' || !isRecord(data) || !Array.isArray(data.records)) return null
  const records = data.records.filter(isCompanyOverview)
  return records.length >= 2 ? ({ ...data, records } as CompanyComparisonData) : null
}

function fundamentalData(artifact: Artifact): FundamentalDatasetData | null {
  const data = artifact.data
  if (
    !FUNDAMENTAL_ARTIFACTS.has(artifact.artifact_type) || !isRecord(data) ||
    typeof data.symbol !== 'string' || typeof data.dataset !== 'string' ||
    !Array.isArray(data.records)
  ) return null
  return {
    ...data,
    records: data.records.filter(isRecord),
  } as FundamentalDatasetData
}

function priceData(artifact: Artifact): PriceHistoryData | null {
  const data = artifact.data
  if (
    artifact.artifact_type !== 'price_history' || !isRecord(data) ||
    typeof data.symbol !== 'string' || typeof data.period !== 'string' ||
    typeof data.interval !== 'string' || !Array.isArray(data.points)
  ) return null
  const points = data.points.filter(
    (point): point is PricePointData =>
      isRecord(point) && typeof point.timestamp === 'string' && typeof point.close === 'number',
  )
  return { ...data, points } as PriceHistoryData
}

function money(value: number | null | undefined, currency?: string | null): string {
  if (typeof value !== 'number') return '—'
  return `${currency ? `${currency} ` : ''}${compactNumber.format(value)}`
}

function ratio(value: number | null | undefined): string {
  return typeof value === 'number' ? value.toFixed(2) : '—'
}

function percent(value: number | null | undefined, digits = 1): string {
  return typeof value === 'number' ? `${(value * 100).toFixed(digits)}%` : '—'
}

function sourceLine(data: { provider?: string; retrieved_at?: string }): string {
  const date = data.retrieved_at
    ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(data.retrieved_at))
    : null
  return [data.provider, date].filter(Boolean).join(' · ')
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="artifact-metric"><span>{label}</span><strong>{value}</strong></div>
}

function CompanyOverviewCard({ data }: { data: CompanyOverviewData }) {
  return (
    <section className="company-card">
      <header className="artifact-header">
        <span className="artifact-icon"><Building2 size={17} /></span>
        <div><strong>{data.company_name ?? data.symbol}</strong><span>{data.symbol}{data.exchange ? ` · ${data.exchange}` : ''}</span></div>
        <div className="company-price"><strong>{money(data.price, data.currency)}</strong><span>Latest price</span></div>
      </header>
      <div className="company-tags">
        {data.sector && <span>{data.sector}</span>}
        {data.industry && <span>{data.industry}</span>}
      </div>
      <div className="artifact-metrics">
        <Metric label="Market cap" value={money(data.market_cap, data.currency)} />
        <Metric label="Trailing P/E" value={ratio(data.trailing_pe)} />
        <Metric label="Forward P/E" value={ratio(data.forward_pe)} />
        <Metric label="Price / book" value={ratio(data.price_to_book)} />
        <Metric label="Revenue" value={money(data.total_revenue, data.currency)} />
        <Metric label="Revenue growth" value={percent(data.revenue_growth)} />
        <Metric label="Operating margin" value={percent(data.operating_margin)} />
        <Metric label="Profit margin" value={percent(data.profit_margin)} />
        <Metric label="Dividend yield" value={percent(data.dividend_yield, 2)} />
      </div>
      <footer className="artifact-source"><Database size={12} /> {sourceLine(data) || 'Source unavailable'}</footer>
    </section>
  )
}

function FallbackArtifact({ artifact }: { artifact: Artifact }) {
  return (
    <details className={`artifact-card ${artifact.status === 'error' ? 'artifact-error' : ''}`}>
      <summary><Sparkles size={14} /> {artifact.artifact_type.replaceAll('_', ' ')}</summary>
      <pre>{JSON.stringify(artifact.data ?? { error: artifact.error }, null, 2)}</pre>
    </details>
  )
}

function PriceChart({ data }: { data: PriceHistoryData }) {
  const gradientId = `price-area-${useId().replaceAll(':', '')}`
  if (data.points.length < 2) return <FallbackArtifact artifact={{ artifact_type: 'price_history', schema_version: 1, status: 'error', error: 'Not enough price observations to draw a chart.' }} />
  const width = 720
  const height = 230
  const padding = 18
  const closes = data.points.map((point) => point.close)
  const minimum = Math.min(...closes)
  const maximum = Math.max(...closes)
  const span = maximum - minimum || 1
  const coordinates = closes.map((close, index) => ({
    x: padding + index * ((width - padding * 2) / (closes.length - 1)),
    y: padding + (maximum - close) * ((height - padding * 2) / span),
  }))
  const path = coordinates.map((point, index) => `${index ? 'L' : 'M'}${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ')
  const area = `${path} L${coordinates.at(-1)!.x.toFixed(1)},${height - padding} L${padding},${height - padding} Z`
  const first = closes[0]
  const latest = closes.at(-1)!
  const change = first ? latest / first - 1 : null
  const positive = change === null || change >= 0

  return (
    <section className="price-card">
      <header className="artifact-header">
        <span className="artifact-icon"><ChartNoAxesCombined size={17} /></span>
        <div><strong>{data.symbol} price history</strong><span>{data.period} · {data.interval} · {data.points.length} observations</span></div>
        <div className={`price-change ${positive ? 'positive' : 'negative'}`}><strong>{money(latest, data.currency)}</strong><span>{change === null ? '—' : `${change >= 0 ? '+' : ''}${percent(change)}`}</span></div>
      </header>
      <div className="chart-wrap">
        <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${data.symbol} closing price chart`} preserveAspectRatio="none">
          <defs><linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#1b8a66" stopOpacity=".28" /><stop offset="1" stopColor="#1b8a66" stopOpacity=".02" /></linearGradient></defs>
          <line x1={padding} y1={padding} x2={width - padding} y2={padding} className="chart-grid" />
          <line x1={padding} y1={height - padding} x2={width - padding} y2={height - padding} className="chart-grid" />
          <path d={area} fill={`url(#${gradientId})`} />
          <path d={path} className="chart-line" />
        </svg>
        <div className="chart-axis"><span>{new Date(data.points[0].timestamp).toLocaleDateString()}</span><span>{new Date(data.points.at(-1)!.timestamp).toLocaleDateString()}</span></div>
      </div>
      <div className="chart-stats"><Metric label="Low close" value={money(minimum, data.currency)} /><Metric label="High close" value={money(maximum, data.currency)} /><Metric label="Period change" value={change === null ? '—' : percent(change)} /></div>
      <footer className="artifact-source"><Database size={12} /> {sourceLine(data) || 'Source unavailable'}</footer>
    </section>
  )
}

function ComparisonTable({ companies }: { companies: CompanyOverviewData[] }) {
  const rows: Array<[string, (company: CompanyOverviewData) => string]> = [
    ['Price', (company) => money(company.price, company.currency)],
    ['Market cap', (company) => money(company.market_cap, company.currency)],
    ['Trailing P/E', (company) => ratio(company.trailing_pe)],
    ['Forward P/E', (company) => ratio(company.forward_pe)],
    ['Revenue growth', (company) => percent(company.revenue_growth)],
    ['Operating margin', (company) => percent(company.operating_margin)],
    ['Profit margin', (company) => percent(company.profit_margin)],
  ]
  return (
    <section className="comparison-card">
      <header className="artifact-header"><span className="artifact-icon"><Sparkles size={17} /></span><div><strong>Company comparison</strong><span>Normalized provider metrics</span></div></header>
      <div className="comparison-scroll"><table><thead><tr><th>Metric</th>{companies.map((company, index) => <th key={`${company.symbol}-${index}`}>{company.symbol}</th>)}</tr></thead><tbody>{rows.map(([label, read]) => <tr key={label}><th>{label}</th>{companies.map((company, index) => <td key={`${company.symbol}-${index}`}>{read(company)}</td>)}</tr>)}</tbody></table></div>
    </section>
  )
}

function flattenRecord(record: RecordValue): RecordValue {
  const flattened: RecordValue = {}
  Object.entries(record).forEach(([key, value]) => {
    if (isRecord(value)) {
      Object.entries(value).forEach(([nestedKey, nestedValue]) => {
        flattened[`${key}.${nestedKey}`] = nestedValue
      })
    } else {
      flattened[key] = value
    }
  })
  return flattened
}

function evidenceValue(value: unknown, key: string, currency?: string | null) {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') {
    if (/(margin|growth|yield|return_on|percent|debt_to_equity)/.test(key)) return percent(value)
    return money(value, /(revenue|income|cash|debt|assets|liabilities|equity|value|target|expenditure|repurchase|dividend)/.test(key) ? currency : null)
  }
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  return String(value)
}

function FundamentalTable({ data }: { data: FundamentalDatasetData }) {
  const records = data.records.map(flattenRecord)
  const columns = Array.from(new Set(records.flatMap((record) => Object.keys(record))))
  return (
    <section className="comparison-card">
      <header className="artifact-header">
        <span className="artifact-icon"><Database size={17} /></span>
        <div><strong>{data.dataset.replaceAll('_', ' ')}</strong><span>{data.symbol} · {records.length} records</span></div>
      </header>
      <div className="comparison-scroll"><table><thead><tr>{columns.map((column) => <th key={column}>{column.replaceAll('_', ' ').replaceAll('.', ' · ')}</th>)}</tr></thead><tbody>{records.map((record, index) => <tr key={index}>{columns.map((column) => {
        const value = record[column]
        const rendered = evidenceValue(value, column, data.currency)
        return <td key={column}>{column.endsWith('url') && typeof value === 'string' ? <a href={value} target="_blank" rel="noreferrer">Source</a> : rendered}</td>
      })}</tr>)}</tbody></table></div>
      <footer className="artifact-source"><Database size={12} /> {sourceLine(data) || 'Source unavailable'}</footer>
    </section>
  )
}

function ArtifactView({ artifact }: { artifact: Artifact }) {
  if (artifact.status === 'error') return <FallbackArtifact artifact={artifact} />
  const company = companyData(artifact)
  if (company) return <CompanyOverviewCard data={company} />
  const prices = priceData(artifact)
  if (prices) return <PriceChart data={prices} />
  const comparison = comparisonData(artifact)
  if (comparison) return <ComparisonTable companies={comparison.records} />
  const fundamentals = fundamentalData(artifact)
  if (fundamentals) return <FundamentalTable data={fundamentals} />
  return <FallbackArtifact artifact={artifact} />
}

export function ArtifactStack({ artifacts }: { artifacts: Artifact[] }) {
  const companies = artifacts.map(companyData).filter((company): company is CompanyOverviewData => company !== null)
  return (
    <div className="artifact-stack">
      {companies.length > 1 && <ComparisonTable companies={companies} />}
      {artifacts.map((artifact, index) => <ArtifactView key={`${artifact.artifact_type}-${index}`} artifact={artifact} />)}
    </div>
  )
}
