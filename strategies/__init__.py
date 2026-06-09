from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module


@dataclass(frozen=True)
class Strategy:
    """A loaded strategy: the function that runs it and the connector it trades through."""

    run: Callable
    connector: type


def get_strategy(name: str) -> Strategy:
    """Load the strategy defined in ``strategies/<name>.py``.

    The module must define a function named after itself and a ``CONNECTOR`` class.

    Raises:
        ValueError: The module is missing, or it lacks the function or the CONNECTOR.
    """
    try:
        module = import_module(f"strategies.{name}")
    except ModuleNotFoundError as e:
        raise ValueError(f"Strategy '{name}' not found: expected strategies/{name}.py") from e
    try:
        return Strategy(run=getattr(module, name), connector=module.CONNECTOR)
    except AttributeError as e:
        raise ValueError(
            f"strategies/{name}.py must define a function '{name}' and a CONNECTOR"
        ) from e
