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
            handle, self.handle = self.handle, None
            if handle is None:
                return
            _winapi.CloseHandle(handle)

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


    def _resume_thread(thread_handle):
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel32.ResumeThread.restype = wintypes.DWORD
        previous = kernel32.ResumeThread(wintypes.HANDLE(int(thread_handle)))
        if previous == 0xFFFFFFFF:
            raise JobBoundSpawnError(
                "Could not resume Job-bound Windows child")


    def _finalize_handles(owner, process_handle, pipe_handle):
        error = None
        try:
            owner.close()
        except BaseException as exc:
            error = exc
        for handle in (process_handle, pipe_handle):
            try:
                _winapi.CloseHandle(handle)
            except BaseException as exc:
                if error is None:
                    error = exc
        # Finalizers must not throw during interpreter shutdown.
        return error


    class JobBoundPopen:
        """CPython spawn Popen with atomic suspended Job assignment."""

        method = "spawn"

        def __init__(self, process_obj):
            prep_data = spawn.get_preparation_data(process_obj._name)
            rhandle, whandle = _winapi.CreatePipe(None, 0)
            wfd = msvcrt.open_osfhandle(whandle, 0)
            cmd = spawn.get_command_line(
                parent_pid=os.getpid(), pipe_handle=rhandle)
            python_exe = spawn.get_executable()

            if WINENV and _path_eq(python_exe, sys.executable):
                cmd[0] = python_exe = sys._base_executable
                env = os.environ.copy()
                env["__PYVENV_LAUNCHER__"] = sys.executable
            else:
                env = None

            cmd = " ".join('"%s"' % value for value in cmd)
            create_suspended = 0x00000004
            owner = _create_kill_job()
            hp = ht = None
            finalizer = None
            assigned = False

            try:
                with open(wfd, "wb", closefd=True) as to_child:
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
                    self._akuz_job_owner = owner
                    self.finalizer = util.Finalize(
                        self, _finalize_handles,
                        (owner, self.sentinel, int(rhandle)))
                    finalizer = self.finalizer

                    set_spawning_popen(self)
                    try:
                        reduction.dump(prep_data, to_child)
                        reduction.dump(process_obj, to_child)
                    finally:
                        set_spawning_popen(None)

                    _resume_thread(ht)
                    _winapi.CloseHandle(ht)
                    ht = None
            except BaseException:
                # If the standard-style Finalize object exists, let it own the
                # Job/process/pipe handles exactly once. Before that point,
                # close the Job (killing an assigned suspended child) and then
                # close the raw CreateProcess/pipe handles ourselves.
                if ht is not None:
                    try:
                        _winapi.CloseHandle(ht)
                    except BaseException:
                        pass
                if finalizer is not None:
                    try:
                        finalizer()
                    except BaseException:
                        pass
                else:
                    if hp is not None:
                        if assigned:
                            # Closing KILL_ON_JOB_CLOSE terminates the exact
                            # suspended process tree after successful assign.
                            try:
                                owner.close()
                            except BaseException:
                                try:
                                    _winapi.TerminateProcess(hp, TERMINATE)
                                except BaseException:
                                    pass
                        else:
                            # Assignment failed: the Job does not own hp yet,
                            # so terminate the exact suspended process handle
                            # directly, then close the empty Job.
                            try:
                                _winapi.TerminateProcess(hp, TERMINATE)
                            except BaseException:
                                pass
                            try:
                                owner.close()
                            except BaseException:
                                pass
                    else:
                        # CreateProcess failed before an exact child HANDLE
                        # existed. The empty kill Job is still parent-owned
                        # and must not leak.
                        try:
                            owner.close()
                        except BaseException:
                            pass
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
            self.finalizer()


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
