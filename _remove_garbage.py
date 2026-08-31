"""Remove the garbage file from git index."""
import subprocess
import os

# Get all tracked files
result = subprocess.run(["git", "ls-files", "-z"], capture_output=True)
files = result.stdout.split(b"\x00")

for f in files:
    if b"ExecutionPolicy" in f or b"ScriptsActivate" in f:
        print(f"Found garbage file: {f!r}")
        r = subprocess.run(["git", "rm", "--cached", "-f", "--", f.decode("utf-8", errors="replace")], capture_output=True, text=True)
        print(f"  rm result: {r.returncode} {r.stdout.strip()} {r.stderr.strip()}")

print("Done.")
