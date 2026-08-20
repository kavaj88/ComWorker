#!/usr/bin/env python3
"""ComWorker launcher for the embedded Hermes runtime."""

from gateway.platforms.comworker_api_compat import install as install_comworker_overlay
from hermes_cli.main import main as hermes_main


def main() -> None:
    install_comworker_overlay()
    hermes_main()


if __name__ == "__main__":
    main()
