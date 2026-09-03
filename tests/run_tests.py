import subprocess, sys, os

cwd = '/home/jwj/dev/taurus-stack/taurus-executor'
logdir = os.path.join(cwd, 'tests', '_logs')
os.makedirs(logdir, exist_ok=True)

cmd = [sys.executable, '-m', 'pytest', 'tests/unit', '--tb=short', '-q']
r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
with open(os.path.join(logdir, 'summary.log'), 'w') as f:
    f.write(f"EXIT={r.returncode}\n")
    f.write(f"STDOUT:\n{r.stdout}\n")
    f.write(f"STDERR:\n{r.stderr}\n")
print(f"Done. Exit={r.returncode}")
print(f"Stdout: {r.stdout}")