"""Windows spawn context that binds a child to KILL_ON_JOB_CLOSE before resume.

The implementation mirrors CPython 3.11 multiprocessing.popen_spawn_win32
but creates the process suspended, assigns the exact CreateProcess handle to
a Job Object, serializes the normal spawn payload, then resumes the primary
thread. No child instruction can execute before Job membership exists.
"""
from __future__ import annotations

import os
import sys

if os.name == "nt":
    import ctypes
    import msvcrt
    import signal
    import _winapi
    from ctypes import wintypes
    from multiprocessing import spawn, util
    from multiprocessing.context import (
        SpawnContext, SpawnProcess, reduction, get_spawning_popen,
        set_spawning_popen)
    from multiprocessing.popen_spawn_win32 import (
        TERMINATE, WINENV, _path_eq)
else:
    SpawnContext = object
    SpawnProcess = object


class JobBoundSpawnError(RuntimeError):
    pass


if os.name == "nt":
    class _JobOwner:
        def __init__(self, handle):
            self.handle = int(handle)

        def close(self):
            handle = self.handle
            if handle is None:
                return
            # Do not relinquish ownership until CloseHandle succeeds. A
            # failed close remains retryable and can never silently orphan a
            # KILL_ON_JOB_CLOSE process tree.
            _winapi.CloseHandle(handle)
            self.handle = None

        @property
        def closed(self):
            return self.handle is None


    def _create_kill_job():
        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [
            ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise JobBoundSpawnError(
                "Could not create Windows kill Job Object")
        owner = _JobOwner(job)
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x00002000
        if not kernel32.SetInformationJobObject(
                wintypes.HANDLE(owner.handle), 9,
                ctypes.byref(info), ctypes.sizeof(info)):
            try:
                owner.close()
            finally:
                raise JobBoundSpawnError(
                    "Could not configure Windows kill Job Object")
        return owner


    def _assign_job(job_owner, process_handle):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        if not kernel32.AssignProcessToJobObject(
                wintypes.HANDLE(job_owner.handle),
                wintypes.HANDLE(int(process_handle))):
            raise JobBoundSpawnError(
                "Could not assign suspended child to Windows kill Job Object")


    def _terminate_job_and_wait(job_owner, process_handle, timeout_ms=10000):
        """Synchronously terminate an assigned child tree before error return."""
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.TerminateJobObject.argtypes = [
            wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD

        if not kernel32.TerminateJobObject(
                wintypes.HANDLE(job_owner.handle), TERMINATE):
            raise JobBoundSpawnError(
                "Could not terminate failed atomic Job-bound child tree")
        result = kernel32.WaitForSingleObject(
            wintypes.HANDLE(int(process_handle)), timeout_ms)
        if result != _winapi.WAIT_OBJECT_0:
            raise JobBoundSpawnError(
                "Failed atomic Job-bound child did not terminate in time")


    def _resume_thread(thread_handle):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel32.ResumeThread.restype = wintypes.DWORD
        previous = kernel32.ResumeThread(wintypes.HANDLE(int(thread_handle)))
        if previous == 0xFFFFFFFF:
            raise JobBoundSpawnError(
                "Could not resume Job-bound Windows child")


    def _close_handles_strict(owner, process_handle, pipe_handle):
        errors = []
        try:
            owner.close()
        except BaseException as exc:
            errors.append(exc)
        for handle in (process_handle, pipe_handle):
            if handle is None:
                continue
            try:
                _winapi.CloseHandle(handle)
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise JobBoundSpawnError(
                "Could not close atomic Job-bound spawn handles") from errors[0]


    def _finalize_handles(owner, process_handle, pipe_handle):
        # GC/interpreter-shutdown fallback only. Normal ProcessFetch lifecycle
        # calls JobBoundPopen.close() synchronously and propagates failures.
        try:
            _close_handles_strict(owner, process_handle, pipe_handle)
        except BaseException:
            pass


    class JobBoundPopen:
        """CPython spawn Popen with atomic suspended Job assignment."""

        method = "spawn"

        def __init__(self, process_obj):
            prep_data = spawn.get_preparation_data(process_obj._name)

            rhandle = whandle = None
            wfd = None
            to_child = None
            owner = None
            hp = ht = None
            finalizer = None
            assigned = False

            try:
                rhandle, whandle = _winapi.CreatePipe(None, 0)
                try:
                    wfd = msvcrt.open_osfhandle(whandle, 0)
                except BaseException:
                    # open_osfhandle did not accept ownership.
                    _winapi.CloseHandle(whandle)
                    whandle = None
                    raise
                # The CRT fd now owns whandle.
                whandle = None
                to_child = open(wfd, "wb", closefd=True)
                wfd = None

                cmd = spawn.get_command_line(
                    parent_pid=os.getpid(), pipe_handle=rhandle)
                python_exe = spawn.get_executable()

                if WINENV and _path_eq(python_exe, sys.executable):
                    cmd[0] = python_exe = sys._base_executable
                    env = os.environ.copy()
                    env["__PYVENV_LAUNCHER__"] = sys.executable
                else:
                    env = None

                # Deliberately matches CPython 3.11 popen_spawn_win32.
                cmd = " ".join('"%s"' % value for value in cmd)
                create_suspended = 0x00000004
                owner = _create_kill_job()

                hp, ht, pid, tid = _winapi.CreateProcess(
                    python_exe, cmd, None, None, False,
                    create_suspended, env, None, None)

                # Critical invariant: the exact CreateProcess handle is
                # assigned while the primary thread is still suspended.
                _assign_job(owner, hp)
                assigned = True

                self.pid = pid
                self.returncode = None
                self._handle = hp
                self.sentinel = int(hp)
                self._pipe_handle = int(rhandle)
                self._akuz_job_owner = owner
                self._closed = False
                self.finalizer = util.Finalize(
                    self, _finalize_handles,
                    (owner, self.sentinel, self._pipe_handle))
                finalizer = self.finalizer

                # Job membership is now authoritative. Resume before writing
                # the spawn payload so a payload larger than the anonymous-pipe
                # buffer cannot deadlock while the child is suspended.
                _resume_thread(ht)
                _winapi.CloseHandle(ht)
                ht = None

                set_spawning_popen(self)
                try:
                    reduction.dump(prep_data, to_child)
                    reduction.dump(process_obj, to_child)
                finally:
                    set_spawning_popen(None)

                to_child.close()
                to_child = None
            except BaseException:
                # Close the Python/CRT writer first so a resumed child cannot
                # remain blocked on an artificially open parent writer.
                if to_child is not None:
                    try:
                        to_child.close()
                    except BaseException:
                        pass
                    to_child = None
                if wfd is not None:
                    try:
                        os.close(wfd)
                    except BaseException:
                        pass
                    wfd = None
                if whandle is not None:
                    try:
                        _winapi.CloseHandle(whandle)
                    except BaseException:
                        pass
                    whandle = None
                if ht is not None:
                    try:
                        _winapi.CloseHandle(ht)
                    except BaseException:
                        pass
                    ht = None

                if finalizer is not None:
                    # The child may already have been resumed and started
                    # consuming the spawn pipe. Kill the already Job-bound tree
                    # synchronously and wait for the exact process HANDLE
                    # before releasing process/pipe ownership.
                    finalizer.cancel()
                    termination_error = None
                    if assigned and self.sentinel is not None:
                        try:
                            _terminate_job_and_wait(owner, self.sentinel)
                        except BaseException as exc:
                            termination_error = exc
                    cleanup_error = None
                    try:
                        _close_handles_strict(
                            owner, self.sentinel, self._pipe_handle)
                    except BaseException as exc:
                        cleanup_error = exc
                    self._closed = cleanup_error is None
                    if cleanup_error is None:
                        self._handle = None
                        self._pipe_handle = None
                    if termination_error is not None:
                        raise JobBoundSpawnError(
                            "Could not synchronously terminate failed atomic spawn"
                        ) from termination_error
                    if cleanup_error is not None:
                        raise JobBoundSpawnError(
                            "Could not clean failed atomic spawn handles"
                        ) from cleanup_error
                else:
                    if hp is not None:
                        if assigned:
                            # Closing KILL_ON_JOB_CLOSE terminates the exact
                            # suspended/resumed process tree.
                            try:
                                owner.close()
                            except BaseException:
                                try:
                                    _winapi.TerminateProcess(hp, TERMINATE)
                                except BaseException:
                                    pass
                        else:
                            # Assignment failed: Job does not own hp, so kill
                            # the exact suspended process handle directly.
                            try:
                                _winapi.TerminateProcess(hp, TERMINATE)
                            except BaseException:
                                pass
                            if owner is not None:
                                try:
                                    owner.close()
                                except BaseException:
                                    pass
                    elif owner is not None:
                        # Failure before CreateProcess produced hp.
                        try:
                            owner.close()
                        except BaseException:
                            pass

                    if rhandle is not None:
                        try:
                            _winapi.CloseHandle(rhandle)
                        except BaseException:
                            pass
                    if hp is not None:
                        try:
                            _winapi.CloseHandle(hp)
                        except BaseException:
                            pass
                raise

        def duplicate_for_child(self, handle):
            if self is not get_spawning_popen():
                raise AssertionError("not the active spawning Popen")
            return reduction.duplicate(handle, self.sentinel)

        def wait(self, timeout=None):
            if self.returncode is not None:
                return self.returncode
            if timeout is None:
                msecs = _winapi.INFINITE
            else:
                msecs = max(0, int(timeout * 1000 + 0.5))
            result = _winapi.WaitForSingleObject(
                int(self._handle), msecs)
            if result == _winapi.WAIT_OBJECT_0:
                code = _winapi.GetExitCodeProcess(self._handle)
                if code == TERMINATE:
                    code = -signal.SIGTERM
                self.returncode = code
            return self.returncode

        def poll(self):
            return self.wait(timeout=0)

        def terminate(self):
            if self.returncode is not None:
                return
            try:
                _winapi.TerminateProcess(
                    int(self._handle), TERMINATE)
            except PermissionError:
                code = _winapi.GetExitCodeProcess(int(self._handle))
                if code == _winapi.STILL_ACTIVE:
                    raise

        kill = terminate

        def close_job(self):
            self._akuz_job_owner.close()

        def close(self):
            if getattr(self, "_closed", False):
                return

            # Explicit close takes ownership away from the quiet GC fallback.
            # Update each raw HANDLE slot immediately after its successful
            # CloseHandle so a later retry can never double-close it.
            finalizer = getattr(self, "finalizer", None)
            if finalizer is not None and finalizer.still_active():
                finalizer.cancel()

            errors = []
            try:
                self._akuz_job_owner.close()
            except BaseException as exc:
                errors.append(exc)

            process_handle = getattr(self, "_handle", None)
            if process_handle is not None:
                try:
                    _winapi.CloseHandle(process_handle)
                except BaseException as exc:
                    errors.append(exc)
                else:
                    self._handle = None

            pipe_handle = getattr(self, "_pipe_handle", None)
            if pipe_handle is not None:
                try:
                    _winapi.CloseHandle(pipe_handle)
                except BaseException as exc:
                    errors.append(exc)
                else:
                    self._pipe_handle = None

            if errors:
                raise JobBoundSpawnError(
                    "Could not close atomic Job-bound spawn handles"
                ) from errors[0]
            self._closed = True


    class JobBoundSpawnProcess(SpawnProcess):
        @staticmethod
        def _Popen(process_obj):
            return JobBoundPopen(process_obj)


    class JobBoundSpawnContext(SpawnContext):
        Process = JobBoundSpawnProcess
        _akuz_job_bound_context = True


    _CONTEXT = JobBoundSpawnContext()


def get_job_bound_spawn_context():
    if os.name != "nt":
        raise JobBoundSpawnError(
            "Job-bound spawn context is available only on Windows")
    return _CONTEXT
