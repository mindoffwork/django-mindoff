import shutil
from pathlib import Path
from typing import List

from ..helper_kit import mo_helper_kit


# ======== CLASSES =======
# Add Classes here
class DjangoProjectDeleter:
    """
    Remove Django-Mindoff project artifacts from the current workspace.

    Supports guided scope selection, optional dry-run mode, guarded deletion, and
    summary reporting for deleted or skipped paths.
    """
    def __init__(self, dry_run: bool = False, delete_all: bool = False):
        self.project_root = Path.cwd()
        self.default_exclude = {".git", ".venv", ".gitignore", "README.md", ".env.bak"}
        self.exclude_files = {}  # empty for now, can add files here to exclude
        self.project_artifacts = [
            "config",
            "apps",
            "templates",
            ".env",
            ".gitignore",
            "pytest.ini",
            "db.sqlite3",
            "manage.py",
            "mindoff.py",
            "README.md",
            ".venv",
            ".git",
            "run_env.sh",
            "run_env.bat",
        ]
        self.dry_run = dry_run
        self.delete_all = delete_all
        self.deleted: List[Path] = []
        self.skipped: List[Path] = []
        self.to_delete: List[Path] = []

    def _identify_targets(self):
        for item in self.project_artifacts:
            path = self.project_root / item
            if not path.exists():
                continue
            if not self.delete_all and path.name in self.default_exclude:
                self.skipped.append(path)
                continue
            if path.name in self.exclude_files:
                self.skipped.append(path)
                continue
            self.to_delete.append(path)

    def _print_dry_run(self):
        print("\n[ACTION] [Dry Run Mode] Planned Deletions:")
        for path in self.to_delete:
            print(f"  • {path.relative_to(self.project_root)}")
        print("\n[OK] [Dry Run Mode] Dry run complete. No files deleted.")

    def _confirm_deletion(self) -> bool:
        if not self.to_delete:
            print("[ERROR] No files to delete.")
            return False
        print("The following file(s)/folder(s) will be deleted:")
        for path in self.to_delete:
            print(f"  • {path.relative_to(self.project_root)}")
        confirm = (
            input("\nAre you sure you want to proceed? This cannot be undone. (y/N): ")
            .strip()
            .lower()
        )
        return confirm == "y"

    def _perform_deletion(self):
        for path in self.to_delete:
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                self.deleted.append(path)
            except Exception as e:
                print(f"[ERROR] Failed to delete {path}: {e}")

    def _report_summary(self):
        if self.deleted:
            print("\nDeleted:")
            for p in self.deleted:
                print(f"  • {p.relative_to(self.project_root)}")
        if self.skipped:
            print("\nSkipped:")
            for p in self.skipped:
                print(f"  • {p.relative_to(self.project_root)}")

    def _choose_scope(self) -> bool:
        print(
            "You're about to permanently delete the Django-Mindoff project in the current directory. \n"
            "Select a removal method:"
        )
        print("1. Basic -- Remove project files only")
        print("2. Full -- Remove both project files and workspace")

        choice = input("\nEnter choice of number (default: 1): ").strip() or "1"
        self.delete_all = choice == "2"
        if choice == "1":
            print("\n[Selected removal method: Basic]")
        else:
            print("\n[Selected removal method: Full]")
        return True

    @mo_helper_kit.file_guardian
    def run(self):
        """Run the guided project deletion workflow."""
        print("\n# ------- Mindoff > Nuke ------- #")
        self._choose_scope()
        self._identify_targets()

        if self.dry_run:
            self._print_dry_run()
            return

        if not self._confirm_deletion():
            print("Nuke process aborted. Exiting.")
            return

        self._perform_deletion()
        self._report_summary()
        print("Nuke process completed successfully.")


# ======== FUNCTIONS =======
# Add Functions here
# F1. Command Entry Point -- Registers the command into the CLI.
def register_subcommand(subparsers):
    """Register the `nuke` manager command and handler."""
    def _delete_project(args):
        DjangoProjectDeleter(dry_run=args.dry_run, delete_all=args.all).run()

    parser = subparsers.add_parser("nuke", help="Removes the Current Django Project.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned actions without making any changes",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Delete all project artificates related to mindoff",
    )
    parser.set_defaults(handler=_delete_project)
