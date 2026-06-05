from collections.abc import Callable
from importlib import import_module


def get_strategy(name: str) -> Callable:
    """Return the strategy function ``name`` defined in ``strategies/<name>.py``.

    Raises:
        ValueError: No such module exists, or it does not define a function
            named after itself.
    """
    try:
        module = import_module(f"strategies.{name}")
    except ModuleNotFoundError as e:
        raise ValueError(f"Strategy '{name}' not found: expected strategies/{name}.py") from e
    try:
        return getattr(module, name)
    except AttributeError as e:
        raise ValueError(f"strategies/{name}.py does not define a function named '{name}'") from e
