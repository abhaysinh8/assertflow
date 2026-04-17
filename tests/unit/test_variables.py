import pytest

from assertflow.errors import VariableResolutionError
from assertflow.variables import VariableContext


def test_exact_placeholder_preserves_type_and_nested_values() -> None:
    context = VariableContext({"quantity": 10, "payload": {"quantity": "${quantity}"}})

    assert context.resolve("${quantity}") == 10
    assert context.resolve("${payload}") == {"quantity": 10}


def test_embedded_and_escaped_placeholders() -> None:
    context = VariableContext({"host": "api.test", "enabled": True})

    assert context.resolve("https://${host}/$${literal}/${enabled}") == (
        "https://api.test/${literal}/true"
    )


def test_unknown_and_recursive_variables_are_clear() -> None:
    with pytest.raises(VariableResolutionError, match="undefined variable: missing"):
        VariableContext().resolve("${missing}")

    with pytest.raises(VariableResolutionError, match="a -> b -> a"):
        VariableContext({"a": "${b}", "b": "${a}"}).resolve("${a}")


def test_precedence_is_process_then_environment_then_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE", "process")
    context = VariableContext.from_sources({"SOURCE": "environment"}, {"SOURCE": "suite"})

    assert context.resolve("${SOURCE}") == "suite"
