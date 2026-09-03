import subprocess, sys, os

cwd = '/home/jwj/dev/taurus-stack/taurus-executor'
logdir = os.path.join(cwd, 'tests', '_logs')
os.makedirs(logdir, exist_ok=True)

cmd = [sys.executable, '-m', 'pytest', 'tests/unit/test_taurus_client.py', '-v', '--tb=short']
r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=120)
with open(os.path.join(logdir, 'taurus_client_result.log'), 'w') as f:
    f.write(r.stdout)
    f.write(r.stderr)
    f.write(f"\nEXIT={r.returncode}\n")

cmd2 = [sys.executable, '-m', 'pytest', 'tests/unit', '-v', '--tb=short', '-q']
r2 = subprocess.run(cmd2, capture_output=True, text=True, cwd=cwd, timeout=180)
with open(os.path.join(logdir, 'all_unit_result.log'), 'w') as f:
    f.write(r2.stdout)
    f.write(r2.stderr)
    f.write(f"\nEXIT={r2.returncode}\n")