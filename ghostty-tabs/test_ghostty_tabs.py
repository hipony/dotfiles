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


# ---- restore ----

UUID = "0b7f3c1e-1111-4222-8333-444455556666"
INTEGRATION = "/snap/ghostty/current/share/ghostty/shell-integration/zsh"


class Restore:
    """Fake Ghostty, notifier and clock around gt.restore."""

    def __init__(self, tmp_path):
        self.home = tmp_path / "home"
        self.root = self.home / ".local/state/ghostty-tabs"
        self.root.mkdir(parents=True)
        self.dirs = {str(self.home)}
        self.calls = []
        self.results = []  # per-call outcomes; default True
        self.notes = []
        self.logs = []
        self.now = 0.0
        self.sleeps = []
        self.surface = "0x79c4059cee687806"
        self.surface_after = 0.0  # seconds until the first tab process exists
        self.version = "Ghostty 1.4.0\nVersion\n"
        self.version_calls = 0
        self.stderr = "ServiceUnknown"
        self.env = {}
        self.ids = iter(f"id{i}" for i in range(100))

    def write_snapshot(self, tabs, name="snapshot.json"):
        (self.root / name).write_text(json.dumps(tabs))

    def _ghostty(self, argv):
        self.calls.append(argv)
        ok = self.results.pop(0) if self.results else True
        return ok, "" if ok else self.stderr

    def _version(self):
        self.version_calls += 1
        return self.version

    def _sleep(self, s):
        self.sleeps.append(s)
        self.now += s

    def _surface(self, tab_id):
        assert tab_id == "id0"
        return self.surface if self.now >= self.surface_after else None

    def run(self, mode=None):
        return gt.restore(
            self.home, mode=mode, boot_id=BOOT, get_version=self._version, env=self.env,
            isdir=lambda p: p in self.dirs, new_id=lambda: next(self.ids),
            run_ghostty=self._ghostty, find_surface_id=self._surface,
            notify=self.notes.append, sleep=self._sleep, monotonic=lambda: self.now,
            log=self.logs.append)

    @property
    def marker(self):
        return (self.root / f"restored-{BOOT}").exists()

    def envs(self, i=0):
        """The KEY=value elements after `env` in call i, as a dict."""
        argv = self.calls[i]
        rest = argv[argv.index("env") + 2:argv.index("zsh")]  # after "env --chdir=<cwd>"
        return dict(x.split("=", 1) for x in rest)


@pytest.fixture
def rs(tmp_path):
    return Restore(tmp_path)


def tabs3(rs):
    rs.dirs |= {"/a", "/b", "/c"}
    rs.write_snapshot([
        {"kind": "claude", "cwd": "/a", "session": UUID, "prefill": ""},
        {"kind": "opencode", "cwd": "/b", "session": "", "prefill": ""},
        {"kind": "shell", "cwd": "/c", "session": "", "prefill": "nvim x"},
    ])


def test_each_kind_gets_its_run_prefill_cwd_and_zdotdir(rs):
    tabs3(rs)
    rs.run()
    assert len(rs.calls) == 3
    for index, (argv, cwd) in enumerate(zip(rs.calls, ["/a", "/b", "/c"])):
        assert f"--working-directory={cwd}" in argv
        # +new-tab ignores --working-directory (Ghostty 1.4.0); env -C sets it.
        assert argv[argv.index("env") + 1] == f"--chdir={cwd}"
        assert not any(a.startswith("--title") for a in argv)
        assert rs.envs(index)["ZDOTDIR"] == INTEGRATION
        assert rs.envs(index)["GHOSTTY_TABS_ID"]
        assert argv[-2:] == ["zsh", "-i"]
    assert rs.envs(0)["GHOSTTY_TABS_RUN"] == f"claude --resume {UUID}"
    assert rs.envs(1)["GHOSTTY_TABS_RUN"] == "opencode"
    assert rs.envs(2)["GHOSTTY_TABS_PREFILL"] == "nvim x"
    assert rs.envs(2)["GHOSTTY_TABS_RUN"] == ""
    assert rs.marker


def test_first_tab_opens_a_window_and_the_rest_join_its_surface(rs):
    tabs3(rs)
    rs.run()
    assert rs.calls[0][:2] == ["ghostty", "+new-window"]
    assert rs.calls[0][2].startswith("--working-directory")
    for argv in rs.calls[1:]:
        assert argv[:3] == ["ghostty", "+new-tab", "--surface-id=0x79c4059cee687806"]


def test_surface_wait_ends_after_10_seconds_and_tabs_open_without_surface_id(rs):
    tabs3(rs)
    rs.surface_after = 1e9
    rs.run()
    assert 10 <= rs.now < 11
    assert [a[1] for a in rs.calls] == ["+new-window", "+new-tab", "+new-tab"]
    assert not any(a.startswith("--surface-id") for argv in rs.calls for a in argv)
    assert any("surface" in m for m in rs.logs)
    assert rs.marker


def test_surface_wait_stops_as_soon_as_the_process_appears(rs):
    tabs3(rs)
    rs.surface_after = 3.0
    rs.run()
    assert rs.now < 4
    assert rs.calls[1][2] == "--surface-id=0x79c4059cee687806"


def test_user_zdotdir_goes_to_ghostty_zsh_zdotdir(rs):
    tabs3(rs)
    rs.env = {"ZDOTDIR": "/home/u/zd"}
    rs.run()
    assert rs.envs(0)["GHOSTTY_ZSH_ZDOTDIR"] == "/home/u/zd"
    assert rs.envs(0)["ZDOTDIR"] == INTEGRATION


def test_no_zdotdir_means_no_ghostty_zsh_zdotdir_and_resources_dir_is_honoured(rs):
    tabs3(rs)
    rs.env = {"GHOSTTY_RESOURCES_DIR": "/opt/g"}
    rs.run()
    assert "GHOSTTY_ZSH_ZDOTDIR" not in rs.envs(0)
    assert rs.envs(0)["ZDOTDIR"] == "/opt/g/shell-integration/zsh"


def test_bad_session_id_becomes_a_shell_tab_with_the_resume_prefill(rs):
    rs.dirs.add("/a")
    evil = "x; rm -rf ~ #"
    rs.write_snapshot([{"kind": "claude", "cwd": "/a", "session": evil, "prefill": ""}])
    rs.run()
    assert rs.envs(0)["GHOSTTY_TABS_RUN"] == ""
    assert rs.envs(0)["GHOSTTY_TABS_PREFILL"] == f"claude --resume {evil}"


def test_hostile_prefill_arrives_as_one_raw_argv_element(rs):
    rs.dirs.add("/a")
    text = 'echo "a\\"b" \'q\' $(id) ; ls\nnext line'
    rs.write_snapshot([{"kind": "shell", "cwd": "/a", "session": "", "prefill": text}])
    rs.run()
    assert f"GHOSTTY_TABS_PREFILL={text}" in rs.calls[0]


def test_missing_cwd_opens_in_home_with_a_note(rs):
    rs.write_snapshot([{"kind": "shell", "cwd": "/gone", "session": "", "prefill": "ls"}])
    rs.run()
    assert f"--working-directory={rs.home}" in rs.calls[0]
    assert "/gone" in rs.envs(0)["GHOSTTY_TABS_NOTE"]
    assert rs.envs(0)["GHOSTTY_TABS_PREFILL"] == "ls"


def test_missing_cwd_of_an_agent_tab_prefills_the_resume_command(rs):
    rs.write_snapshot([
        {"kind": "claude", "cwd": "/gone", "session": UUID, "prefill": ""},
        {"kind": "opencode", "cwd": "/gone", "session": "", "prefill": ""},
    ])
    rs.run()
    assert rs.envs(0)["GHOSTTY_TABS_RUN"] == ""
    assert rs.envs(0)["GHOSTTY_TABS_PREFILL"] == f"claude --resume {UUID}"
    assert rs.envs(1)["GHOSTTY_TABS_RUN"] == ""
    assert rs.envs(1)["GHOSTTY_TABS_PREFILL"] == "opencode"


def test_existing_marker_means_no_ghostty_call_and_force_ignores_it(rs):
    tabs3(rs)
    (rs.root / f"restored-{BOOT}").write_text("")
    rs.run()
    assert rs.calls == []
    rs.run(mode="force")
    assert len(rs.calls) == 3


def test_skip_discards_the_snapshot_and_a_later_force_opens_nothing(rs):
    tabs3(rs)
    rs.write_snapshot([], name="snapshot.prev.json")
    rs.run(mode="skip")
    assert rs.calls == []
    assert not (rs.root / "snapshot.json").exists()
    assert not (rs.root / "snapshot.prev.json").exists()
    assert (rs.root / "snapshot.skipped.json").exists()
    assert rs.marker
    rs.run(mode="force")
    assert rs.calls == []


def test_corrupt_snapshot_falls_back_to_prev_and_logs(rs):
    rs.dirs.add("/p")
    (rs.root / "snapshot.json").write_text("{not json")
    rs.write_snapshot([{"kind": "shell", "cwd": "/p", "session": "", "prefill": ""}],
                      name="snapshot.prev.json")
    rs.run()
    assert "--working-directory=/p" in rs.calls[0]
    assert rs.logs


def test_absent_or_doubly_corrupt_snapshot_writes_marker_and_exits(rs):
    rs.run()
    assert rs.calls == [] and rs.marker
    (rs.root / f"restored-{BOOT}").unlink()
    (rs.root / "snapshot.json").write_text("[")
    (rs.root / "snapshot.prev.json").write_text("")
    rs.logs.clear()
    rs.run()
    assert rs.calls == [] and rs.marker
    assert any("both snapshot files are bad" in m for m in rs.logs)


def test_first_window_failing_5_times_notifies_and_writes_no_marker(rs):
    tabs3(rs)
    rs.results = [False] * 5
    rs.run()
    assert len(rs.calls) == 5
    assert all(a[1] == "+new-window" for a in rs.calls)
    assert rs.sleeps == [2, 2, 2, 2]
    assert rs.notes and rs.logs
    assert not rs.marker


def test_first_window_succeeding_on_a_retry_continues(rs):
    tabs3(rs)
    rs.results = [False, False, True]
    rs.run()
    assert [a[1] for a in rs.calls] == ["+new-window"] * 3 + ["+new-tab"] * 2
    assert rs.marker


def test_failed_new_tab_is_logged_and_the_next_tab_continues(rs):
    tabs3(rs)
    rs.results = [True, False, True]
    rs.run()
    assert len(rs.calls) == 3
    assert rs.logs
    assert len(rs.notes) == 1 and "1" in rs.notes[0]
    assert rs.marker


def test_ghostty_before_1_4_0_notifies_and_writes_no_marker(rs):
    tabs3(rs)
    rs.version = "Ghostty 1.3.9\n"
    rs.run()
    assert rs.calls == []
    assert rs.notes and rs.logs
    assert not rs.marker


def test_failed_calls_log_the_ghostty_stderr(rs):
    tabs3(rs)
    rs.results = [False, True, False]
    rs.run()
    assert sum("ServiceUnknown" in m for m in rs.logs) == 2


def test_version_is_not_looked_up_when_marker_exists_or_on_skip(rs):
    tabs3(rs)
    (rs.root / f"restored-{BOOT}").write_text("")
    rs.run()
    rs.run(mode="skip")
    assert rs.version_calls == 0
    rs.run(mode="force")
    assert rs.version_calls == 1


def test_restore_returns_a_status_for_the_exit_code(rs):
    tabs3(rs)
    assert rs.run() == "ok"
    rs.results = [False] * 5
    assert rs.run(mode="force") == "no-window"
    rs.version = "Ghostty 1.3.0\n"
    assert rs.run(mode="force") == "ghostty-too-old"


def test_unparsable_version_counts_as_too_old(rs):
    tabs3(rs)
    rs.version = ""
    assert rs.run() == "ghostty-too-old"
    assert rs.calls == [] and not rs.marker


def test_full_argv_of_a_tab(rs):
    tabs3(rs)
    rs.run()
    assert rs.calls[2] == [
        "ghostty", "+new-tab", "--surface-id=0x79c4059cee687806",
        "--working-directory=/c", "-e", "env", "--chdir=/c",
        "GHOSTTY_TABS_ID=id2", "GHOSTTY_TABS_RUN=", "GHOSTTY_TABS_PREFILL=nvim x",
        "GHOSTTY_TABS_NOTE=", f"ZDOTDIR={INTEGRATION}", "zsh", "-i"]


def test_find_surface_id_reads_the_matching_process_environment(tmp_path):
    def environ(pid, *items):
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "environ").write_bytes(b"\0".join(i.encode() for i in items) + b"\0")

    environ(7, "HOME=/h", "GHOSTTY_TABS_ID=other", "GHOSTTY_SURFACE_ID=0x1")
    environ(8, "GHOSTTY_TABS_ID=abc", "GHOSTTY_SURFACE_ID=0x79c4059cee687806")
    (tmp_path / "self").mkdir()
    assert gt.find_surface_id("abc", proc_root=str(tmp_path)) == "0x79c4059cee687806"
    assert gt.find_surface_id("zzz", proc_root=str(tmp_path)) is None


def test_run_ghostty_timeout_does_not_log_the_argv(monkeypatch):
    def timeout(argv, **kwargs):
        raise gt.subprocess.TimeoutExpired(argv, 30)

    monkeypatch.setattr(gt.subprocess, "run", timeout)
    ok, err = gt.run_ghostty(["ghostty", "-e", "env", "GHOSTTY_TABS_PREFILL=export TOKEN=secret"])
    assert not ok
    assert "secret" not in err
    assert "TimeoutExpired" in err


def test_cwd_with_leading_dash_is_one_chdir_element():
    tab = {"cwd": "/w/-dash", "run": "", "prefill": "", "note": ""}
    argv = gt.build_ghostty_argv(tab, action="+new-tab", surface_id=None, tab_id="t",
                                 integration_dir="/i", user_zdotdir="")
    assert argv[argv.index("env") + 1] == "--chdir=/w/-dash"
