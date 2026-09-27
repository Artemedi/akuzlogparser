# AKUZ Log Explorer — GitHub Actions / DBA-008D development handoff

Checkpoint: 2026-09-28. The private `Artemedi/akuzlogparser`
repository has a self-hosted Windows runner on DBA-008D. This is a
CI execution channel; it is **not** an interactive Desktop Commander
or a way to mutate the original developer checkout automatically.

## Evidence established

- [Windows runner smoke #1](https://github.com/Artemedi/akuzlogparser/actions/runs/36355653056):
  SUCCESS on SHA `b7a9106`, runner `DBA-008D`, 2.337.0,
  Windows 11 Pro, Python 3.11.9 and Node installed.
- [Self-hosted regression workflow](https://github.com/Artemedi/akuzlogparser/actions/workflows/akuz-windows-tests.yml):
  triggers on selected `main` code/test/workflow pushes and manual
  dispatch. Verifies exact `GITHUB_SHA`, DBA-008D identity, complete
  synthetic Python unittest, Node browser controls and diff check.
  It never reads source AKUZ logs or publishes a release.
- [Read-only fresh-all evidence workflow](https://github.com/Artemedi/akuzlogparser/actions/workflows/akuz-phase94-freshall-evidence.yml):
  one push on its own addition and optional manual dispatch;
  checks an ALREADY completed PRIVATE local nine-trial result.
  It does not start a new large benchmark, SSH, copy inputs, clean
  original data or upload private JSON. Only bounded numeric
  medians/ranges and PASS/FAIL may enter the private Actions log.
  Its evidence still belongs to exact experiment code SHA
  `a65007bb66196a9c24c6579151b4d557355cb45e`.

**Do not describe a queued or in-progress workflow as passing**:
inspect the final workflow conclusion, job steps and, when necessary,
decoded job logs on GitHub. A green smoke is not a full regression pass.

## Important filesystem isolation

Actions checkout lives under
`C:\actions-runner\_work\akuzlogparser\akuzlogparser`;
the earlier original developer repo and ignored diagnostic evidence
are under `E:\Software\Project\LogAkusExplorer\akuzlogparser`.
An Actions checkout **does not update the E: developer checkout**.
The evidence workflow reads the preexisting E: JSON files only.

The real benchmark uses NTFS hardlinks. Its sources and its owned
temporary root MUST reside on the **same volume**. Do NOT simply
call the benchmark from the normal C: Actions checkout while pointing
to E: downloads: `os.link` is cross-volume and will fail.
A future real-data workflow needs a separately reviewed E:-volume
disposable checkout + matching diagnostic root, exact data SHA
verification and disk-space/cleanup guards. Never silently copy
the original logs to GitHub or C: to bypass this requirement.

## Boundaries

- GitHub release remains unchanged without owner permission.
- B-lite retention authorization and normal app cache / analytics /
  SQLite / export acceptance remain OPEN.
- No API keys, `CleanApi.env`, `ConnectConf.cfg`, clinical data,
  event-level samples or raw `.log` are Actions inputs or artifacts.
- The manually started runner's persistence as a Windows service has
  NOT been demonstrated. Do not reconfigure or restart the runner
  from a job running on itself; service installation requires a
  separate local administrative step and should preserve runner
  ownership/permissions.
- Use pinned third-party Actions revisions, read-only
  `GITHUB_TOKEN`, private `main`, no untrusted PR execution on
  the host, and no automatic release publication.
