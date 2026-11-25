import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path

import pytest

_path = str(Path(__file__).with_name("ghostty-tabs"))
_loader = importlib.machinery.SourceFileLoader("ghostty_tabs", _path)
_spec = importlib.util.spec_from_loader("ghostty_tabs", _loader)
gt = importlib.util.module_from_spec(_spec)
_loader.exec_module(gt)

BOOT = "boot-1"
TICKS = 100
UPTIME = 100000.0  # seconds


def start_for_age(age_seconds):
    return int((UPTIME - age_seconds) * TICKS)


class Env:
    """Fake machine: state root, Claude records, process table."""

    def __init__(self, tmp_path):
        self.home = tmp_path / "home"
        self.root = self.home / ".local/state/ghostty-tabs"
        self.claude = self.home / ".claude/sessions"
        self.claude.mkdir(parents=True)
        self.table = {}
        self.cwds = {}
        self.system_state = "running"
        self.marker = True

    def proc(self, pid, ppid, comm, age=3600):
        self.table[pid] = gt.Proc(ppid, comm, start_for_age(age))

    def tab(self, pid, age=3600, cwd="/w", running="", last="", sh_pid=None):
        """A normal tab: ghostty(1) -> sh -> zsh, plus its live record."""
        self.table.setdefault(1, gt.Proc(0, "ghostty", 0))
        sh = sh_pid or pid + 1000
        self.table[sh] = gt.Proc(1, "sh", start_for_age(age))
        self.table[pid] = gt.Proc(sh, "zsh", start_for_age(age))
        self.live(pid, cwd=cwd, running=running, last=last)

    def live(self, pid, start=None, cwd="/w", running="", last=""):
        d = self.root / "live"
        d.mkdir(parents=True, exist_ok=True)
        rec = {"pid": pid, "start": self.table[pid].start if start is None else start,
               "cwd": cwd, "running": running, "last": last}
        (d / f"{pid}.json").write_text(json.dumps(rec))

    def claude_rec(self, pid, session, cwd, interactive=True):
        rec = {"pid": pid, "sessionId": session, "cwd": cwd,
               "procStart": str(self.table[pid].start)}
        if interactive:
            rec["kind"] = "interactive"
        (self.claude / f"{pid}.json").write_text(json.dumps(rec))

    def run(self):
        if self.marker:
            self.root.mkdir(parents=True, exist_ok=True)
            (self.root / f"restored-{BOOT}").write_text("")
        return gt.snapshot(
            self.home, table=self.table, proc_cwd=lambda p: self.cwds[p],
            system_state=self.system_state, boot_id=BOOT,
            uptime=UPTIME, clk_tck=TICKS)

    def snap(self):
        p = self.root / "snapshot.json"
        return json.loads(p.read_text()) if p.exists() else None


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_claude_tab_uses_session_and_agent_cwd(env):
    env.tab(100, cwd="/shell")
    env.proc(200, 100, "claude")
    env.claude_rec(200, "sess-1", "/agent")
    env.run()
    assert env.snap() == [{"kind": "claude", "cwd": "/agent", "session": "sess-1", "prefill": ""}]


def test_opencode_tab_uses_process_cwd(env):
    env.tab(100, cwd="/shell")
    env.proc(200, 100, "bash")
    env.proc(300, 200, "opencode")
    env.cwds[300] = "/oc"
    env.run()
    assert env.snap() == [{"kind": "opencode", "cwd": "/oc", "session": "", "prefill": ""}]


def test_nested_non_interactive_claude_does_not_replace_session(env):
    env.tab(100)
    env.proc(200, 100, "claude")
    env.claude_rec(200, "outer", "/a")
    env.proc(300, 200, "claude")
    env.claude_rec(300, "inner", "/b", interactive=False)
    env.proc(400, 300, "claude")  # no record at all
    env.run()
    assert env.snap()[0]["session"] == "outer"


def test_nested_interactive_claude_does_not_replace_nearest(env):
    env.tab(100)
    env.proc(200, 100, "claude")
    env.claude_rec(200, "outer", "/a")
    env.proc(300, 200, "claude")
    env.claude_rec(300, "inner", "/b")
    env.run()
    assert env.snap()[0]["session"] == "outer"


def test_non_interactive_claude_is_not_an_agent(env):
    env.tab(100, cwd="/shell", running="claude -p x")
    env.proc(200, 100, "claude")
    env.claude_rec(200, "s", "/a", interactive=False)
    env.run()
    assert env.snap() == [{"kind": "shell", "cwd": "/shell", "session": "", "prefill": "claude -p x"}]


def test_shell_prefill_running_then_last(env):
    env.tab(100, running="nvim file", last="ls")
    env.tab(101, age=3000, running="", last="make test")
    env.run()
    by_prefill = sorted(e["prefill"] for e in env.snap())
    assert by_prefill == ["make test", "nvim file"]


def test_only_digit_json_files_are_records(env):
    env.tab(100)
    live = env.root / "live"
    (live / "100.json.tmp").write_text("garbage")
    (live / ".101.json").write_text("garbage")
    (live / "abc.json").write_text("garbage")
    env.run()
    assert len(env.snap()) == 1
    assert sorted(p.name for p in live.iterdir()) == [".101.json", "100.json", "100.json.tmp", "abc.json"]


def test_missing_live_dir_gives_no_entry_no_error(env):
    env.run()
    assert env.snap() is None


@pytest.mark.parametrize("kind", ["dead", "reused"])
def test_stale_live_record_is_deleted(env, kind):
    env.tab(100)
    env.tab(101, age=3000)
    if kind == "dead":
        del env.table[100]
    else:
        env.table[100] = gt.Proc(env.table[100].ppid, "zsh", env.table[100].start + 5)
    env.run()
    assert not (env.root / "live/100.json").exists()
    assert (env.root / "live/101.json").exists()
    assert len(env.snap()) == 1


def test_entries_ordered_by_tab_shell_start(env):
    env.tab(300, age=5000, cwd="/oldest")
    env.tab(100, age=4000, cwd="/middle")
    env.tab(200, age=3000, cwd="/newest")
    env.run()
    assert [e["cwd"] for e in env.snap()] == ["/oldest", "/middle", "/newest"]


def test_live_record_of_zsh_under_ghostty_is_kept(env):
    env.table[1] = gt.Proc(0, "ghostty", 0)
    env.table[100] = gt.Proc(1, "zsh", start_for_age(3600))
    env.live(100)
    env.run()
    assert len(env.snap()) == 1


def test_no_write_while_stopping(env):
    env.tab(100)
    env.system_state = "stopping"
    env.run()
    assert env.snap() is None


def test_no_write_without_tab_shell_keeps_old_snapshot(env):
    env.root.mkdir(parents=True)
    (env.root / "snapshot.json").write_text('[{"old": 1}]')
    env.run()
    assert env.snap() == [{"old": 1}]
    assert not (env.root / "snapshot.prev.json").exists()


def test_no_write_without_marker_when_oldest_tab_is_young(env):
    env.marker = False
    env.tab(100, age=599)
    env.run()
    assert env.snap() is None


def test_write_without_marker_when_oldest_tab_is_ten_minutes_old(env):
    env.marker = False
    env.tab(100, age=600)
    env.tab(101, age=10)
    env.run()
    assert len(env.snap()) == 2


def test_write_moves_old_snapshot_to_prev(env):
    env.tab(100)
    env.root.mkdir(parents=True, exist_ok=True)
    (env.root / "snapshot.json").write_text('[{"old": 1}]')
    env.run()
    assert json.loads((env.root / "snapshot.prev.json").read_text()) == [{"old": 1}]
    assert len(env.snap()) == 1
    assert not [p for p in env.root.iterdir() if p.name.endswith(".tmp")]


def test_failed_write_keeps_old_snapshot(env, monkeypatch):
    env.tab(100)
    env.root.mkdir(parents=True, exist_ok=True)
    (env.root / "snapshot.json").write_text('[{"old": 1}]')

    def power_loss(path, data):
        raise OSError("power loss")

    monkeypatch.setattr(gt, "write_json_atomic", power_loss)
    with pytest.raises(OSError):
        env.run()
    assert env.snap() == [{"old": 1}]


def test_claude_record_without_session_is_absent(env):
    env.tab(100, cwd="/shell")
    env.proc(200, 100, "claude")
    (env.claude / "200.json").write_text(json.dumps({"kind": "interactive", "cwd": "/a"}))
    env.run()
    assert env.snap() == [{"kind": "shell", "cwd": "/shell", "session": "", "prefill": ""}]


def test_vanished_opencode_process_gives_shell_entry(env):
    env.tab(100, cwd="/shell")
    env.proc(200, 100, "opencode")

    def gone(pid):
        raise FileNotFoundError(pid)

    env.cwds = None
    gt_run = gt.snapshot(
        env.home, table=env.table, proc_cwd=gone, system_state="running",
        boot_id="no-marker-needed", uptime=UPTIME, clk_tck=TICKS)
    assert gt_run is True
    assert env.snap()[0]["kind"] == "shell"


def test_live_record_without_cwd_is_deleted(env):
    env.tab(100)
    d = env.root / "live"
    (d / "100.json").write_text(json.dumps({"pid": 100, "start": env.table[100].start}))
    env.run()
    assert env.snap() is None
    assert not (d / "100.json").exists()
