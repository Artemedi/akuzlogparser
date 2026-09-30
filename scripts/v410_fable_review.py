from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import urllib.request


def load_api_key(path: Path) -> str:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    for key in (
        "CLEANAPIS_API_KEY",
        "CLEANAPI_API_KEY",
        "CLEAN_API_KEY",
        "OPENAI_API_KEY",
        "API_KEY",
        "TOKEN",
    ):
        if values.get(key):
            return values[key]
    nonempty = [value for value in values.values() if value]
    if len(nonempty) == 1:
        return nonempty[0]
    raise RuntimeError("FABLE_CREDENTIALS=UNRESOLVED")


def main() -> int:
    env_path = Path(os.environ["FABLE_ENV_FILE"])
    if not env_path.is_file():
        raise RuntimeError("FABLE_CREDENTIALS_FILE=MISSING")
    api_key = load_api_key(env_path)

    paths = [
        "akuz_app.py",
        "akuz_parallel_reports.py",
        "akuz_store.py",
        "app_controls.js",
        "tests/test_phase15_production_parallel.py",
        "tests/test_phase16_cache_clear.py",
        "tests/test_phase16_legacy_cache.py",
        "tests/test_phase16_stale_session.py",
        "scripts/bench_phase15_normal_app.py",
        "scripts/check_phase16_real_smoke.py",
    ]
    diff = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--unified=80", "v4.9.0..HEAD", "--", *paths],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    if not diff.strip():
        raise RuntimeError("FABLE_DIFF=EMPTY")
    if len(diff) > 220_000:
        diff = diff[:220_000] + "\n[DIFF TRUNCATED AT 220000 CHARACTERS]\n"

    prompt = """You are an independent senior release reviewer for AKUZ Log Explorer v4.10.0.
Review the supplied v4.9.0..candidate diff for correctness and release safety.
Do not optimize for style. Look for concrete defects, races, unsafe cleanup,
process-lifecycle problems, cache/source identity corruption, nondeterministic
publication, compatibility regressions, or missing fail-closed behavior.

Release facts already established by executable gates:
- Phase 16 mixed/per-folder cache, stale browser listing_revision, legacy v4
  cache fallback and real cache-clear smoke passed.
- Phase 15 is Windows-only, default-OFF opt-in with
  AKUZ_PHASE15_PARALLEL_GENERATION=1; it requires 2+ selections, built-in
  generate, and Phase 12 delta disabled. While Phase 15 is active, Phase 11
  process-prefetch is disabled.
- Phase 15 real normal-app B/C/C/B/B/C: wall median 192.513628 -> 165.654935 s
  (-13.952%), CPU +1.94%, peak private +0.051%; exact outputs, analytics and
  downloads parity passed; no network during trials and no raw payload retained.
- Post-integration Windows portable and full Windows regression gates passed on
  the accepted runtime SHA.
- Public v4.9.0 is immutable; this review is for v4.10.0.

Pay special attention to:
1) Windows spawn/worker containment and failure cleanup.
2) Parent ownership, source order, validation and deterministic publication.
3) Interaction boundaries with Phase 11 and Phase 12.
4) clear_cache path ownership / symlink / custom-root fail-closed behavior.
5) listing_revision stale-tab race behavior.
6) backward compatibility and future-version fail-closed behavior.

Return exactly these four verdict lines first:
OVERALL=ACCEPT or OVERALL=BLOCK
CONCURRENCY=ACCEPT or CONCURRENCY=BLOCK
CACHE=ACCEPT or CACHE=BLOCK
RELEASE=ACCEPT or RELEASE=BLOCK

Then list findings, each tagged BLOCKER/HIGH/MEDIUM/LOW and grounded in a
specific file/function. Use BLOCK only for a credible release-blocking defect
visible in this diff, not for optional future hardening.

DIFF START
""" + diff + "\nDIFF END\n"

    payload = {
        "model": "claude-fable-5.1",
        "messages": [
            {
                "role": "system",
                "content": "Act as an independent code/release reviewer. Be precise and evidence-based.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }
    request = urllib.request.Request(
        "https://cleanapis.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "User-Agent": "akuz-v410-fable-review",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        body = json.loads(response.read().decode("utf-8"))
    review = body["choices"][0]["message"]["content"].strip()

    print("FABLE_REVIEW_BEGIN")
    print(review)
    print("FABLE_REVIEW_END")

    verdicts: dict[str, str] = {}
    for line in review.splitlines()[:12]:
        if "=" not in line:
            continue
        key, value = line.strip().split("=", 1)
        if key in {"OVERALL", "CONCURRENCY", "CACHE", "RELEASE"}:
            verdicts[key] = value.strip()
    required = {"OVERALL", "CONCURRENCY", "CACHE", "RELEASE"}
    if set(verdicts) != required:
        raise RuntimeError("FABLE_VERDICT_FORMAT=INVALID")
    if any(verdicts[key] != "ACCEPT" for key in required):
        raise RuntimeError("FABLE_RELEASE_GATE=BLOCK")
    print("FABLE_RELEASE_GATE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
