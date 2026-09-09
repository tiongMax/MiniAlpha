import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, it, vi } from 'vitest'
import App from '../src/App'
import type { Artifact, ThreadTurn } from '../src/types'

const THREAD = '11111111-1111-4111-8111-111111111111'
const RUN = '22222222-2222-4222-8222-222222222222'
const DATE = '2026-09-09T00:00:00Z'
let requests: string[]
let artifacts: Artifact[]
let storedTurns: ThreadTurn[]
let failure: boolean

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

beforeEach(() => {
  requests = []
  artifacts = [
    {
      artifact_type: 'company_comparison',
      schema_version: 1,
      status: 'ok',
      data: {
        records: [
          { symbol: 'AAPL', price: 100 },
          { symbol: 'MSFT', price: 200 },
        ],
      },
    },
  ]
  storedTurns = []
  failure = false
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: string, init?: RequestInit) => {
      if (input.includes('/messages')) return json({ thread_id: THREAD, turns: storedTurns })
      if (input.includes('/events')) {
        storedTurns = [
          {
            run_id: RUN,
            turn_index: 1,
            attempt_no: 1,
            status: 'completed',
            message: requests.at(-1) ?? '',
            answer: 'Fixture research completed.',
            tool_calls: [],
            artifacts,
            error: null,
            started_at: DATE,
            completed_at: DATE,
          },
        ]
        const events = [
          { event: 'metadata', data: { turn_index: 1 } },
          ...artifacts.map((artifact) => ({ event: 'artifact', data: artifact })),
          { event: 'message_chunk', data: { delta: 'Fixture research completed.' } },
          { event: 'run_end', data: { status: 'completed' } },
        ]
          .map(
            (event, index) =>
              `data: ${JSON.stringify({ ...event, event_id: index + 1, thread_id: THREAD, run_id: RUN, timestamp: DATE })}\n\n`,
          )
          .join('')
        return new Response(events, { headers: { 'Content-Type': 'text/event-stream' } })
      }
      if (init?.method === 'POST' && input.endsWith('/runs')) {
        requests.push(JSON.parse(init.body as string).messages[0].content)
        if (failure)
          return json(
            { error: { code: 'unavailable', message: 'Research service unavailable.' } },
            503,
          )
        return json({
          thread_id: THREAD,
          run_id: RUN,
          turn_index: 1,
          status: 'in_progress',
          replayed: false,
          events_url: `/api/v1/runs/${RUN}/events`,
        })
      }
      return json({ threads: [], total: 0, limit: 100, offset: 0 })
    }),
  )
})

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  )
}

async function route(hash: string) {
  await act(async () => {
    window.history.replaceState(null, '', hash)
    window.dispatchEvent(new HashChangeEvent('hashchange'))
  })
}

it('persists watchlist and journal data across a remount', async () => {
  const user = userEvent.setup()
  const view = mount()
  await user.type(screen.getByLabelText('Add ticker to watchlist'), 'AAPL')
  await user.click(screen.getByRole('button', { name: 'Add', exact: true }))
  await route('#/journal/AAPL')
  await user.type(screen.getByLabelText('Your thesis'), 'Verify margins in the next filing.')
  await user.click(screen.getByRole('button', { name: 'Save note' }))
  expect(await screen.findByText('Note saved.')).toBeTruthy()
  view.unmount()
  mount()
  expect(await screen.findByText('Verify margins in the next filing.')).toBeTruthy()
  expect(
    JSON.parse(localStorage.getItem('minialpha.personal-research.v1')!).watchlist[0].symbol,
  ).toBe('AAPL')
})

it('validates comparisons, renders streamed evidence, and reloads a saved report', async () => {
  const user = userEvent.setup()
  const view = mount()
  await user.click(screen.getByRole('link', { name: 'Compare', exact: true }))
  await screen.findByRole('heading', { name: 'Put your options side by side.' })
  await user.type(screen.getByLabelText('Company tickers'), 'AAPL, aapl')
  await user.click(screen.getByRole('button', { name: 'Run comparison' }))
  expect(screen.getByRole('alert').textContent).toContain('distinct tickers')
  expect(requests).toHaveLength(0)
  await user.clear(screen.getByLabelText('Company tickers'))
  await user.type(screen.getByLabelText('Company tickers'), 'AAPL, MSFT')
  await user.click(screen.getByRole('button', { name: 'Run comparison' }))
  expect(await screen.findByText('Fixture research completed.')).toBeTruthy()
  expect(screen.getByRole('table')).toBeTruthy()
  expect(requests[0]).toContain('Compare AAPL, MSFT')
  await waitFor(() =>
    expect(JSON.parse(localStorage.getItem('minialpha.workspace.v1')!)[0].threadId).toBe(THREAD),
  )
  view.unmount()
  mount()
  expect(await screen.findByText('Fixture research completed.')).toBeTruthy()
  expect(screen.getByRole('table')).toBeTruthy()
})

it('retains exact backtest settings and displays strategy and benchmark charts', async () => {
  artifacts = [
    {
      artifact_type: 'moving_average_backtest',
      schema_version: 1,
      status: 'ok',
      data: {
        analysis: 'moving_average_backtest',
        symbols: ['AAPL'],
        period: '5y',
        interval: '1d',
        parameters: { short_window: 10, long_window: 100, transaction_cost_bps: 25 },
        summary: { strategy_total_return: 0.1 },
        series: [
          {
            timestamp: '2026-01-01',
            strategy_equity: 1,
            benchmark_equity: 1,
            strategy_drawdown: 0,
          },
          {
            timestamp: '2026-01-02',
            strategy_equity: 1.1,
            benchmark_equity: 1.2,
            strategy_drawdown: 0,
          },
        ],
      },
    },
  ]
  await route('#/strategy')
  const user = userEvent.setup()
  mount()
  await user.type(screen.getByLabelText('Ticker', { exact: true }), 'AAPL')
  fireEvent.change(screen.getByLabelText('Short window (days)'), { target: { value: '10' } })
  fireEvent.change(screen.getByLabelText('Long window (days)'), { target: { value: '100' } })
  fireEvent.change(screen.getByLabelText('Cost per trade (bps)'), { target: { value: '25' } })
  await user.selectOptions(screen.getByLabelText('Historical period'), '5y')
  await user.click(screen.getByRole('button', { name: 'Run backtest' }))
  expect(await screen.findByText('Fixture research completed.')).toBeTruthy()
  expect(requests[0]).toContain('short_window=10, long_window=100, transaction_cost_bps=25')
  expect((screen.getByLabelText('Short window (days)') as HTMLInputElement).value).toBe('10')
  expect((screen.getByLabelText('Historical period') as HTMLSelectElement).value).toBe('5y')
  expect(screen.getByRole('slider', { name: 'Strategy drawdown observation' })).toBeTruthy()
  expect(screen.getByRole('img', { name: /Growth of 1/ })).toBeTruthy()
})

it('shows admission failures without saving a broken report', async () => {
  failure = true
  const user = userEvent.setup()
  mount()
  await user.click(screen.getByRole('link', { name: 'Compare', exact: true }))
  await screen.findByRole('heading', { name: 'Put your options side by side.' })
  await user.type(screen.getByLabelText('Company tickers'), 'AAPL MSFT')
  await user.click(screen.getByRole('button', { name: 'Run comparison' }))
  await waitFor(() =>
    expect(screen.getByRole('alert').textContent).toContain('Research service unavailable.'),
  )
  expect(JSON.parse(localStorage.getItem('minialpha.workspace.v1')!)).toEqual([])
  expect(
    (screen.getByRole('button', { name: 'Run comparison' }) as HTMLButtonElement).disabled,
  ).toBe(false)
})

it('ignores malformed persisted records and warns when storage is blocked', async () => {
  localStorage.setItem(
    'minialpha.personal-research.v1',
    JSON.stringify({ watchlist: [null, { symbol: 4 }], journal: [null] }),
  )
  localStorage.setItem('minialpha.workspace.v1', '[null, {"kind":"strategy"}]')
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new DOMException('Quota exceeded', 'QuotaExceededError')
  })
  mount()
  expect(screen.getByText('Start with a company you follow.')).toBeTruthy()
  expect(await screen.findByRole('status')).toBeTruthy()
})
