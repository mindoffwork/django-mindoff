import os
import subprocess


def register_subcommand(subparsers):
    def _worker(args):
        env = os.environ.copy()
        env.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

        cmd = [
            "dramatiq",
            "--path",
            ".",
            args.module,
            "--processes",
            str(args.processes),
            "--threads",
            str(args.threads),
        ]
        subprocess.run(cmd, env=env, check=True)

    parser = subparsers.add_parser("runqueueworker", help="Start Dramatiq worker")
    parser.add_argument(
        "--module",
        default="django_mindoff.components._api_kit.queue_process",
        help="Python module containing dramatiq actors",
    )
    parser.add_argument("--processes", type=int, default=1)
    parser.add_argument("--threads", type=int, default=8)
    parser.set_defaults(handler=_worker)
