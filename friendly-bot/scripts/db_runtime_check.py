"""Guard the account-isolated local PostgreSQL Compose runtime."""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

FRIENDLY_BOT_DIRECTORY = Path(__file__).resolve().parents[1]
EXPECTED_UID = os.getuid()
EXPECTED_CHECKOUT = FRIENDLY_BOT_DIRECTORY.parent
COMPOSE_FILE = FRIENDLY_BOT_DIRECTORY / "compose.yaml"
RUNTIME_PROFILE = f"u{EXPECTED_UID}"
POSTGRES_ENV_FILE = (
    FRIENDLY_BOT_DIRECTORY / ".runtime" / RUNTIME_PROFILE / "postgres.env"
)
PROJECT_NAME = f"friendly-bot-{RUNTIME_PROFILE}"
NETWORK_NAME = PROJECT_NAME
VOLUME_NAME = f"{PROJECT_NAME}-postgres"
POSTGRES_PORT = 5833


class RuntimeGuardError(ValueError):
    """Raised when a command is not confined to this local runtime."""


@dataclass(frozen=True, slots=True)
class RuntimeNamespace:
    """Every literal that must match before Docker can be invoked."""

    uid: int
    checkout: Path
    project: str
    network: str
    volume: str
    port: int


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse a deliberately small, reviewable local runtime command surface."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("verify", "up", "down", "reset"), nargs="?", default="verify"
    )
    parser.add_argument("--uid", type=int, default=EXPECTED_UID)
    parser.add_argument("--checkout", type=Path, default=EXPECTED_CHECKOUT)
    parser.add_argument("--project", default=PROJECT_NAME)
    parser.add_argument("--network", default=NETWORK_NAME)
    parser.add_argument("--volume", default=VOLUME_NAME)
    parser.add_argument("--port", type=int, default=POSTGRES_PORT)
    parser.add_argument("--confirm-reset", action="store_true")
    return parser.parse_args(arguments)


def namespace_from_args(arguments: argparse.Namespace) -> RuntimeNamespace:
    """Translate parser values into a value object that can be checked before use."""

    return RuntimeNamespace(
        uid=arguments.uid,
        checkout=arguments.checkout,
        project=arguments.project,
        network=arguments.network,
        volume=arguments.volume,
        port=arguments.port,
    )


def validate_namespace(namespace: RuntimeNamespace) -> None:
    """Reject every mismatched scope value before any Docker command is built."""

    expected_values = {
        "uid": EXPECTED_UID,
        "checkout": EXPECTED_CHECKOUT.resolve(),
        "project": PROJECT_NAME,
        "network": NETWORK_NAME,
        "volume": VOLUME_NAME,
        "port": POSTGRES_PORT,
    }
    actual_values = {
        "uid": namespace.uid,
        "checkout": namespace.checkout.resolve(),
        "project": namespace.project,
        "network": namespace.network,
        "volume": namespace.volume,
        "port": namespace.port,
    }
    mismatches = [
        name
        for name, expected in expected_values.items()
        if actual_values[name] != expected
    ]
    if mismatches:
        raise RuntimeGuardError(f"unexpected runtime {'/'.join(mismatches)}")
    if os.getuid() != EXPECTED_UID:
        raise RuntimeGuardError("unexpected macOS user")
    if Path(__file__).resolve().parents[2] != EXPECTED_CHECKOUT.resolve():
        raise RuntimeGuardError("unexpected checkout")
    if not COMPOSE_FILE.is_file():
        raise RuntimeGuardError("Compose file is missing")


def create_postgres_env_if_needed() -> None:
    """Create ignored local credentials only for an explicitly requested startup."""

    if POSTGRES_ENV_FILE.exists():
        return

    POSTGRES_ENV_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    password = secrets.token_urlsafe(32)
    POSTGRES_ENV_FILE.write_text(
        "POSTGRES_USER=friendly_bot\n"
        f"POSTGRES_PASSWORD={password}\n"
        "POSTGRES_DB=friendly_bot\n",
        encoding="utf-8",
    )
    POSTGRES_ENV_FILE.chmod(0o600)


def require_postgres_env() -> None:
    """Keep destructive and teardown operations from using an unknown namespace."""

    if not POSTGRES_ENV_FILE.is_file():
        raise RuntimeGuardError("local PostgreSQL environment file is missing")


def run_compose(arguments: tuple[str, ...]) -> None:
    """Run Docker Compose with the only project and file this guard permits."""

    if (
        subprocess.run(
            ("docker", "compose", "version"),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        != 0
    ):
        raise RuntimeGuardError("Docker Compose v2 plugin is unavailable")
    compose_environment = os.environ | {
        "FRIENDLY_BOT_RUNTIME_NETWORK": NETWORK_NAME,
        "FRIENDLY_BOT_RUNTIME_PROFILE": RUNTIME_PROFILE,
        "FRIENDLY_BOT_RUNTIME_PROJECT": PROJECT_NAME,
        "FRIENDLY_BOT_RUNTIME_VOLUME": VOLUME_NAME,
    }
    subprocess.run(
        (
            "docker",
            "compose",
            "-p",
            PROJECT_NAME,
            "-f",
            str(COMPOSE_FILE),
            *arguments,
        ),
        check=True,
        cwd=FRIENDLY_BOT_DIRECTORY,
        env=compose_environment,
    )


def run_action(action: str, *, confirm_reset: bool) -> None:
    """Perform only the requested operation after namespace validation has completed."""

    if action == "verify":
        run_compose(("config", "--quiet"))
        return
    if action == "up":
        create_postgres_env_if_needed()
        run_compose(("up", "-d", "postgres"))
        return

    if action == "down":
        require_postgres_env()
        run_compose(("down",))
        return
    if not confirm_reset:
        raise RuntimeGuardError("reset requires --confirm-reset")
    run_compose(("down", "--volumes"))
    create_postgres_env_if_needed()
    run_compose(("up", "-d", "postgres"))


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate the account namespace before the selected local runtime action."""

    parsed = parse_args(arguments)
    try:
        validate_namespace(namespace_from_args(parsed))
        run_action(parsed.action, confirm_reset=parsed.confirm_reset)
    except RuntimeGuardError as error:
        print(f"runtime guard: {error}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError:
        print("runtime guard: Docker Compose command failed", file=sys.stderr)
        return 1

    print(f"runtime guard: {parsed.action} checked for {PROJECT_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
