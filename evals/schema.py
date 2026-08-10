"""Versioned contracts used by the MiniAlpha evaluation framework."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

Difficulty = Literal["easy", "medium", "hard"]


class EvaluationSchemaError(ValueError):
    """Raised when a case or trial does not satisfy the evaluation contract."""


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EvaluationSchemaError(f"{field_name} must be a list of strings")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class ToolArgumentExpectation:
    """Expected normalized arguments for one tool call."""

    tool: str
    arguments: dict[str, object]

    @classmethod
    def from_dict(cls, data: object) -> ToolArgumentExpectation:
        if not isinstance(data, dict):
            raise EvaluationSchemaError("tool argument expectation must be an object")
        tool = data.get("tool")
        arguments = data.get("arguments")
        if not isinstance(tool, str) or not tool:
            raise EvaluationSchemaError("tool argument expectation requires tool")
        if not isinstance(arguments, dict):
            raise EvaluationSchemaError("tool argument expectation requires arguments")
        return cls(tool=tool, arguments=arguments)


@dataclass(frozen=True, slots=True)
class NumericExpectation:
    """Expected structured number located in a trial artifact."""

    artifact_type: str
    path: str
    expected: float
    unit: str = "number"
    absolute_tolerance: float = 0.0
    relative_tolerance: float = 0.0

    @classmethod
    def from_dict(cls, data: object) -> NumericExpectation:
        if not isinstance(data, dict):
            raise EvaluationSchemaError("numerical expectation must be an object")
        required = ("artifact_type", "path", "expected")
        if any(name not in data for name in required):
            raise EvaluationSchemaError(
                "numerical expectation requires artifact_type, path, and expected"
            )
        try:
            expected = float(data["expected"])
            absolute = float(data.get("absolute_tolerance", 0.0))
            relative = float(data.get("relative_tolerance", 0.0))
        except (TypeError, ValueError) as error:
            raise EvaluationSchemaError("numerical values must be numbers") from error
        if absolute < 0 or relative < 0:
            raise EvaluationSchemaError("numerical tolerances cannot be negative")
        return cls(
            artifact_type=str(data["artifact_type"]),
            path=str(data["path"]),
            expected=expected,
            unit=str(data.get("unit", "number")),
            absolute_tolerance=absolute,
            relative_tolerance=relative,
        )


@dataclass(frozen=True, slots=True)
class GraderConfiguration:
    """Per-case deterministic grader switches."""

    require_all_answer_elements: bool = True
    argument_subset_match: bool = True
    forbid_duplicate_calls: bool = True

    @classmethod
    def from_dict(cls, data: object) -> GraderConfiguration:
        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise EvaluationSchemaError("grader must be an object")
        return cls(
            require_all_answer_elements=bool(
                data.get("require_all_answer_elements", True)
            ),
            argument_subset_match=bool(data.get("argument_subset_match", True)),
            forbid_duplicate_calls=bool(data.get("forbid_duplicate_calls", True)),
        )


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """One versioned, provider-independent financial-agent task."""

    schema_version: int
    case_id: str
    category: str
    question: str
    difficulty: Difficulty
    expected_symbols: tuple[str, ...]
    expected_entities: tuple[str, ...]
    required_tools: tuple[str, ...]
    optional_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    expected_tool_arguments: tuple[ToolArgumentExpectation, ...]
    required_answer_elements: tuple[str, ...]
    numerical_expectations: tuple[NumericExpectation, ...]
    fixture: str
    grader: GraderConfiguration
    turns: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: object) -> EvaluationCase:
        if not isinstance(data, dict):
            raise EvaluationSchemaError("evaluation case must be an object")
        required = ("schema_version", "case_id", "category", "question", "difficulty")
        missing = [name for name in required if name not in data]
        if missing:
            raise EvaluationSchemaError(f"case is missing: {', '.join(missing)}")
        version = data["schema_version"]
        if version != 1:
            raise EvaluationSchemaError(f"unsupported case schema version: {version}")
        difficulty = data["difficulty"]
        if difficulty not in {"easy", "medium", "hard"}:
            raise EvaluationSchemaError(f"invalid difficulty: {difficulty}")
        case_id = data["case_id"]
        question = data["question"]
        category = data["category"]
        if not all(
            isinstance(value, str) and value for value in (case_id, question, category)
        ):
            raise EvaluationSchemaError("case_id, category, and question are required")
        argument_data = data.get("expected_tool_arguments", [])
        numeric_data = data.get("numerical_expectations", [])
        if not isinstance(argument_data, list) or not isinstance(numeric_data, list):
            raise EvaluationSchemaError("expectation collections must be lists")
        fixture = data.get("fixture")
        if not isinstance(fixture, str) or not fixture:
            raise EvaluationSchemaError("case requires a fixture reference")
        return cls(
            schema_version=1,
            case_id=case_id,
            category=category,
            question=question,
            difficulty=difficulty,
            expected_symbols=_strings(data.get("expected_symbols"), "expected_symbols"),
            expected_entities=_strings(
                data.get("expected_entities"), "expected_entities"
            ),
            required_tools=_strings(data.get("required_tools"), "required_tools"),
            optional_tools=_strings(data.get("optional_tools"), "optional_tools"),
            forbidden_tools=_strings(data.get("forbidden_tools"), "forbidden_tools"),
            expected_tool_arguments=tuple(
                ToolArgumentExpectation.from_dict(item) for item in argument_data
            ),
            required_answer_elements=_strings(
                data.get("required_answer_elements"), "required_answer_elements"
            ),
            numerical_expectations=tuple(
                NumericExpectation.from_dict(item) for item in numeric_data
            ),
            fixture=fixture,
            grader=GraderConfiguration.from_dict(data.get("grader")),
            turns=_strings(data.get("turns"), "turns"),
        )


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """Provider-neutral result returned by an evaluation executor."""

    final_answer: str
    tool_calls: tuple[dict[str, object], ...] = ()
    tool_results: tuple[dict[str, object], ...] = ()
    artifacts: tuple[dict[str, object], ...] = ()
    errors: tuple[str, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True, slots=True)
class TrialRecord:
    """Complete, serializable record of one case/configuration trial."""

    schema_version: int
    case_id: str
    category: str
    trial_id: str
    configuration: str
    model: str | None
    prompt_version: str
    graph_version: str
    tool_calls: tuple[dict[str, object], ...]
    tool_results: tuple[dict[str, object], ...]
    artifacts: tuple[dict[str, object], ...]
    final_answer: str
    errors: tuple[str, ...]
    elapsed_seconds: float
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GradeResult:
    """Deterministic metrics and failure reasons for one trial."""

    case_id: str
    trial_id: str
    passed: bool
    metrics: dict[str, float | None]
    failure_reasons: tuple[str, ...] = field(default_factory=tuple)
    details: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class JudgeResult:
    """Structured output contract for an optional semantic LLM judge."""

    judge_model: str
    judge_version: str
    completeness: float
    evidence_based_interpretation: float
    appropriate_uncertainty: float
    rationale: str
