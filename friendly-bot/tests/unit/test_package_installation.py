import subprocess
import sys


def test_friendly_bot_is_installed_in_the_uv_environment() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import friendly_bot"],
        check=False,
        cwd="/",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
