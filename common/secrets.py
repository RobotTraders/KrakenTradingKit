import tomllib
from pathlib import Path


SECRETS_DIR = Path(__file__).resolve().parents[1] / "bots"


class MissingCredentialError(Exception):
    """Raised when a secret in the secrets file is empty."""


def get_secrets(name: str, *, required: tuple[str, ...] = ()) -> dict[str, str]:
    """Load all secrets from ``bots/<name>.toml``, dropping empty values.

    Args:
        required: Keys that must be present and non-empty.

    Raises:
        FileNotFoundError: The secrets file does not exist.
        MissingCredentialError: A required secret is absent or empty.
    """
    path = SECRETS_DIR / f"{name}.toml"
    if not path.exists():
        raise FileNotFoundError(f"Secrets file not found at {path}")

    with open(path, "rb") as f:
        secrets = tomllib.load(f)

    missing = [key for key in required if not secrets.get(key)]
    if missing:
        raise MissingCredentialError(f"Secrets {missing} are not set in {path}")
    return {key: value for key, value in secrets.items() if value}
