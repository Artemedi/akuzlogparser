"""Bounded one-child process lifecycle for Phase 11 prefetch.

This module contains only process ownership/cancellation/cleanup mechanics.
It does not know about AKUZ inventory/cache and is not wired into perform_build.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
import hashlib
import json
import os
import re
import threading
from multiprocessing import parent_process
from time import monotonic, perf_counter, process_time, sleep

from akuz_fetch import fetch_selected


class ProcessFetchError(RuntimeError):
    pass


class ProcessFetchUnsafeError(ProcessFetchError):
    """Cleanup/lifecycle failure where serial fallback is unsafe."""
    pass


@dataclass(frozen=True)
class ProcessFetchResult:
    path: Path
    digest: str
    bytes: int
    child_cpu_s: float | None
    child_fetch_wall_s: float | None
    ready_latency_s: float
    metadata: dict
    cleanup_identity: tuple | None = None


class ProcessFetch:
    """Own one spawned fetch child and one destination path.

    The parent may do CPU work after start(). On any parent/child failure the
    child is terminated and the owned destination is removed. Success transfers
    ownership of the completed destination to the caller.
    """

    def __init__(self, ctx, target, args, destination: Path,
                 *, poll_timeout_s=300, join_timeout_s=20, kill_timeout_s=10,
                 name="akuz-phase11-fetch", require_metrics=False,
                 expected_listed_bytes=None, require_kill_job=False,
                 safe_ipc=True):
        self.ctx = ctx
        self.target = target
        self.args = tuple(args)
        self.destination = Path(destination)
        self.poll_timeout_s = poll_timeout_s
        self.join_timeout_s = join_timeout_s
        self.kill_timeout_s = kill_timeout_s
        self.name = name
        self.require_metrics = require_metrics
        self.expected_listed_bytes = expected_listed_bytes
        self.require_kill_job = require_kill_job
        if safe_ipc is not True:
            raise ProcessFetchError("Process fetch requires bounded JSON IPC")
        self.safe_ipc = True
        if require_kill_job:
            if (not isinstance(expected_listed_bytes, int)
                    or isinstance(expected_listed_bytes, bool)
                    or expected_listed_bytes < 0):
                raise ProcessFetchError(
                    "Production Job prefetch requires listed-size binding")
        self.receiver = None
        self._start_gate = None
        self._kill_job = None
        self.child = None
        self.started_at = None
        self._success = False
        self._validated_cleanup_identity = None
        self._preserve_validated_snapshot = False

    def start(self):
        if self.child is not None:
            raise RuntimeError("ProcessFetch already started")
        receiver, sender = self.ctx.Pipe(duplex=False)
        if self.require_kill_job:
            if os.name != "nt":
                receiver.close()
                sender.close()
                raise ProcessFetchError(
                    "Kill-on-close Job Object requires Windows")
            if not getattr(self.ctx, "_akuz_job_bound_context", False):
                receiver.close()
                sender.close()
                raise ProcessFetchUnsafeError(
                    "Production prefetch requires atomic Job-bound spawn")
        child = self.ctx.Process(
            target=self.target,
            args=(*self.args, str(self.destination), sender),
            name=self.name)
        self.receiver = receiver
        self.child = child
        self.started_at = perf_counter()
        try:
            child.start()
        except BaseException:
            receiver.close()
            sender.close()
            self.receiver = None
            self.child = None
            self.started_at = None
            remove_owned_snapshot(self.destination)
            raise
        # Capture the authoritative Job owner immediately after the atomic
        # start returns, before any fallible parent-side pipe cleanup. If
        # sender.close() fails, abort() must still tear down the whole Job
        # tree rather than only the direct process.
        if self.require_kill_job:
            popen = getattr(child, "_popen", None)
            owner = getattr(popen, "_akuz_job_owner", None)
            if owner is None or getattr(owner, "closed", True):
                try:
                    self.abort()
                except ProcessFetchUnsafeError:
                    raise
                raise ProcessFetchUnsafeError(
                    "Atomic Job-bound spawn did not expose live Job ownership")
            self._kill_job = owner

        # Parent sender ownership ends immediately after start. Child EOF/crash
        # behavior remains authoritative; a sender-close exception is allowed
        # to surface only after Job-aware abort has completed.
        try:
            sender.close()
        except BaseException as sender_exc:
            try:
                self.abort()
            except ProcessFetchUnsafeError as unsafe_exc:
                raise unsafe_exc from sender_exc
            raise
        return self

    def _release_kill_job(self):
        if self._kill_job is None:
            return
        # Keep the exact owner reachable until its close succeeds. _JobOwner
        # is retry-safe: a failed CloseHandle retains its raw HANDLE, so
        # clearing this alias before success would orphan the only explicit
        # retry path during abort/finally cleanup.
        handle = self._kill_job
        close = getattr(handle, "close", None)
        if callable(close):
            try:
                close()
            except BaseException as exc:
                raise ProcessFetchUnsafeError(
                    "Could not close Windows kill Job Object") from exc
        else:
            try:
                _close_windows_handle(handle)
            except BaseException as exc:
                raise ProcessFetchUnsafeError(
                    "Could not close Windows kill Job Object") from exc
        if self._kill_job is handle:
            self._kill_job = None

    def finish(self) -> ProcessFetchResult:
        if self.child is None or self.receiver is None or self.started_at is None:
            raise RuntimeError("ProcessFetch not started")
        message = None
        primary_error = None
        try:
            deadline = monotonic() + self.poll_timeout_s
            # The one-way Pipe is the sole IPC authority. Do not mix a
            # Connection with a raw Windows process HANDLE in
            # multiprocessing.connection.wait(); that contract is not stable
            # across CPython/Windows versions. Poll the Pipe in short bounded
            # slices and use child liveness only to shorten crash detection.
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Process fetch did not return")
                try:
                    if self.receiver.poll(min(0.10, remaining)):
                        break
                except (EOFError, OSError) as exc:
                    raise ProcessFetchError(
                        "Process fetch pipe closed or invalid") from exc

                if not self.child.is_alive():
                    self.child.join(timeout=0)
                    # send_bytes may have completed just before process exit;
                    # preserve a bounded grace for the Pipe buffer to become
                    # visible, but never wait the full operation timeout.
                    drain = min(0.5, max(0.0, deadline - monotonic()))
                    try:
                        if drain > 0 and self.receiver.poll(drain):
                            break
                    except (EOFError, OSError) as exc:
                        raise ProcessFetchError(
                            "Process fetch pipe closed or invalid") from exc
                    raise ProcessFetchError(
                        "Process fetch child exited before IPC result")

            try:
                raw = self.receiver.recv_bytes(maxlength=8192)
                message = json.loads(raw.decode("utf-8"))
            except (EOFError, OSError, UnicodeDecodeError, ValueError) as exc:
                raise ProcessFetchError(
                    "Process fetch pipe closed or invalid") from exc

            self.child.join(timeout=self.join_timeout_s)
            if self.child.is_alive():
                raise TimeoutError("Process fetch did not exit")
            if self.child.exitcode != 0 or not message or message[0] != "ok":
                kind = message[1] if message and message[0] == "error" else "ChildExit"
                raise ProcessFetchError("Process fetch failed: " + str(kind))
            if len(message) not in (6, 7):
                raise ProcessFetchError("Process fetch IPC shape invalid")

            _, returned_path, digest, count, child_cpu, child_fetch_wall = message[:6]
            metadata = message[6] if len(message) == 7 else {}
            if not isinstance(metadata, dict):
                raise ProcessFetchError("Process fetch metadata invalid")

            if not isinstance(returned_path, str):
                raise ProcessFetchError("Process fetch returned path invalid")
            if not _same_path_lexical(returned_path, self.destination):
                raise ProcessFetchError(
                    "Process fetch returned unexpected snapshot path")
            # The parent owns the destination path. Do not resolve or open the
            # child-provided path; validate the known owned path itself and
            # reject symlink/reparse substitution.
            returned = self.destination
            st = _owned_snapshot_stat(returned)
            if (not isinstance(count, int) or isinstance(count, bool)
                    or count < 0):
                raise ProcessFetchError("Process fetch size invalid")
            if st.st_size != count:
                raise ProcessFetchError("Process fetch snapshot incomplete")
            if (not isinstance(digest, str) or
                    re.fullmatch(r"[0-9a-f]{64}", digest) is None):
                raise ProcessFetchError("Process fetch digest invalid")
            actual_digest, cleanup_identity = _sha256_owned_snapshot(
                returned, st, return_cleanup_identity=True)
            if actual_digest != digest:
                raise ProcessFetchError(
                    "Process fetch snapshot checksum mismatch")

            allowed_metadata = {
                "active", "captured_bytes", "stored_bytes",
                "dropped_tail_bytes", "listed_bytes"}
            if any(key not in allowed_metadata for key in metadata):
                raise ProcessFetchError(
                    "Process fetch metadata contains unknown fields")
            if "active" in metadata and not isinstance(metadata["active"], bool):
                raise ProcessFetchError("Process fetch active flag invalid")
            for key in allowed_metadata - {"active"}:
                if key in metadata and (
                        not isinstance(metadata[key], int)
                        or isinstance(metadata[key], bool)
                        or metadata[key] < 0):
                    raise ProcessFetchError("Process fetch metadata invalid")

            if self.expected_listed_bytes is not None:
                expected = self.expected_listed_bytes
                if (not isinstance(expected, int) or isinstance(expected, bool)
                        or expected < 0):
                    raise ProcessFetchError(
                        "Process fetch expected size invalid")
                if metadata.get("listed_bytes") != expected:
                    raise ProcessFetchError(
                        "Process fetch listed-size binding mismatch")
                if metadata.get("stored_bytes") != count:
                    raise ProcessFetchError(
                        "Process fetch stored-size binding mismatch")
                captured = metadata.get("captured_bytes")
                if not isinstance(captured, int) or captured < count:
                    raise ProcessFetchError(
                        "Process fetch captured-size binding mismatch")
                if metadata.get("active") is False and captured != count:
                    raise ProcessFetchError(
                        "Process fetch static snapshot size mismatch")

            if self.require_metrics:
                if child_cpu is None or float(child_cpu) < 0:
                    raise ProcessFetchError(
                        "Process fetch CPU evidence unavailable")
                if child_fetch_wall is None or float(child_fetch_wall) < 0:
                    raise ProcessFetchError(
                        "Process fetch wall evidence unavailable")

            result = ProcessFetchResult(
                path=returned,
                digest=digest,
                bytes=count,
                child_cpu_s=(None if child_cpu is None else float(child_cpu)),
                child_fetch_wall_s=(
                    None if child_fetch_wall is None
                    else float(child_fetch_wall)),
                ready_latency_s=perf_counter() - self.started_at,
                metadata=dict(metadata),
                cleanup_identity=cleanup_identity)

            # From this point the snapshot is fully validated and bound to one
            # exact Windows file identity. If process/Job handle cleanup fails,
            # preserve this temp snapshot rather than deleting trusted evidence
            # in the generic failure-finally path.
            self._validated_cleanup_identity = cleanup_identity
            self._preserve_validated_snapshot = True
            self._close_reaped_child_resources()
            self._start_gate = None
            self._success = True
            self._preserve_validated_snapshot = False
            return result
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            receiver = self.receiver
            self.receiver = None
            if receiver is not None:
                try:
                    receiver.close()
                except OSError as close_exc:
                    if primary_error is not None:
                        try:
                            primary_error.add_note(
                                "Phase 11 pipe close also failed: " +
                                type(close_exc).__name__)
                        except BaseException:
                            pass
            if not self._success:
                try:
                    self.abort()
                except ProcessFetchUnsafeError:
                    # A live child or unclosed Job makes serial fallback unsafe.
                    raise
                except Exception as cleanup_exc:
                    # Preserve the original fetch/timeout failure as the main
                    # diagnostic while still surfacing secondary cleanup facts.
                    if primary_error is None:
                        raise
                    try:
                        primary_error.add_note(
                            "Phase 11 cleanup also failed: " +
                            type(cleanup_exc).__name__ + ": " +
                            str(cleanup_exc))
                    except BaseException:
                        pass

    def _close_reaped_child_resources(self):
        """Synchronously close process/pipe/Job handles after child exit."""
        child = self.child
        if child is None:
            self._release_kill_job()
            return

        close = getattr(child, "close", None)
        if self.require_kill_job and callable(close):
            try:
                close()
            except BaseException as exc:
                # Keep the alias to the same _JobOwner on failure. abort()
                # can then retry the exact owner before retrying any remaining
                # process/pipe HANDLE slots held by JobBoundPopen.
                raise ProcessFetchUnsafeError(
                    "Could not close spawned child process handles") from exc
            # Atomic JobBoundPopen closed this same _JobOwner successfully.
            self._kill_job = None
            return

        # Unit/benchmark callers without the production JobBound context.
        self._release_kill_job()

    def abort(self):
        child = self.child
        survivor = False
        unsafe_error = None
        pipe_error = None
        try:
            if child is not None:
                if child.is_alive() and self._kill_job is not None:
                    try:
                        self._release_kill_job()
                    except Exception as exc:
                        unsafe_error = exc
                    child.join(timeout=self.kill_timeout_s)
                if child.is_alive():
                    child.terminate()
                    child.join(timeout=self.kill_timeout_s)
                if child.is_alive():
                    child.kill()
                    child.join(timeout=self.kill_timeout_s)
                survivor = child.is_alive()
                if not survivor:
                    child.join(timeout=0)
        finally:
            if self._kill_job is not None:
                try:
                    self._release_kill_job()
                except Exception as exc:
                    if unsafe_error is None:
                        unsafe_error = exc
            self._start_gate = None
            if self.receiver is not None:
                try:
                    self.receiver.close()
                except OSError as exc:
                    pipe_error = exc
                finally:
                    self.receiver = None

        if not survivor and child is not None:
            try:
                self._close_reaped_child_resources()
            except BaseException as exc:
                if unsafe_error is None:
                    unsafe_error = exc

        if survivor:
            raise ProcessFetchUnsafeError(
                "Process fetch child could not be terminated") from unsafe_error
        if unsafe_error is not None:
            raise ProcessFetchUnsafeError(
                "Process fetch Windows Job handle could not be closed"
            ) from unsafe_error

        # A fully validated snapshot is preserved if later handle teardown was
        # unsafe. It was never promoted/indexed, and app-scoped orphan cleanup
        # can handle it on the next run without destroying diagnostic evidence.
        if not self._preserve_validated_snapshot:
            remove_owned_snapshot(
                self.destination,
                expected_identity=self._validated_cleanup_identity)
        if pipe_error is not None:
            raise ProcessFetchError(
                "Process fetch pipe could not be closed") from pipe_error

    def close(self):
        if not self._success:
            self.abort()
        if self.receiver is not None:
            self.receiver.close()
            self.receiver = None
        self._release_kill_job()
        self._start_gate = None

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self.close()
            return False
        try:
            self.close()
        except ProcessFetchUnsafeError as cleanup_exc:
            # Safety failure wins, but preserve the report/parse failure as
            # explicit exception context for diagnosis.
            raise cleanup_exc from exc
        return False


def _same_path_lexical(left, right) -> bool:
    left_value = os.path.normcase(os.path.abspath(os.path.normpath(str(left))))
    right_value = os.path.normcase(os.path.abspath(os.path.normpath(str(right))))
    return left_value == right_value


def _owned_snapshot_stat(path: Path):
    target = Path(path)
    try:
        st = target.lstat()
    except OSError as exc:
        raise ProcessFetchError("Process fetch snapshot missing") from exc
    if target.is_symlink():
        raise ProcessFetchError("Process fetch snapshot is a symlink")
    # Windows st_file_attributes exposes junction/reparse substitution without
    # following it. Reject every reparse-point file on the owned temp path.
    if getattr(st, "st_file_attributes", 0) & 0x0400:
        raise ProcessFetchError("Process fetch snapshot is a reparse point")
    import stat
    if not stat.S_ISREG(st.st_mode):
        raise ProcessFetchError("Process fetch snapshot is not a regular file")
    parent = target.parent
    try:
        parent_st = parent.lstat()
    except OSError as exc:
        raise ProcessFetchError("Process fetch snapshot parent missing") from exc
    if parent.is_symlink() or (
            getattr(parent_st, "st_file_attributes", 0) & 0x0400):
        raise ProcessFetchError("Process fetch snapshot parent is redirected")
    return st


def _windows_handle_value(handle) -> int:
    value = getattr(handle, "value", handle)
    if value is None:
        raise ProcessFetchUnsafeError("Windows handle value unavailable")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProcessFetchUnsafeError("Windows handle value invalid") from exc


def _windows_cleanup_identity(handle):
    import ctypes
    from ctypes import wintypes

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    info = BY_HANDLE_FILE_INFORMATION()
    if not kernel32.GetFileInformationByHandle(
            wintypes.HANDLE(_windows_handle_value(handle)),
            ctypes.byref(info)):
        raise ProcessFetchUnsafeError(
            "Could not identify owned Windows snapshot handle")
    if info.dwFileAttributes & 0x0400:
        raise ProcessFetchUnsafeError(
            "Owned process snapshot became a reparse point")
    if info.dwFileAttributes & 0x0010:
        raise ProcessFetchUnsafeError(
            "Owned process snapshot became a directory")
    file_index = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
    file_size = (int(info.nFileSizeHigh) << 32) | int(info.nFileSizeLow)
    return int(info.dwVolumeSerialNumber), file_index, file_size


def _windows_open_directory_identity_handle(path: Path):
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE

    file_read_attributes = 0x00000080
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    flags = 0x00200000 | 0x02000000  # OPEN_REPARSE_POINT | BACKUP_SEMANTICS
    handle = kernel32.CreateFileW(
        str(path), file_read_attributes, share_all,
        None, open_existing, flags, None)
    value = getattr(handle, "value", handle)
    invalid = ctypes.c_void_p(-1).value
    if value in (None, invalid):
        error = ctypes.get_last_error()
        raise OSError(error, "Could not open owned snapshot parent")
    return handle


def _windows_directory_identity(handle):
    import ctypes
    from ctypes import wintypes

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]
    kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
    info = BY_HANDLE_FILE_INFORMATION()
    if not kernel32.GetFileInformationByHandle(
            wintypes.HANDLE(_windows_handle_value(handle)),
            ctypes.byref(info)):
        raise ProcessFetchUnsafeError(
            "Could not identify owned snapshot parent")
    if info.dwFileAttributes & 0x0400:
        raise ProcessFetchUnsafeError(
            "Owned snapshot parent became a reparse point")
    if not (info.dwFileAttributes & 0x0010):
        raise ProcessFetchUnsafeError(
            "Owned snapshot parent is not a directory")
    file_index = (int(info.nFileIndexHigh) << 32) | int(info.nFileIndexLow)
    return int(info.dwVolumeSerialNumber), file_index


def _windows_parent_identity(path: Path):
    handle = _windows_open_directory_identity_handle(Path(path))
    try:
        return _windows_directory_identity(handle)
    finally:
        _close_windows_handle(handle)


def _windows_open_promotion_guard_handle(path: Path, *, directory=False):
    """Open an identity guard that prevents pathname replacement on Windows.

    The file guard requests GENERIC_READ and shares only READ. This blocks
    writers, rename and deletion while the guard is alive. The directory
    guard omits FILE_SHARE_DELETE so the owned parent cannot be swapped while
    a hard-link promotion resolves the source pathname.
    """
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE

    generic_read = 0x80000000
    file_read_attributes = 0x00000080
    share_read = 0x00000001
    share_write = 0x00000002
    open_existing = 3
    open_reparse_point = 0x00200000
    backup_semantics = 0x02000000

    access = file_read_attributes if directory else generic_read
    share = share_read | share_write if directory else share_read
    flags = open_reparse_point | (backup_semantics if directory else 0)
    handle = kernel32.CreateFileW(
        str(path), access, share, None, open_existing, flags, None)
    value = getattr(handle, "value", handle)
    invalid = ctypes.c_void_p(-1).value
    if value in (None, invalid):
        error = ctypes.get_last_error()
        raise ProcessFetchUnsafeError(
            "Could not lock owned snapshot identity for promotion"
        ) from OSError(error, "CreateFileW promotion guard failed")
    return handle


@contextmanager
def owned_snapshot_promotion_guard(path: Path, expected_identity):
    """Freeze the validated Windows file+parent identity across promotion.

    Phase 11 production prefetch runs only on Windows. Non-Windows callers
    use the historical benchmark/test path and get a no-op guard.
    """
    if os.name != "nt":
        yield
        return
    if (not isinstance(expected_identity, tuple)
            or len(expected_identity) != 2
            or expected_identity[0] is None
            or expected_identity[1] is None):
        raise ProcessFetchUnsafeError(
            "Validated Windows snapshot identity is unavailable")

    target = Path(path)
    expected_file, expected_parent = expected_identity
    parent_handle = None
    file_handle = None
    close_errors = []
    try:
        parent_handle = _windows_open_promotion_guard_handle(
            target.parent, directory=True)
        if _windows_directory_identity(parent_handle) != expected_parent:
            raise ProcessFetchUnsafeError(
                "Owned snapshot parent changed before promotion")

        file_handle = _windows_open_promotion_guard_handle(target)
        if _windows_cleanup_identity(file_handle) != expected_file:
            raise ProcessFetchUnsafeError(
                "Owned snapshot changed before promotion")

        # Re-read the already-open parent HANDLE after the file HANDLE exists.
        # Both stay open without delete sharing until the caller completes the
        # hard-link operation, closing the pathname replacement window.
        if _windows_directory_identity(parent_handle) != expected_parent:
            raise ProcessFetchUnsafeError(
                "Owned snapshot parent changed during promotion setup")

        yield

        if _windows_cleanup_identity(file_handle) != expected_file:
            raise ProcessFetchUnsafeError(
                "Owned snapshot changed during promotion")
        if _windows_directory_identity(parent_handle) != expected_parent:
            raise ProcessFetchUnsafeError(
                "Owned snapshot parent changed during promotion")
    finally:
        if file_handle is not None:
            try:
                _close_windows_handle(file_handle)
            except BaseException as exc:
                close_errors.append(exc)
        if parent_handle is not None:
            try:
                _close_windows_handle(parent_handle)
            except BaseException as exc:
                close_errors.append(exc)
        if close_errors:
            raise ProcessFetchUnsafeError(
                "Could not close owned snapshot promotion guard"
            ) from close_errors[0]


def _windows_open_cleanup_handle(path: Path, access: int):
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE

    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    open_existing = 3
    open_reparse_point = 0x00200000
    handle = kernel32.CreateFileW(
        str(path), access,
        file_share_read | file_share_write | file_share_delete,
        None, open_existing, open_reparse_point, None)
    value = getattr(handle, "value", handle)
    invalid = ctypes.c_void_p(-1).value
    if value in (None, invalid):
        error = ctypes.get_last_error()
        if error in (2, 3):
            return None
        raise OSError(error, "Could not open owned Windows snapshot")
    return handle


def _remove_owned_snapshot_windows(
        target: Path, *, attempts=100, delay_s=0.10,
        expected_identity=None):
    import ctypes
    from ctypes import wintypes

    file_read_attributes = 0x00000080
    delete_access = 0x00010000
    parent_guard = None
    anchor = None
    close_errors = []
    try:
        # Hold the exact parent directory open without FILE_SHARE_DELETE for
        # the entire cleanup operation. This closes the directory-swap window
        # between identity validation and SetFileInformationByHandle.
        parent_guard = _windows_open_promotion_guard_handle(
            target.parent, directory=True)
        parent_identity = _windows_directory_identity(parent_guard)

        anchor = _windows_open_cleanup_handle(target, file_read_attributes)
        if anchor is None:
            return
        owned_identity = _windows_cleanup_identity(anchor)

        if expected_identity is not None:
            expected_file, expected_parent = expected_identity
            if owned_identity != expected_file:
                raise ProcessFetchUnsafeError(
                    "Owned process snapshot identity changed before cleanup")
            if parent_identity != expected_parent:
                raise ProcessFetchUnsafeError(
                    "Owned process snapshot parent changed before cleanup")
        last_error = None

        class FILE_DISPOSITION_INFO(ctypes.Structure):
            _fields_ = [("DeleteFile", ctypes.c_ubyte)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.SetFileInformationByHandle.argtypes = [
            wintypes.HANDLE, ctypes.c_int,
            ctypes.c_void_p, wintypes.DWORD]
        kernel32.SetFileInformationByHandle.restype = wintypes.BOOL

        for attempt in range(max(1, attempts)):
            delete_handle = None
            try:
                delete_handle = _windows_open_cleanup_handle(
                    target, delete_access | file_read_attributes)
                if delete_handle is None:
                    return
                if _windows_cleanup_identity(delete_handle) != owned_identity:
                    raise ProcessFetchUnsafeError(
                        "Owned process snapshot pathname was replaced")
                if _windows_directory_identity(parent_guard) != parent_identity:
                    raise ProcessFetchUnsafeError(
                        "Owned process snapshot parent was replaced")
                disposition = FILE_DISPOSITION_INFO(1)
                if not kernel32.SetFileInformationByHandle(
                        wintypes.HANDLE(_windows_handle_value(delete_handle)),
                        4, ctypes.byref(disposition),
                        ctypes.sizeof(disposition)):
                    error = ctypes.get_last_error()
                    raise OSError(
                        error, "Could not mark owned Windows snapshot deleted")
                return
            except ProcessFetchUnsafeError:
                raise
            except OSError as exc:
                last_error = exc
            finally:
                if delete_handle is not None:
                    _close_windows_handle(delete_handle)

            if attempt + 1 < max(1, attempts):
                sleep(delay_s)

        raise ProcessFetchUnsafeError(
            "Owned process snapshot could not be removed") from last_error
    finally:
        if anchor is not None:
            try:
                _close_windows_handle(anchor)
            except BaseException as exc:
                close_errors.append(exc)
        if parent_guard is not None:
            try:
                _close_windows_handle(parent_guard)
            except BaseException as exc:
                close_errors.append(exc)
        if close_errors:
            raise ProcessFetchUnsafeError(
                "Could not close owned snapshot cleanup guards"
            ) from close_errors[0]

def remove_owned_snapshot(
        path: Path, *, attempts=100, delay_s=0.10,
        expected_identity=None):
    """Remove one owned temp file without ever deleting a replacement."""
    target = Path(path)
    if os.name == "nt":
        return _remove_owned_snapshot_windows(
            target, attempts=attempts, delay_s=delay_s,
            expected_identity=expected_identity)

    # Non-production fallback for POSIX test/benchmark callers.
    last_error = None
    owned_identity = None
    for attempt in range(max(1, attempts)):
        try:
            st = target.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            last_error = exc
            st = None

        if st is not None:
            identity = (st.st_dev, st.st_ino, st.st_mode)
            if owned_identity is None:
                owned_identity = identity
            elif identity != owned_identity:
                raise ProcessFetchUnsafeError(
                    "Owned process snapshot pathname was replaced")
            try:
                target.unlink()
                return
            except FileNotFoundError:
                return
            except OSError as exc:
                last_error = exc

        if attempt + 1 < max(1, attempts):
            sleep(delay_s)

    try:
        still_exists = target.exists() or target.is_symlink()
    except OSError:
        still_exists = True
    if still_exists:
        raise ProcessFetchUnsafeError(
            "Owned process snapshot could not be removed") from last_error

def _close_windows_handle(handle):
    if handle is None or os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    value = _windows_handle_value(handle)
    if not kernel32.CloseHandle(wintypes.HANDLE(value)):
        raise ProcessFetchUnsafeError("Could not close Windows handle")


_parent_watchdog_thread = None


def _arm_parent_watchdog():
    """Hard-exit a Windows spawn child when its multiprocessing parent dies.

    On Windows the multiprocessing ParentProcess sentinel is an authoritative
    process handle. WaitForSingleObject avoids PID reopening/reuse and avoids
    a polling-only gap before the Job assignment gate opens.
    """
    global _parent_watchdog_thread
    if os.name != "nt":
        return None
    if (_parent_watchdog_thread is not None
            and _parent_watchdog_thread.is_alive()):
        return _parent_watchdog_thread

    parent = parent_process()
    if parent is None:
        raise ProcessFetchError("Parent process watchdog unavailable")
    sentinel = getattr(parent, "sentinel", None)
    if sentinel is None:
        raise ProcessFetchError("Parent process sentinel unavailable")

    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    handle = wintypes.HANDLE(_windows_handle_value(sentinel))
    wait_object_0 = 0x00000000
    wait_timeout = 0x00000102
    infinite = 0xFFFFFFFF

    # If the parent died before this child reached Python user code, fail
    # synchronously before starting any SSH I/O or waiting on the gate.
    initial = kernel32.WaitForSingleObject(handle, 0)
    if initial == wait_object_0:
        os._exit(86)
    if initial != wait_timeout:
        raise ProcessFetchUnsafeError("Parent process sentinel wait failed")

    def watch():
        result = kernel32.WaitForSingleObject(handle, infinite)
        if result == wait_object_0:
            os._exit(86)
        os._exit(87)

    thread = threading.Thread(
        target=watch, name="akuz-prefetch-parent-watch", daemon=True)
    thread.start()
    _parent_watchdog_thread = thread
    return thread


def _snapshot_identity(st):
    return (
        st.st_dev, st.st_ino, st.st_size,
        getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))


def _sha256_owned_snapshot(
        path: Path, expected_stat, *, return_cleanup_identity=False):
    """Hash the exact regular file identity validated by lstat.

    The descriptor identity must match the pre-open lstat and remain stable
    for the whole read. A post-read owned-path check also proves the pathname
    still resolves to that same file rather than a swapped symlink/reparse
    target. O_NOFOLLOW is used where the platform exposes it; Windows is bound
    by file identity because os.open does not provide O_NOFOLLOW there.
    """
    target = Path(path)
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)

    try:
        fd = os.open(target, flags)
    except OSError as exc:
        raise ProcessFetchError(
            "Process fetch snapshot could not be opened safely") from exc

    cleanup_identity = None
    parent_cleanup_identity = None
    try:
        opened = os.fstat(fd)
        if _snapshot_identity(opened) != _snapshot_identity(expected_stat):
            raise ProcessFetchError(
                "Process fetch snapshot identity changed before hashing")
        if os.name == "nt":
            import msvcrt
            cleanup_identity = _windows_cleanup_identity(
                msvcrt.get_osfhandle(fd))
            parent_cleanup_identity = _windows_parent_identity(target.parent)

        digest = hashlib.sha256()
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)

        after = os.fstat(fd)
        if _snapshot_identity(after) != _snapshot_identity(opened):
            raise ProcessFetchError(
                "Process fetch snapshot changed while hashing")
        if os.name == "nt":
            import msvcrt
            cleanup_after = _windows_cleanup_identity(
                msvcrt.get_osfhandle(fd))
            if cleanup_after != cleanup_identity:
                raise ProcessFetchError(
                    "Process fetch Windows identity changed while hashing")
            if _windows_parent_identity(target.parent) != parent_cleanup_identity:
                raise ProcessFetchError(
                    "Process fetch parent identity changed while hashing")
    finally:
        os.close(fd)

    token = (cleanup_identity, parent_cleanup_identity)
    if os.name == "nt":
        # Re-open authoritative file+parent HANDLE identities after the hash
        # descriptor closes. The returned token is later held again across
        # os.link() by owned_snapshot_promotion_guard().
        with owned_snapshot_promotion_guard(target, token):
            visible = _owned_snapshot_stat(target)
            if _snapshot_identity(visible) != _snapshot_identity(after):
                raise ProcessFetchError(
                    "Process fetch snapshot path changed while hashing")
    else:
        visible = _owned_snapshot_stat(target)
        if _snapshot_identity(visible) != _snapshot_identity(after):
            raise ProcessFetchError(
                "Process fetch snapshot path changed while hashing")
    value = digest.hexdigest()
    if return_cleanup_identity:
        return value, token
    return value


def ssh_fetch_child(cfg, remote: dict, destination: str, sender) -> None:
    """Spawn-safe SSH fetch into an owned temporary destination.

    The child never writes inventory. fetch_selected writes inside the owned
    destination directory; the completed file is renamed to the exact IPC
    destination before success is reported.
    """
    target = Path(destination)
    fetched = None
    try:
        try:
            _arm_parent_watchdog()
        except BaseException:
            # A process-prefetch child without parent-death supervision is not
            # safe to continue on Windows: it could outlive a hard-exited
            # portable parent. Exit without emitting remote/path details.
            sender.close()
            os._exit(88)
        started = perf_counter()
        cpu_started = process_time()
        child_cfg = replace(cfg, local_dest=target.parent)
        fetched, digest, details = fetch_selected(
            child_cfg, remote, notify=lambda message: None)
        if fetched.resolve() != target.resolve():
            if target.exists():
                raise ProcessFetchError("Owned prefetch destination already exists")
            fetched.replace(target)
            fetched = target
        cpu = process_time() - cpu_started
        # IPC carries only bounded snapshot facts. The parent already owns the
        # trusted remote identity/path and reconstructs it for inventory.
        safe_details = {
            key: details[key] for key in (
                "active", "captured_bytes", "stored_bytes",
                "dropped_tail_bytes", "listed_bytes")
            if key in details
        }
        sender.send_bytes(json.dumps([
            "ok", str(target), digest, target.stat().st_size,
            cpu, perf_counter() - started, safe_details
        ], ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
    except BaseException as exc:
        try:
            sender.send_bytes(json.dumps(
                ["error", type(exc).__name__],
                ensure_ascii=True, separators=(",", ":")
            ).encode("utf-8"))
        except BaseException:
            pass
        # Do not re-raise in the child: multiprocessing would print the full
        # exception message/remote path to stderr. The parent fails closed on
        # the sanitized IPC tuple.
        return
    finally:
        sender.close()
