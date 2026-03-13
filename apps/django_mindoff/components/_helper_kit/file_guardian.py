import functools
import hashlib
import os
import shutil
import tempfile
from pathlib import Path


# ----------------
# Functions
# ----------------
def file_guardian(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        created_files, created_dirs = set(), set()
        modified_files = {}  # {original_path: backup_path}
        backup_root = tempfile.mkdtemp(prefix="rollback_")

        # Save original functions
        original_open = open
        original_makedirs = os.makedirs
        original_write_text = Path.write_text
        original_write_bytes = Path.write_bytes
        original_mkdir = Path.mkdir

        # Create wrapped versions
        _wrapped_open = _make_wrapped_open(
            created_files, modified_files, backup_root, original_open
        )
        _wrapped_makedirs = _make_wrapped_makedirs(created_dirs, original_makedirs)
        _wrapped_write_text = _make_wrapped_write_text(
            created_files, modified_files, backup_root, original_write_text
        )
        _wrapped_write_bytes = _make_wrapped_write_bytes(
            created_files, modified_files, backup_root, original_write_bytes
        )
        _wrapped_mkdir = _make_wrapped_mkdir(created_dirs, original_mkdir)

        # Patch
        builtins = __import__("builtins")
        setattr(builtins, "open", _wrapped_open)
        os.makedirs = _wrapped_makedirs
        Path.write_text = _wrapped_write_text
        Path.write_bytes = _wrapped_write_bytes
        Path.mkdir = _wrapped_mkdir

        try:
            result = func(self, *args, **kwargs)

            # Success — clean up backups
            _delete_dir_safely(backup_root)
            return result

        except Exception as e:
            print(f"\n[ERROR] Something went wrong during execution: {e}")
            print("[ACTION] Rolling back.")

            for f in created_files:
                _delete_file_safely(f)

            for orig, backup in modified_files.items():
                _restore_modified_file(orig, backup)

            for d in sorted(created_dirs, key=len, reverse=True):
                _delete_dir_safely(d)

            print("[OK] Rollback complete.")
            raise

        finally:
            # Restore originals
            setattr(builtins, "open", original_open)
            os.makedirs = original_makedirs
            Path.write_text = original_write_text
            Path.write_bytes = original_write_bytes
            Path.mkdir = original_mkdir

            # Clean up backup directory (if it wasn't deleted already)
            if os.path.exists(backup_root):
                _delete_dir_safely(backup_root)

    return wrapper


# ----------------
# Helper Functions
# ----------------
def _sha256sum(file_path):
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def _delete_file_safely(file):
    try:
        if os.path.exists(file):
            os.remove(file)
            print(f"[OK] Deleted file: {file}")
    except Exception as err:
        print(f"[ERROR] Could not delete file {file}: {err}")


def _delete_dir_safely(directory):
    try:
        if os.path.exists(directory):
            shutil.rmtree(directory)
    except Exception as err:
        print(f"[ERROR] Could not delete directory {directory}: {err}")


def _backup_modified_file(path, modified_files, backup_root):
    try:
        abs_path = os.path.abspath(path)
        _, rest = os.path.splitdrive(abs_path)
        safe_rel_path = rest.lstrip(os.sep)
        backup_path = os.path.join(backup_root, safe_rel_path)
        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
        shutil.copy2(path, backup_path)
        modified_files[path] = backup_path

    except Exception as err:
        print(f"[WARNING] Could not backup file {path}: {err}.")


def _restore_modified_file(path, backup_path):
    try:
        shutil.copy2(backup_path, path)
        print(f"[OK] Restored modified file: {path}")
    except Exception as err:
        print(f"[ERROR] Could not restore file {path}: {err}")


def _is_in_backup_root(path, backup_root):
    try:
        abs_path = os.path.abspath(path)
        abs_root = os.path.abspath(backup_root)
        return os.path.commonpath([abs_path, abs_root]) == abs_root
    except Exception:
        return False


def _make_wrapped_open(created_files, modified_files, backup_root, original_open):
    def _wrapped_open(file, mode="r", *a, **k):
        path = str(file)
        is_writing = any(m in mode for m in "wax+")
        if is_writing:
            if _is_in_backup_root(path, backup_root):
                return original_open(file, mode, *a, **k)
            if not os.path.exists(path):
                created_files.add(path)
            elif path not in modified_files:
                _backup_modified_file(path, modified_files, backup_root)
        return original_open(file, mode, *a, **k)

    return _wrapped_open


def _make_wrapped_makedirs(created_dirs, original_makedirs):
    def _wrapped_makedirs(name, exist_ok=False):
        path = Path(name)
        if not path.exists():
            created_dirs.add(str(path))
        return original_makedirs(name, exist_ok=exist_ok)

    return _wrapped_makedirs


def _make_wrapped_write_text(
    created_files, modified_files, backup_root, original_write_text
):
    def _wrapped_write_text(path_obj, data, encoding=None, errors=None):
        file = str(path_obj)
        if _is_in_backup_root(file, backup_root):
            return original_write_text(path_obj, data, encoding=encoding, errors=errors)
        if not os.path.exists(file):
            created_files.add(file)
        elif file not in modified_files:
            _backup_modified_file(file, modified_files, backup_root)
        return original_write_text(path_obj, data, encoding=encoding, errors=errors)

    return _wrapped_write_text


def _make_wrapped_write_bytes(
    created_files, modified_files, backup_root, original_write_bytes
):
    def _wrapped_write_bytes(path_obj, data):
        file = str(path_obj)
        if _is_in_backup_root(file, backup_root):
            return original_write_bytes(path_obj, data)
        if not os.path.exists(file):
            created_files.add(file)
        elif file not in modified_files:
            _backup_modified_file(file, modified_files, backup_root)
        return original_write_bytes(path_obj, data)

    return _wrapped_write_bytes


def _make_wrapped_mkdir(created_dirs, original_mkdir):
    def _wrapped_mkdir(self, mode=0o777, parents=False, exist_ok=False):
        path_str = str(self)
        if not Path(self).exists():
            created_dirs.add(path_str)
        return original_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    return _wrapped_mkdir
