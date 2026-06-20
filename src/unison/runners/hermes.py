"""HermesRunner — wraps `hermes chat -q --yolo {prompt}`."""
from dataclasses import dataclass

from unison.runners.base import BaseRunner


@dataclass
class HermesRunner(BaseRunner):
    """`hermes chat -q --yolo {prompt}` wrapper.

    Executes the Hermes CLI via subprocess.run with stdout/stderr capture
    and timeout detection. Writes full output to log_path.
    """

    binary: str = "hermes"
