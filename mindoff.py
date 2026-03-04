import argparse
import importlib
import pkgutil
import sys
from apps.django_mindoff.components import managers

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(prog="python mindoff.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _register_manager_subcommands(subparsers)
    args = parser.parse_args()
    if hasattr(args, "handler"):
        args.handler(args)
    else:
        parser.print_help()


def _register_manager_subcommands(subparsers):
    for _, name, is_pkg in pkgutil.iter_modules(managers.__path__):
        if is_pkg or name in {"init", "nuke"}:
            continue

        module = importlib.import_module(
            f"apps.django_mindoff.components.managers.{name}"
        )

        if not hasattr(module, "register_subcommand"):
            continue

        try:
            module.register_subcommand(subparsers)
        except argparse.ArgumentError as exc:
            if "conflicting subparser" not in str(exc):
                raise


if __name__ == "__main__":
    main()
