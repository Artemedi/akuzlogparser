"""Deterministic differential test: find-based classify vs last shipped regex.

Use ev['message'], not ev['raw'], because generate() classifies message.
No real AKUZ files, .git mutations, network access or patient data.
"""
from collections import Counter
import importlib.util
from pathlib import Path
import random
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from akuz_log_parser import classify, event_stream

BASELINE = "c57f7a13776ae4943004c5a350a0d166ea39ddc3"
SEED = 312776


def original(directory):
    source = subprocess.check_output(
        ["git", "show", BASELINE + ":akuz_log_parser.py"], cwd=ROOT)
    path = directory / "regex_parser.py"
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location("baseline_regex_parser", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.classify


def variants():
    atoms = [
        "notfound", "истекловремяожидания", "timedout", "timeout", "time out",
        "timed out", "not found", "не найден", "не найдена", "не найдены",
        "таймаут", "тайм-аут", "тайм\nаут", "TİMEOUT", "tımeout", "ſailure",
        "faılure", "error", "ошибка", "no_data_found", "nack", "отклонен",
        "отказ в доступе", "exception", "time", "not", "found", "не", "найден",
        "истекло", "время", "ожидания", "K", "İ", "ı", "ſ", "ß", "ẞ",
        "\u0301", "\u200b", "\u00a0",
    ]
    glue = ["", " ", "\n", "\t", "  ", "\u00a0", "\u200b",
            "x", "5", ".", "-", "_", "(", ")", "ё", "i", "İ", "ı", "ſ", "K"]
    yield from atoms
    # Targeted boundary/Unicode cases that passed ordinary random fuzz but
    # exposed semantic regressions in the uncorrected find-based prototype.
    yield from (
        "notfound", "istекловремяожидания", "истекловремяожидания",
        "таймßаут", "таймßаут rejected", "таймßаут.exception",
        "notfoundİнайден", "notfound\u00a0найден",
        "TİMEOUT", "tımeout", "тайм\nаут",
        "not\u00a0found", "не\u00a0найдена", "истекло\nвремя\tожидания",
        "timeoutошибка", "ошибкаtimeout",
    )
    rng = random.Random(SEED)
    for _ in range(130000):
        s = "".join(rng.choice(atoms) + rng.choice(glue)
                    for _ in range(rng.randint(1, 5)))
        if rng.random() < 0.4:
            s = s.upper()
        if rng.random() < 0.2:
            s = "X" * rng.randrange(100, 1000) + s
        yield s


def main():
    with TemporaryDirectory() as temp:
        directory = Path(temp)
        previous = original(directory)
        checked = 0
        for sample in variants():
            expected = previous(sample)
            actual = classify(sample)
            if actual != expected:
                raise AssertionError(
                    f"case={checked}, expected={expected}, actual={actual}, "
                    f"sample={sample[:130]!r}")
            checked += 1

        # A real event_stream() output shape, backed ONLY by a synthetic log.
        logfile = directory / "synthetic.log"
        with logfile.open("w", encoding="utf-8", newline="") as stream:
            for i in range(1800):
                stream.write(
                    f"12:00:{i%60:02d}.000,AKUZ,req{i},user: "
                    f"{['notfound', 'not found', 'timedout', 'ошибка', 'none'][i%5]}\n")
                if i % 6 == 0:
                    stream.write(" " + "X" * 25000 + "\n")
        for ev in event_stream(logfile, Counter()):
            msg = ev["message"]
            expected = previous(msg)
            actual = classify(msg)
            if actual != expected:
                raise AssertionError(
                    f"event={ev['event_id']}, expected={expected}, "
                    f"actual={actual}, message_prefix={msg[:100]!r}")
            checked += 1
        print(f"classify_equivalence=PASS checked={checked} "
              f"seed={SEED} baseline={BASELINE[:8]} event_input=message")


if __name__ == "__main__":
    main()
