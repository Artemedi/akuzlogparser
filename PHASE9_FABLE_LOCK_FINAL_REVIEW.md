# Phase 9 — bounded independent Fable review of ee699af

Fable 5.1 reviewed exact clean tracked `ee699afa283f3ccf4aaf6d83ee268b7b1258884c`, five line-numbered lock/guard/test files, via user's Clean APIs on Bazzite. Second request: HTTP 200, finish_reason=stop, 4,232 prompt / 7,914 completion tokens. First broader attempt exhausted max_tokens with no text; it is NOT cited as a completed review. The unmodified useful second response is in `PHASE9_FABLE_LOCK_FINAL_RAW_REVIEW.md`; neither request included credentials or real logs.

Reviewer status on ee699af: **MODIFY** for two small lock-specific errors. This is NOT a review verdict on subsequent fix `8cb7985` or a public Release approval.

1. `exclusive_instance` was mapping **every** OS locking `OSError` to `InstanceBusy`. Applied `8cb7985`: only busy errno EACCES, EAGAIN and EDEADLK is mapped; EBADF/invalid descriptors propagate without misleading a user. Added OS-backend fault-injection regression for EBADF and EACCES, followed by successful reacquisition.
2. Process-local `_REGISTRY` key used `str(Path.resolve())`. Windows paths may alias across differing case. Applied `8cb7985`: use `os.path.normcase(str(path))` as key; Linux remains case-sensitive. Added conditional real Windows alternate-case key-identity test.
3. Concurrent hostile replacement of the checked cache/lock path remains a **hypothetical, explicitly excluded TOCTOU case**. Static symlink + NTFS junction checks were already included at ee699af; no unsupported guarantee is inferred.

Synthetic post-fix Linux 20 focused tests OK (2 expected platform skips), full Linux 162 OK (6 skips). The prior Fable MODIFY cannot be represented as Fable approving unreviewed 8cb7985; full exact-SHA Windows/runtime checks are separate evidence.
