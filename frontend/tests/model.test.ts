import { describe, expect, it } from 'vitest'
import {
  artifactSymbols,
  comparisonError,
  isResearchDocument,
  parseSymbols,
  readRoute,
  strategyError,
} from '../src/workspace/model'

describe('workspace input boundaries', () => {
  it('normalizes and deduplicates comparisons while preserving exchange-qualified symbols', () => {
    expect(parseSymbols(' aapl, MSFT AAPL, 1155.KL ^GSPC ')).toEqual([
      'AAPL',
      'MSFT',
      '1155.KL',
      '^GSPC',
    ])
    expect(comparisonError(parseSymbols('aapl AAPL'))).not.toBeNull()
    expect(comparisonError(['A', 'B', 'C', 'D', 'E', 'F'])).not.toBeNull()
    expect(comparisonError(['AAPL', '<script>'])).not.toBeNull()
    expect(comparisonError(['BRK-B', '^GSPC'])).toBeNull()
  })
  it.each([
    [1, 50, 10],
    [20, 20, 10],
    [50, 20, 10],
    [20, 201, 10],
    [2.5, 50, 10],
    [20, 50, -1],
    [20, 50, NaN],
    [20, 50, 1001],
  ])('rejects unsupported backtest parameters %s/%s at %s bps', (short, long, cost) => {
    expect(strategyError(short, long, cost)).not.toBeNull()
  })
  it('accepts the documented parameter boundaries', () => {
    expect(strategyError(2, 200, 0)).toBeNull()
    expect(strategyError(199, 200, 1000)).toBeNull()
  })
  it('handles malformed and unknown URLs without crashing', () => {
    expect(readRoute('#/companies/%E0%A4%A')).toEqual({ page: 'companies', target: '' })
    expect(readRoute('#/companies/%5EGSPC')).toEqual({ page: 'companies', target: '^GSPC' })
    expect(readRoute('#/companies/%3Cscript%3E').target).toBe('')
    expect(readRoute('#/unknown').page).toBe('overview')
  })
  it('rejects corrupted saved records before they reach the UI', () => {
    const valid = {
      id: 'report',
      kind: 'strategy',
      title: 'Backtest',
      symbols: ['AAPL'],
      threadId: '11111111-1111-4111-8111-111111111111',
      updatedAt: '2026-09-09T00:00:00Z',
    }
    expect(isResearchDocument(valid)).toBe(true)
    expect(isResearchDocument({ ...valid, settings: { shortWindow: '20' } })).toBe(false)
    expect(isResearchDocument({ ...valid, updatedAt: 'bad' })).toBe(false)
    expect(isResearchDocument({ ...valid, threadId: '../../etc' })).toBe(false)
    expect(isResearchDocument({ ...valid, symbols: [null] })).toBe(false)
  })
  it('extracts company identities from comparison evidence', () => {
    expect(
      artifactSymbols({
        artifact_type: 'company_comparison',
        schema_version: 1,
        status: 'ok',
        data: { records: [{ symbol: 'AAPL' }, null, { symbol: 'MSFT' }] },
      }),
    ).toEqual(['AAPL', 'MSFT'])
  })
})
