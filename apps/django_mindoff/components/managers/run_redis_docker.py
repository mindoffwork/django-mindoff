import subprocess


def _container_exists(container_name: str) -> bool:
    result = subprocess.run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"name=^/{container_name}$",
            "--format",
            "{{.ID}}",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(result.stdout.strip())


def register_subcommand(subparsers):
    def _run_redis_docker(args):
        if _container_exists(args.container_name):
            subprocess.run(["docker", "start", args.container_name], check=True)
            print(f"[OK] Started existing container: {args.container_name}")
            return

        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                args.container_name,
                "-p",
                f"{args.port}:6379",
                args.image,
            ],
            check=True,
        )
        print(
            f"[OK] Redis container '{args.container_name}' is running on port {args.port}"
        )

    parser = subparsers.add_parser(
        "runredisdocker",
        help="Run or start a Redis Docker container",
    )
    parser.add_argument("--container-name", default="mindoff-redis")
    parser.add_argument("--port", type=int, default=6379)
    parser.add_argument("--image", default="redis:7-alpine")
    parser.set_defaults(handler=_run_redis_docker)
