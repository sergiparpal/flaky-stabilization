"""The standalone ``flaky-stab`` CLI — the SECOND composition root.

``registration.py`` composes the plugin onto a Hermes ``PluginContext``; this
module composes the *same* stage CLIs onto a plain ``argparse`` parser, so the
detection half runs with no Hermes installed. Both roots drive one package, one
``state.db`` and one schema ladder — see ``docs/STANDALONE-CLI.md`` for why this
is a second root rather than an extracted package.

The mirror image of ``registration.py``'s rule applies here: that module is the
only place Hermes surfaces may be imported, and this one must never import them
at all. Everything it needs is already Hermes-free — ``cli.setup_cli`` was
written to receive a parser from the host, but it does not care who supplies it.

No module-level side effects: this file is imported both as ``__main__`` (via
``python -m flaky_stabilization``) and as ``flaky_stabilization.__main__`` (via
the ``flaky-stab`` console script), so anything done at import time would happen
twice or in the wrong process.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """The standalone top-level parser, with every stage subcommand mounted."""
    from . import __version__, cli  # deferred, matching the package's import style

    parser = argparse.ArgumentParser(
        prog="flaky-stab",
        description=(
            "Flaky-test history, detection, and reporting. Healing, triage, and "
            "bug-report structuring need the Hermes Agent plugin."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"flaky-stab {__version__}",
        help="Show the flaky-stabilization version and exit",
    )
    cli.setup_cli(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse *argv* (defaults to ``sys.argv[1:]``) and dispatch. Returns an exit code.

    Always returns an ``int``: setuptools wraps this as ``sys.exit(main())``.
    Usage errors and ``--help`` / ``--version`` still raise ``SystemExit`` from
    argparse itself, which carries its own code (2 and 0 respectively).
    """
    from . import cli

    parser = build_parser()
    return cli.run_cli(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
