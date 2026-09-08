"""Mutual exclusion survives stale metadata and a killed lock supervisor."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOLDER = r'''
import os, pathlib, sys, time
root, name = pathlib.Path(sys.argv[1]), sys.argv[2]
(root / (name + '.pid')).write_text(str(os.getpid()))
critical = root / 'writing'
critical.mkdir()  # Concurrent writers fail here, regardless of log ordering.
(root / (name + '.entered')).touch()
deadline = time.monotonic() + 10
while not (root / (name + '.release')).exists():
    if time.monotonic() > deadline:
        raise SystemExit('test holder was never released')
    time.sleep(0.01)
critical.rmdir()
'''


class LiftoverProcessLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.lock = self.root / "input.vcf.gz.lock"
        self.processes = []
        self.logs = []

    def tearDown(self):
        for name in ("a", "b", "c"):
            (self.root / (name + ".release")).touch()
        for process in self.processes:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for log in self.logs:
            log.close()
        self.temp.cleanup()

    def start(self, name, *, timeout=5, command=None):
        log = (self.root / (name + ".log")).open("w")
        self.logs.append(log)
        process = subprocess.Popen(
            [sys.executable, str(ROOT / "pipeline/run_liftover_locked.py"),
             "--lock", str(self.lock), "--timeout", str(timeout), "--"]
            + (command or [sys.executable, "-c", HOLDER, str(self.root), name]),
            stdout=log, stderr=log,
        )
        self.processes.append(process)
        return process

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("timed out; " + "\n".join(p.read_text() for p in self.root.glob("*.log")))
            time.sleep(0.01)

    def entered(self, name):
        return (self.root / (name + ".entered")).exists()

    def release(self, name):
        (self.root / (name + ".release")).touch()

    def test_three_waiters_serialize_after_stale_lock(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.lock.mkdir()
        (self.lock / "pid").write_text(str(dead.pid))
        running = {name: self.start(name) for name in ("a", "b", "c")}
        while running:
            self.wait_for(lambda: any(self.entered(name) for name in running))
            active = [name for name in running if self.entered(name)]
            self.assertEqual(len(active), 1)
            name = active[0]
            time.sleep(0.15)
            self.assertEqual([n for n in running if self.entered(n)], [name])
            self.release(name)
            self.assertEqual(running.pop(name).wait(timeout=5), 0)
        self.assertFalse(self.lock.exists())
        self.assertTrue(Path(str(self.lock) + ".guard").exists())
        self.assertEqual(sum(p.read_text().count("reclaiming abandoned") for p in self.root.glob("*.log")), 1)

    def test_child_keeps_lock_after_supervisor_is_killed(self):
        first = self.start("a")
        self.wait_for(lambda: self.entered("a"))
        guard = Path(str(self.lock) + ".guard")
        inode = guard.stat().st_ino
        first.kill()
        first.wait(timeout=5)
        second = self.start("b")
        self.wait_for(lambda: "waiting for its liftover" in (self.root / "b.log").read_text())
        self.assertFalse(self.entered("b"))
        self.release("a")
        self.wait_for(lambda: self.entered("b"))
        self.release("b")
        self.assertEqual(second.wait(timeout=5), 0)
        self.assertEqual(guard.stat().st_ino, inode, "the guard inode must never be replaced")

    def test_killed_supervisor_and_child_do_not_leave_a_stale_guard(self):
        first = self.start("a")
        self.wait_for(lambda: self.entered("a"))
        child_pid = int((self.root / "a.pid").read_text())
        first.kill()
        first.wait(timeout=5)
        os.kill(child_pid, signal.SIGKILL)
        # The marker represents application work, not the locking mechanism.
        (self.root / "writing").rmdir()
        second = self.start("b")
        self.wait_for(lambda: self.entered("b"))
        self.release("b")
        self.assertEqual(second.wait(timeout=5), 0)

    def test_timeout_does_not_remove_active_lock(self):
        first = self.start("a")
        self.wait_for(lambda: self.entered("a"))
        guard = Path(str(self.lock) + ".guard")
        recorded_pid = guard.read_text()
        second = self.start("b", timeout=0.15)
        self.assertEqual(second.wait(timeout=5), 1)
        self.assertIn("gave up waiting", (self.root / "b.log").read_text())
        self.assertEqual(guard.read_text(), recorded_pid)
        self.assertFalse(self.entered("b"))
        self.release("a")
        self.assertEqual(first.wait(timeout=5), 0)

    def test_command_failure_releases_the_lock_and_preserves_status(self):
        first = self.start("a", command=[sys.executable, "-c", "raise SystemExit(23)"])
        self.assertEqual(first.wait(timeout=5), 23)
        self.assertFalse(self.lock.exists())
        second = self.start("b", command=[sys.executable, "-c", "pass"])
        self.assertEqual(second.wait(timeout=5), 0)


if __name__ == "__main__":
    unittest.main()
