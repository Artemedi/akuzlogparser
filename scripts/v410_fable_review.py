from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PATH = Path(
    r"E:\Software\Project\LogAkusExplorer\secrets\fable_review_v410_private.pem"
)
ENCRYPTED_PATH = ROOT / ".github" / "fable_review_v410.enc.b64"


def decrypt_environment() -> dict[str, str]:
    if not PRIVATE_PATH.is_file():
        raise RuntimeError("FABLE_EPHEMERAL_PRIVATE_KEY=MISSING")
    if not ENCRYPTED_PATH.is_file():
        raise RuntimeError("FABLE_ENCRYPTED_CREDENTIAL=MISSING")

    private = serialization.load_pem_private_key(
        PRIVATE_PATH.read_bytes(), password=None
    )
    ciphertext = base64.b64decode(
        ENCRYPTED_PATH.read_text(encoding="ascii").strip()
    )
    plaintext = private.decrypt(
        ciphertext,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    ).decode("utf-8")

    values: dict[str, str] = {}
    for raw in plaintext.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    if not values.get("ANTHROPIC_AUTH_TOKEN"):
        raise RuntimeError("FABLE_TOKEN=MISSING")
    if not values.get("ANTHROPIC_BASE_URL"):
        raise RuntimeError("FABLE_BASE_URL=MISSING")
    return values


def candidate_diff() -> str:
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
        ["git", "diff", "--no-ext-diff", "--unified=80",
         "v4.9.0..HEAD", "--", *paths],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout
    if not diff.strip():
        raise RuntimeError("FABLE_DIFF=EMPTY")
    if len(diff) > 220_000:
        diff = diff[:220_000] + "\n[DIFF TRUNCATED AT 220000 CHARACTERS]\n"
    return diff


def call_fable(values: dict[str, str], prompt: str) -> str:
    token = values["ANTHROPIC_AUTH_TOKEN"]
    base = values["ANTHROPIC_BASE_URL"].rstrip("/")
    model = values.get("ANTHROPIC_MODEL", "claude-fable-5.1")

    payload = json.dumps(
        {
            "model": model,
            "max_tokens": 7000,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Act as an independent senior code and release reviewer. "
                        "Be precise, skeptical and evidence-based."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".json", delete=False
        ) as request_file:
            request_file.write(payload)
            request_path = request_file.name

        curl_config = (
            'url = "' + base + '/chat/completions"\n'
            'header = "Authorization: Bearer ' + token + '"\n'
            'header = "Content-Type: application/json"\n'
            'header = "User-Agent: curl/8"\n'
            'data-binary = "@' + request_path.replace("\\", "/") + '"\n'
        )
        proc = subprocess.run(
            [
                "curl", "--silent", "--show-error", "--fail",
                "--max-time", "300", "--config", "-"
            ],
            input=curl_config.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=310,
        )
        if proc.returncode:
            raise RuntimeError(
                "FABLE_CURL_FAILED=" + str(proc.returncode) + ":"
                + proc.stderr.decode("utf-8", errors="replace")[:500]
            )
        data = json.loads(proc.stdout.decode("utf-8"))
    finally:
        if request_path:
            Path(request_path).unlink(missing_ok=True)

    content = data["choices"][0]["message"]["content"]
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("FABLE_REVIEW=EMPTY")
    return content.strip()


def main() -> int:
    values = decrypt_environment()
    diff = candidate_diff()
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()

    prompt = f"""You are the independent senior release reviewer for AKUZ Log Explorer v4.10.0.
Review candidate SHA {sha}, specifically the supplied runtime/test diff from
public v4.9.0 to this candidate. Do not optimize for style. Look for concrete
release defects, races, unsafe cleanup, process-lifecycle problems,
cache/source-identity corruption, nondeterministic publication, compatibility
regressions, or missing fail-closed behavior.

Established executable evidence:
- Phase 16 mixed/per-folder cache correctness, stale-browser listing_revision,
  legacy v4 cache fallback and real cache-clear smoke passed.
- Phase 15 is Windows-only and default-OFF, enabled only by
  AKUZ_PHASE15_PARALLEL_GENERATION=1; it requires 2+ selections, built-in
  generate and Phase 12 delta disabled. While active it disables Phase 11
  process-prefetch.
- Phase 15 real normal-app B/C/C/B/B/C on 956,307,242 source bytes / 4 reports:
  median wall 192.513628 -> 165.654935 s (-13.952%), CPU +1.94%, peak private
  memory +0.051%; exact report parity, analytics parity and downloads
  invariants passed; no network during trials and no raw payload retained.
- Post-integration Windows regression and portable gates passed on the accepted
  runtime lineage. Final exact-SHA gates will be rerun after this review.
- Public v4.9.0 is immutable. This review is for v4.10.0.

Focus especially on:
1) Windows spawn/worker containment, cancellation and failure cleanup.
2) Parent ownership, source ordering, staged-output validation and
   deterministic publication.
3) Phase 15 boundaries with Phase 11 and Phase 12.
4) clear_cache ownership / symlink / custom-root fail-closed behavior.
5) listing_revision stale-tab race behavior.
6) backward compatibility and future-version fail-closed behavior.

Return exactly these four verdict lines first:
OVERALL=ACCEPT or OVERALL=BLOCK
CONCURRENCY=ACCEPT or CONCURRENCY=BLOCK
CACHE=ACCEPT or CACHE=BLOCK
RELEASE=ACCEPT or RELEASE=BLOCK

Then list findings, each tagged BLOCKER/HIGH/MEDIUM/LOW and grounded in a
specific file/function. Use BLOCK only for a credible release-blocking defect
visible in the supplied code.

DIFF START
{diff}
DIFF END
"""

    try:
        review = call_fable(values, prompt)
    finally:
        PRIVATE_PATH.unlink(missing_ok=True)

    print("FABLE_REVIEW_BEGIN")
    print(review)
    print("FABLE_REVIEW_END")
    print("FABLE_EPHEMERAL_PRIVATE_KEY_DESTROYED=PASS")
    print("NO_PRIVATE_LOGS_OR_SECRETS_SENT=PASS")

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
