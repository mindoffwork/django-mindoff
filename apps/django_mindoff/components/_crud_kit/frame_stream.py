"""
Bounded-memory frame streaming, and the temp files it leaves behind.

Two concerns that always travel together, so they live in one place instead of
being re-implemented by every caller:

- **Chunking.** :func:`iter_frames` walks a Polars frame in pieces small enough
  to hand to a database without materializing the whole thing. ``LazyFrame``
  inputs are sunk to a temporary Parquet file (bounded by the streaming engine)
  and re-read in Arrow batches; eager frames are sliced in place.
- **Temp-file lifecycle.** Those Parquet files outlive the call that wrote them
  whenever a ``LazyFrame`` still scans one, and on Windows an unlink fails
  outright while Polars holds the handle. :func:`track_temp_file` records a path
  for best-effort cleanup at interpreter exit; :func:`safe_unlink` removes one
  and stops tracking it.

Peak memory for a ``LazyFrame`` walk is one Parquet *row group*, not one
``batch_size`` chunk — pyarrow decodes at row-group granularity. Polars caps row
groups at roughly 83k rows regardless of dataset size, so the bound holds no
matter how large the input, but it is not as tight as ``batch_size`` suggests.
"""

import atexit
import gc
import os
import tempfile
import threading
import weakref

import polars as pl
import pyarrow.parquet as pq

# ----------------
# Constants
# ----------------
# Temp Parquet files that are still awaiting cleanup.
#
# The drain is registered with ``atexit`` exactly once. Registering it per file
# would grow the ``atexit`` registry for the life of the process, and
# ``atexit.unregister`` cannot undo a single registration — it matches on the
# function object alone, so it would cancel every other pending path too.
_PENDING_TEMP_FILES: set = set()
_TEMP_FILE_LOCK = threading.Lock()
_TEMP_DRAIN_REGISTERED = False


# ----------------
# Functions
# ----------------
def new_temp_path(suffix: str = ".parquet") -> str:
    """Reserve a temp file path, with the handle already closed.

    ``delete=False`` plus an immediate close, so the path can be handed to
    Polars/pyarrow writers that open it by name.
    """
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    path = handle.name
    handle.close()
    return path


def track_temp_file(path: str) -> None:
    """Record ``path`` as awaiting cleanup, registering the drain on first use."""
    global _TEMP_DRAIN_REGISTERED
    with _TEMP_FILE_LOCK:
        _PENDING_TEMP_FILES.add(path)
        if not _TEMP_DRAIN_REGISTERED:
            atexit.register(drain_temp_files)
            _TEMP_DRAIN_REGISTERED = True


def drain_temp_files() -> None:
    """Best-effort removal of every still-pending temp file, at interpreter exit.

    Snapshots under the lock before unlinking: :func:`safe_unlink` mutates the
    set as files go away, and a concurrent read may add to it — iterating it
    live could raise "Set changed size during iteration".
    """
    with _TEMP_FILE_LOCK:
        pending = list(_PENDING_TEMP_FILES)
    for path in pending:
        safe_unlink(path)


def safe_unlink(path: str) -> None:
    """Remove ``path``, forgetting it once it is actually gone.

    A failure other than "already gone" leaves the path pending so the atexit
    drain retries it — that is the Windows case, where Polars still holds the
    file handle at garbage-collection time.
    """
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass  # already removed: fall through and stop tracking it
    except OSError:  # pragma: no cover - still locked; retry at exit
        return
    with _TEMP_FILE_LOCK:
        _PENDING_TEMP_FILES.discard(path)


class _TempFileRelease:
    """Removes a temp file once every frame holding it has been collected.

    One ``weakref.finalize`` per frame would unlink on the *first* collection
    while the remaining frames still scan the file, so releases are counted and
    only the last one removes it.
    """

    __slots__ = ("_path", "_remaining", "_lock")

    def __init__(self, path: str, holders: int):
        self._path = path
        self._remaining = holders
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            self._remaining -= 1
            if self._remaining > 0:
                return
        safe_unlink(self._path)


def bind_temp_file(path: str, frames) -> None:
    """Tie ``path``'s lifetime to ``frames``, removing it when all are gone.

    For callers that hand a lazily-scanned frame back to *their* caller and so
    cannot unlink deterministically. A frame derived from one of ``frames``
    references the path rather than the object, so dropping every frame in
    ``frames`` does invalidate the derivation — the same contract
    ``arrow_reader.scan_to_lazy`` already has for ``read(is_lazy=True)``.

    Also registers the path for the exit-time drain, so an abandoned frame that
    is never collected still gets cleaned up.
    """
    track_temp_file(path)
    holders = [frm for frm in frames if frm is not None]
    if not holders:
        safe_unlink(path)  # nothing can read it: remove it now
        return
    owner = _TempFileRelease(path, len(holders))
    for frm in holders:
        weakref.finalize(frm, owner.release)


def iter_frames(df, batch_size: int):
    """Yield bounded-memory ``DataFrame`` chunks from an eager or lazy frame.

    ``LazyFrame`` inputs are streamed through Parquet so the full result is
    never held in memory; eager frames are sliced (a zero-copy view — the
    caller already holds the whole frame). At least one (possibly empty) frame
    is always yielded, so a caller creating a table from the first chunk still
    gets one for an empty result.
    """
    # Guard against a degenerate chunk size (``iter_slices``/the streaming
    # reader require >= 1); a non-positive size means "no chunking", i.e. one row.
    batch_size = max(1, batch_size)
    if isinstance(df, pl.LazyFrame):
        yield from iter_lazy_frames(df, batch_size)
    elif df.height == 0:
        yield df
    else:
        yield from df.iter_slices(batch_size)


def iter_lazy_frames(df, batch_size: int):
    """Sink a ``LazyFrame`` to Parquet and re-read it in Arrow batches."""
    tmp_path = new_temp_path()
    try:
        df.sink_parquet(tmp_path)
        with open(tmp_path, "rb") as file_handle:
            parquet = pq.ParquetFile(file_handle)
            wrote = False
            for batch in parquet.iter_batches(batch_size=batch_size):
                wrote = True
                yield pl.from_arrow(batch)
            if not wrote:
                yield pl.from_arrow(parquet.schema_arrow.empty_table())
            del parquet
    finally:
        gc.collect()  # release the Parquet file handle (Windows) before unlink
        safe_unlink(tmp_path)


def sink_frames_to_lazy(frames):
    """Write an iterator of frames to a temp Parquet file and scan it lazily.

    Returns ``(LazyFrame, path)``, or ``(None, None)`` when the iterator yielded
    nothing at all. Every frame after the first is cast to the first one's
    schema, so a chunk whose dtypes were inferred differently still appends.

    The caller owns ``path`` and must choose a cleanup strategy: hand it to
    :func:`track_temp_file` when the lifetime follows the returned frame, or
    unlink it deterministically when the work has a known end. Do not attach a
    finalizer to the returned ``LazyFrame`` and then build further frames from
    it — a derived frame references the *path*, not this object, so the
    finalizer would fire while the plan is still live.
    """
    path = new_temp_path()
    writer = None
    target_schema = None
    try:
        for frm in frames:
            if target_schema is None:
                target_schema = frm.schema
            else:
                frm = frm.cast(dict(target_schema))
            table = frm.to_arrow()
            if writer is None:
                writer = pq.ParquetWriter(path, table.schema)
            writer.write_table(table)
    finally:
        if writer is not None:
            writer.close()

    if writer is None:  # nothing was written
        safe_unlink(path)
        return None, None
    return pl.scan_parquet(path), path
