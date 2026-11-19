"""Stands in for `python -m assistant.cli --config <file> engine --parent-pid <pid> --password-stdin` in the
desktop app's supervisor tests (tests/supervisor.rs copies it to `assistant/cli.py` on PYTHONPATH).

The --config file's text picks the behaviour; what the fake saw is written next to that file."""
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
config = Path(args[args.index("--config") + 1])
here = config.parent
behaviour = config.read_text().strip()
(here / "args.txt").write_text(" ".join(args))
with open(here / "runs.txt", "a") as runs:
    runs.write(f"{os.getpid()}\n")
run = len((here / "runs.txt").read_text().split())

if behaviour == "crash":
    sys.exit(1)
if behaviour == "lock" and run <= 2:
    sys.exit(3)  # the engine's exit when engine.lock is held
(here / "password.txt").write_text(sys.stdin.readline().strip())  # a test password in a scratch folder
if behaviour != "silent":
    print("READY port=47999", flush=True)
if behaviour == "chatty":
    for i in range(20_000):  # both pipes: the real engine logs to stderr all day
        print(f"line {i} from an engine with a lot to say")
        print(f"log line {i} from an engine with a lot to say", file=sys.stderr)
    sys.stdout.flush()
    (here / "chatted.txt").write_text("done")
sys.stdin.read()  # the end of stdin is the stop request
if behaviour == "slow":
    time.sleep(3)  # the current job finishing
if behaviour == "stubborn":
    time.sleep(60)  # a job that hangs past the app's wait
