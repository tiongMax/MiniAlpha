# Grounding, provenance, and claim verification

MiniAlpha distinguishes three evidence-bearing claim classes: retrieved facts,
deterministic calculations, and comparisons. Interpretations may cite those
artifacts but are not promoted to facts. Limitations describe unavailable or
insufficient evidence and do not require a citation.

## Artifact contract

New tool artifacts retain the existing envelope and add two optional fields:

- `artifact_id`: an application-owned UUID generated once at tool completion
- `provenance`: provider metadata copied from structured tool data, including
  source URLs, retrieval time, reporting/market period, currency, entities,
  parent artifact IDs, calculation name/version, and verification/freshness
  status

Unknown metadata is `null`, an empty list, or `unknown`; it is never inferred.
Legacy artifacts without either field remain valid. The same ID is persisted
and returned on transcript replay. Context and memory code should reference
`artifact_id` and treat absent provenance as legacy/unverified evidence.

## Claim and citation contract

An important claim has a type, text, cited artifact IDs, optional entity and
period, and an optional structured numeric value. Numeric citations name an
exact dotted data path and explicitly state whether the artifact value is a
number, decimal fraction, or displayed percentage, plus its scale.

The deterministic verifier checks:

- artifact existence and authorization in the caller-provided run/thread scope
- successful evidence and exact nested value paths
- decimal/percentage and thousand/million/billion/trillion normalization
- entity, reporting-period, currency, and unit alignment
- deterministic calculation provenance for calculated claims
- explicit stale-evidence warnings

Interpretive support is intentionally a protocol. An optional semantic verifier
must receive only one claim and its already-authorized cited artifacts and return
structured issues. Credential-free execution does not call a model judge.

## Severity and delivery

Fabricated/unauthorized citations, unavailable evidence, entity/period/currency
mismatches, unsupported numbers, and calculations without calculation artifacts
are blocking. Staleness is currently a visible warning because freshness limits
vary by artifact type and are not yet defined.

Repair is capped at one or two targeted attempts. Within budget, only failing
claims are revised. After the budget, unsupported claims are softened or removed;
fabricated/unauthorized/unavailable evidence produces an explicit limitation.
There is no verifier loop in this module.

## Current integration boundary

This slice establishes the durable evidence and verification interfaces. The
agent synthesis graph does not yet emit structured claims or invoke verification
before delivery. Orchestration can pass current-run artifact IDs as the verifier's
authorization set; context/memory can consume the persisted provenance view.
