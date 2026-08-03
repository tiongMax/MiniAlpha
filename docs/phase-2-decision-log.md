# Phase 2 architecture decision log

- Status: Accepted
- Date: 2026-08-03
- Phase: Real company-overview research
- Baseline: Phase 1 custom LangGraph model-tool loop

## Context

Phase 1 proved the smallest useful agent loop:

```text
START -> model -> tools -> model -> END
```

Its company tool used a deterministic dictionary containing only AAPL and
MSFT. Phase 2 needed to replace that sample data with real financial data
without hiding the agent loop behind `langchain.agents.create_agent()` or
prematurely introducing LangAlpha's production infrastructure.

The resulting request path is:

```text
Gemini
  -> LangGraph ToolNode
  -> get_company_overview tool
  -> CompanyResearchService
  -> FinancialDataProvider protocol
  -> YahooFinanceProvider
  -> yfinance / Yahoo Finance
```

The phase is intentionally limited to one research capability. It does not
include an API, database, Redis, workspace, sandbox, MCP, subagents, caching,
fallback providers, or a frontend.

## Goals

- Preserve the hand-built LangGraph loop so its mechanics remain visible.
- Replace sample values with real provider data.
- Prevent Yahoo-specific response shapes from leaking through the codebase.
- Keep agent-facing tools small and safe.
- Make provider behavior replaceable and credential-free in tests.
- Preserve missing data instead of inventing values.
- Separate verified tool facts from model interpretation.
- Leave clear extension points for later research capabilities.

## Non-goals

- Exposing every field or product available from Yahoo Finance.
- Building a universal security model for stocks, ETFs, indices, and crypto.
- Guaranteeing real-time, complete, or authoritative market data.
- Adding persistence, retries, caching, provider fallback, or rate limiting.
- Reproducing LangAlpha's PTC, sandbox, MCP, or multi-agent architecture.

## Decisions

### P2-001: Preserve the explicit LangGraph topology

**Decision**

Keep the Phase 1 graph topology unchanged. The graph continues to contain a
model node, a `ToolNode`, conditional routing, and the tool-to-model loop.

**Rationale**

Phase 2 is a data-boundary change, not an agent-control-flow change. Keeping
the topology stable makes it possible to learn which responsibility belongs
to the graph and which belongs to the research stack. A provider can change
without requiring new graph nodes or routing rules.

**Alternatives considered**

- Replace the graph with `langchain.agents.create_agent()`.
- Put Yahoo retrieval directly inside a new graph node.
- Add one graph node for every provider or financial field.

**Consequences**

- The model-tool loop remains easy to inspect and test.
- New tools can be added without redesigning the graph.
- Features such as persistence and approval nodes remain deferred.

Implementation: [`app/agent/graph.py`](../app/agent/graph.py)

### P2-002: Separate tool, service, provider contract, and provider adapter

**Decision**

Use four boundaries:

```text
agent tool -> application service -> provider protocol -> Yahoo adapter
```

**Rationale**

Each boundary has one reason to change:

- The tool changes when the agent contract changes.
- The service changes when application rules or orchestration change.
- The protocol changes when MiniAlpha's provider requirements change.
- The Yahoo adapter changes when Yahoo or yfinance changes.

This follows LangAlpha's provider-separation principle while remaining much
smaller than LangAlpha's complete data-client implementation.

**Alternatives considered**

- Call `yfinance.Ticker` directly inside the tool.
- Create one large financial-research class containing formatting, validation,
  networking, and agent integration.
- Copy LangAlpha's complete data-client layer immediately.

**Consequences**

- Provider code can be replaced without changing the agent tool.
- Tests inject a fake provider or service without network calls.
- The phase contains more files than a direct yfinance tool, but the ownership
  of each behavior is explicit.

Implementation:
[`app/agent/tools.py`](../app/agent/tools.py),
[`app/services/company_research.py`](../app/services/company_research.py),
[`app/providers/base.py`](../app/providers/base.py), and
[`app/providers/yahoo.py`](../app/providers/yahoo.py)

### P2-003: Define a narrow provider protocol

**Decision**

`FinancialDataProvider` initially exposes only:

```python
async def get_company_overview(symbol: str) -> CompanyOverview
```

**Rationale**

The abstraction describes what MiniAlpha currently needs, not everything a
financial provider might offer. A narrow protocol is easier to understand,
mock, and evolve. Future capabilities should add focused methods and domain
models only when their requirements are known.

**Alternatives considered**

- A generic `request(endpoint, params)` provider interface.
- A large provider interface containing prices, statements, options, news,
  estimates, and filings before those features exist.
- Depending on the concrete Yahoo class throughout the application.

**Consequences**

- The contract is small and provider-neutral.
- Future methods may require deliberate interface evolution.
- Provider-specific escape hatches are not available to tools.

Implementation: [`app/providers/base.py`](../app/providers/base.py)

### P2-004: Normalize provider responses into an immutable domain model

**Decision**

Map Yahoo responses into the frozen, slotted `CompanyOverview` dataclass before
returning data to the service or tool.

**Rationale**

Yahoo field names such as `trailingPE` and `operatingMargins` are transport
details. MiniAlpha needs stable names, explicit optionality, documented units,
and a consistent representation that does not change with provider response
formatting.

The model stores raw numeric values:

- Monetary values use base currency units.
- Growth, margins, and yield use decimal fractions.
- Missing values remain `None`.
- Retrieval time is timezone-aware UTC.

Freezing prevents accidental mutation after normalization. Slots keep the
model compact and make its declared fields authoritative.

**Alternatives considered**

- Pass Yahoo dictionaries through every layer.
- Store preformatted values such as `"$4.8T"` and `"15.2%"`.
- Use a large validation framework for one internal model.

**Consequences**

- Formatting and calculations can reuse raw values.
- Provider migrations have a stable target contract.
- Adding a field requires an explicit domain and mapping change.
- The dataclass does not yet perform runtime schema validation.

Implementation: [`app/domain/company.py`](../app/domain/company.py)

### P2-005: Use Yahoo Finance through yfinance as the first provider

**Decision**

Use yfinance as the concrete Phase 2 adapter.

**Rationale**

It offers broad public-symbol coverage without introducing another API key,
making it suitable for a learning phase. It also exposes enough profile,
valuation, growth, profitability, and balance-sheet fields to exercise the
complete architecture.

Yahoo is treated as replaceable upstream data, not as MiniAlpha's domain
contract or source of guaranteed truth.

**Alternatives considered**

- Keep deterministic sample data.
- Start with a paid market-data API.
- Scrape provider web pages directly.
- Add multiple providers and fallback logic in the same phase.

**Consequences**

- Live behavior can vary with upstream availability and schema changes.
- Data may be delayed, incomplete, estimated, or missing.
- yfinance may emit upstream warnings for unknown or delisted symbols.
- A later provider can implement the same protocol.

Implementation: [`app/providers/yahoo.py`](../app/providers/yahoo.py)

### P2-006: Isolate synchronous provider work behind an async boundary

**Decision**

Run the blocking yfinance lookup with `asyncio.to_thread()` and bound the await
with `asyncio.wait_for()`.

**Rationale**

MiniAlpha and LangGraph are async-first, while yfinance performs synchronous
network work. Calling it directly inside an async tool would block the event
loop. The adapter is the correct place to reconcile those execution models.

**Alternatives considered**

- Call yfinance synchronously inside the tool.
- Make the whole graph synchronous.
- Reimplement Yahoo requests with a separate asynchronous HTTP client.

**Consequences**

- Other graph work is not blocked by the synchronous provider call.
- Timeouts become controlled domain errors.
- Timing out the await cannot forcibly stop an already-running worker thread.
- Production-scale concurrency limits remain a future concern.

Implementation: [`app/providers/yahoo.py`](../app/providers/yahoo.py)

### P2-007: Centralize symbol normalization in the service

**Decision**

Trim whitespace, uppercase symbols, and validate an intentionally small set of
Yahoo-compatible characters before provider delegation.

**Rationale**

The model and CLI should not need to know provider input rules. Centralizing
normalization makes direct service calls and agent tool calls behave the same.
It also avoids unnecessary network requests for clearly invalid input.

Supported examples include `AAPL`, `BRK-B`, and `0700.HK`.

**Alternatives considered**

- Normalize independently in every tool.
- Let Yahoo handle all malformed input.
- Restrict symbols to US letters only.

**Consequences**

- Lowercase and whitespace are handled consistently.
- Clearly malformed tickers fail quickly with an actionable message.
- Some unusual but valid provider symbols may require the validation rule to
  evolve.

Implementation:
[`app/services/company_research.py`](../app/services/company_research.py)

### P2-008: Translate expected failures into domain errors

**Decision**

Use a small error hierarchy:

```text
FinancialDataError
  -> InvalidSymbolError
  -> SymbolNotFoundError
  -> FinancialProviderError
       -> FinancialProviderTimeout
```

**Rationale**

The rest of MiniAlpha should respond to meaningful failure categories rather
than yfinance, HTTP, or parsing exceptions. Expected errors are safe to show to
the model. Unexpected upstream details are retained as exception causes for
debugging but are not exposed in the agent response.

**Alternatives considered**

- Catch every exception in the CLI.
- Return `None` for all failures.
- Expose raw yfinance exceptions to Gemini and users.

**Consequences**

- The tool can recover from expected data failures and let the model explain
  them.
- Programmer errors outside the financial-data hierarchy still fail visibly.
- Logging and richer retry metadata remain future work.

Implementation: [`app/domain/errors.py`](../app/domain/errors.py)

### P2-009: Construct tools with dependency injection

**Decision**

Create the company tool with `create_company_overview_tool(service)` and allow
`build_graph()` to accept an optional tool sequence.

**Rationale**

Tool decorators often encourage module-level dependencies. A tool factory
makes the service dependency explicit and allows tests to supply deterministic
implementations. Graph construction owns composition, while graph execution
uses already-built dependencies.

**Alternatives considered**

- Store a mutable provider in module-level global state.
- Instantiate yfinance inside every tool call.
- Monkeypatch the production provider in graph tests.

**Consequences**

- Tests run without Yahoo or credentials.
- Alternate providers can be composed without editing the tool.
- Default composition is still convenient for the CLI.
- A formal dependency-injection framework is unnecessary at this scale.

Implementation:
[`app/agent/tools.py`](../app/agent/tools.py) and
[`app/agent/graph.py`](../app/agent/graph.py)

### P2-010: Return model-readable content and a structured artifact

**Decision**

Configure the tool with `response_format="content_and_artifact"`.

Successful calls return:

```text
content: compact formatted financial text
artifact:
  artifact_type: company_overview
  schema_version: 1
  status: ok
  data: normalized raw values and source metadata
```

Expected failures return readable content plus a versioned error artifact.

**Rationale**

Gemini benefits from compact text, but future APIs, frontends, calculations,
and persistence should not parse prose to recover numbers. The artifact is the
machine-facing contract; content is the model-facing representation.

**Alternatives considered**

- Return only prose.
- Return only JSON and ask Gemini to format it.
- Add artifact persistence before a persistence layer exists.

**Consequences**

- The same tool call supports conversation and future UI/data consumers.
- Artifact schemas can evolve using `schema_version`.
- Artifacts currently exist only in graph messages and are not persisted by
  MiniAlpha.

Implementation: [`app/agent/tools.py`](../app/agent/tools.py)

### P2-011: Keep formatting deterministic and separate from retrieval

**Decision**

Format normalized values in dedicated Python helpers after retrieval.

**Rationale**

Providers should map data, not decide how Gemini sees it. Deterministic
formatting ensures consistent currency abbreviations, ratios, percentages,
missing values, source labels, and retrieval timestamps.

Important rules:

- `None` becomes `N/A`; zero remains zero.
- Monetary values retain raw units in artifacts and are abbreviated only in
  content.
- General rates use one decimal place.
- Dividend yield uses two decimal places so a small yield is not displayed as
  `0.0%`.
- Yahoo's percentage-point dividend field is normalized to a decimal fraction
  at the provider boundary.

The dividend decision came from a live-provider check: current Yahoo data used
`0.35` to represent 0.35%, while growth and margin fields used decimal
fractions. Unit normalization therefore belongs in the adapter, not the
formatter or prompt.

**Alternatives considered**

- Let Gemini format raw provider dictionaries.
- Store formatted strings in the domain model.
- Treat missing values as zero.
- Apply one percentage conversion rule to every Yahoo field.

**Consequences**

- Output is predictable and testable.
- Raw artifacts remain calculation-friendly.
- Each provider field's unit semantics must be verified explicitly.

Implementation:
[`app/providers/yahoo.py`](../app/providers/yahoo.py) and
[`app/agent/tools.py`](../app/agent/tools.py)

### P2-012: Treat tool output as evidence and model prose as interpretation

**Decision**

Require final answers to separate:

1. Verified facts
2. Interpretation
3. Data limitations

Only claims present in tool results may be labeled verified. Strategy,
competitive position, business drivers, management intent, analyst
expectations, catalysts, and risks cannot be introduced unless a tool supplied
that information.

**Rationale**

The first live NVIDIA result showed that a model can add plausible but
unverified claims from pretrained knowledge. Those claims are not necessarily
false, but the current tool cannot verify them. The prompt must distinguish
evidence, inference, and unavailable context.

**Alternatives considered**

- Allow the model to freely combine tool data with pretrained knowledge.
- Remove interpretation entirely.
- Treat every plausible inference as a hallucination.

**Consequences**

- Users can see which claims were retrieved in the current run.
- Simple arithmetic and cautious interpretation remain possible.
- Rich qualitative research requires future filings, news, estimates, and
  business-description tools.
- Prompting reduces unsupported claims but is not a formal proof system.

Implementation: [`app/agent/prompts.py`](../app/agent/prompts.py)

### P2-013: Keep the system prompt transient

**Decision**

Prepend the system prompt for each model invocation but do not append it to
`ResearchState.messages`.

**Rationale**

The prompt is execution configuration, not conversation history. Persisting it
after every loop would duplicate instructions, grow state unnecessarily, and
mix application policy with user/model messages.

**Alternatives considered**

- Store the system prompt in graph state.
- Append a new system message after every tool call.

**Consequences**

- State remains a clean user/assistant/tool conversation.
- Every model call still receives the current application policy.
- Changing the prompt affects future invocations without rewriting state.

Implementation: [`app/agent/graph.py`](../app/agent/graph.py)

### P2-014: Expose graph transitions and artifact status in the CLI

**Decision**

Print model tool requests, tool content, artifact type/status, and final model
answers as separate trace events.

**Rationale**

MiniAlpha is a learning project. Seeing each transition makes routing,
tool execution, and model interpretation observable. The CLI summarizes the
artifact rather than dumping its complete payload so normal use remains
readable.

**Alternatives considered**

- Print only the final answer.
- Dump complete LangChain messages and provider metadata.
- Build a web observability interface in this phase.

**Consequences**

- Users can confirm whether Gemini actually used the tool.
- Structured artifacts remain available programmatically.
- Production logging and tracing remain deferred.

Implementation: [`cli.py`](../cli.py)

### P2-015: Verify live behavior before freezing contracts in tests

**Decision**

Exercise Yahoo directly first, then run a real Gemini-to-tool-to-Gemini
request, and only afterward add credential-free tests using fakes.

**Rationale**

Provider mocks cannot reveal current upstream behavior. Live checks discovered
the dividend-yield unit mismatch that initially produced an incorrect 35%
display. Once field semantics and graph behavior were proven, tests were added
to lock the settled contract.

The automated suite covers:

- Symbol normalization and rejection.
- Provider mapping, missing values, and zero preservation.
- Upstream error translation.
- Tool content and structured artifacts.
- Explicit graph routing and full tool round-trip.
- Gemini content-block formatting.

**Alternatives considered**

- Write mocks first and assume they represent Yahoo accurately.
- Make every automated test call live Yahoo or Gemini.
- Skip automated tests after a successful manual run.

**Consequences**

- Default tests are fast, deterministic, and credential-free.
- Live smoke checks remain available separately.
- Upstream behavior can still change after tests are written, so periodic live
  verification remains necessary.

Implementation:
[`scripts/smoke_company.py`](../scripts/smoke_company.py) and
[`tests/`](../tests/)

### P2-016: Prefer precise types at dynamic boundaries

**Decision**

Avoid `typing.Any` where possible. Use:

- `dict[str, object]` for artifacts.
- `Mapping[str, object]` for normalized provider dictionaries.
- `object` plus explicit narrowing for dynamic message/provider content.
- A small protocol and internal cast for yfinance's `FastInfo`.
- `BaseMessage` for CLI messages.
- `BaseCheckpointSaver` for graph persistence configuration.

**Rationale**

`Any` silently disables type checking beyond the point where it is introduced.
Yahoo and provider message boundaries are genuinely dynamic, but representing
them as `object` forces the code to check or narrow values before use.

The yfinance `FastInfo` stub does not structurally match a strict mapping
protocol, so `_value` accepts `object` and performs the known boundary cast
internally. This confines uncertainty to one helper instead of spreading
`Any` through the application.

**Alternatives considered**

- Annotate all provider and message values as `Any`.
- Ignore Pylance incompatibilities.
- Reproduce yfinance's complete internal type hierarchy locally.

**Consequences**

- Static analysis catches more incorrect assumptions.
- Dynamic boundaries require explicit checks and small casts.
- `AnyMessage` remains in graph state because it is LangChain's named union of
  message types, not `typing.Any`.

Implementation:
[`app/providers/yahoo.py`](../app/providers/yahoo.py),
[`app/domain/company.py`](../app/domain/company.py),
[`app/agent/graph.py`](../app/agent/graph.py), and [`cli.py`](../cli.py)

### P2-017: Document contracts next to the implementation

**Decision**

Use Google-style docstrings for every class and function, including parameters,
returns, raised exceptions, class attributes, and domain-field units.

**Rationale**

MiniAlpha exists for learning. Architectural boundaries are easier to
understand when their contracts are visible beside the code. Field
documentation is especially important for financial values whose units can
otherwise be ambiguous.

**Alternatives considered**

- Rely only on type hints and function names.
- Put all API documentation in a separate document.
- Document public functions but leave provider helpers unexplained.

**Consequences**

- Editors expose useful documentation during development.
- Documentation must be updated with behavior and type changes.
- This decision log explains why; docstrings explain each local contract.

## Intentionally deferred decisions

The following should be decided in later phases when their concrete
requirements are known:

- Historical-price model and interval semantics.
- Financial-statement periods, restatements, and units.
- Filing and news provenance.
- Calculated metrics and formula versioning.
- Caching, freshness rules, rate limiting, retries, and fallback providers.
- Artifact persistence and database schema.
- Thread checkpoints and conversation history.
- FastAPI contracts and streaming event schemas.
- Frontend presentation.
- Workspace, sandbox, MCP, PTC, and subagent boundaries.

## Resulting extension pattern

New research capabilities should normally follow:

```text
1. Define a provider-neutral domain model.
2. Add the narrow provider protocol method.
3. Implement and live-check the Yahoo mapping.
4. Add service-level validation or orchestration.
5. Create an injected agent tool.
6. Return concise content plus a versioned artifact.
7. Compose the tool into the existing graph.
8. Add deterministic tests after the live contract is understood.
```

Examples of future capabilities:

```text
get_price_history
get_financial_statements
get_earnings_and_estimates
get_company_news
```

This pattern is a default, not a rule that every feature must copy blindly.
If a future capability has different lifecycle, streaming, storage, or
calculation requirements, its decision should be recorded separately.
