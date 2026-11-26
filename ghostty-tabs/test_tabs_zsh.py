"""Each test runs a real `zsh -i` with a temporary HOME.

Test seam: tabs.zsh reads the namespaced variable GHOSTTY_TABS_PROC_ROOT as
the directory that holds the process files of the parent chain (default
/proc). The tests build a fake root, so no test depends on the real parent
being Ghostty. The shell reads its own start time from the real /proc.
"""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

TABS_ZSH = Path(__file__).with_name("tabs.zsh")

_loader = importlib.machinery.SourceFileLoader("ghostty_tabs", str(Path(__file__).with_name("ghostty-tabs")))
_spec = importlib.util.spec_from_loader("ghostty_tabs", _loader)
gt = importlib.util.module_from_spec(_spec)
_loader.exec_module(gt)

# Appended to .zshrc after tabs.zsh: copies the live record to $PROBE_OUT
# at each prompt, after the hooks of tabs.zsh ran.
PROBE = r"""
_probe() {
  local f=$HOME/.local/state/ghostty-tabs/live/$$.json
  [[ -f $f ]] && print -r -- "$(<$f)" >> $PROBE_OUT/prompts
}
autoload -Uz add-zsh-hook
add-zsh-hook precmd _probe
"""


class Shell:
    def __init__(self, tmp_path):
        self.cwd = tmp_path.resolve()
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.zdot = tmp_path / "zdot"
        self.zdot.mkdir()
        self.out = tmp_path / "out"
        self.out.mkdir()
        self.proc = tmp_path / "proc"
        self.proc.mkdir()
        self.live = self.home / ".local/state/ghostty-tabs/live"
        (self.zdot / ".zshrc").write_text(
            "HISTFILE=/dev/null\n"
            f"source {TABS_ZSH}\n" + PROBE
        )

    def fake_parent(self, comm="ghostty"):
        d = self.proc / str(os.getpid())
        d.mkdir(exist_ok=True)
        (d / "comm").write_text(comm + "\n")
        return d

    def fake_parent_sh_then_ghostty(self):
        sh = self.fake_parent("sh")
        (sh / "cmdline").write_bytes(b"/bin/sh\0-c\0/usr/bin/zsh\0")
        (sh / "stat").write_text("4242 (sh) S 4141 1 1")
        g = self.proc / "4141"
        g.mkdir()
        (g / "comm").write_text("ghostty\n")

    def environ(self, env=None, tab=True):
        e = {k: v for k, v in os.environ.items() if not k.startswith("GHOSTTY_")}
        e.update(HOME=str(self.home), ZDOTDIR=str(self.zdot),
                 PROBE_OUT=str(self.out))
        if tab:
            e["GHOSTTY_TABS_PROC_ROOT"] = str(self.proc)
        e.update(env or {})
        return e

    def run(self, script, env=None, tab=True):
        r = subprocess.run(["zsh", "-i"], input=script, env=self.environ(env, tab),
                           capture_output=True, text=True, timeout=30,
                           cwd=self.cwd)
        return r

    def prompts(self):
        p = self.out / "prompts"
        if not p.exists():
            return []
        return [json.loads(line) for line in p.read_text().splitlines()]


@pytest.fixture
def sh(tmp_path):
    return Shell(tmp_path)


def test_parent_ghostty_is_tab_shell(sh):
    sh.fake_parent("ghostty")
    sh.run("true\n")
    assert sh.prompts()


def test_parent_sh_c_zsh_under_ghostty_is_tab_shell(sh):
    sh.fake_parent_sh_then_ghostty()
    sh.run("true\n")
    assert sh.prompts()


@pytest.mark.parametrize("case", ["other-comm", "sh-wrong-cmdline",
                                  "sh-under-other", "real-parent"])
def test_other_parents_are_not_tab_shells(sh, case):
    tab = True
    if case == "other-comm":
        sh.fake_parent("tmux: server")
    elif case == "sh-wrong-cmdline":
        sh.fake_parent_sh_then_ghostty()
        (sh.proc / str(os.getpid()) / "cmdline").write_bytes(b"/bin/sh\0-c\0ls\0")
    elif case == "sh-under-other":
        sh.fake_parent_sh_then_ghostty()
        (sh.proc / "4141" / "comm").write_text("gnome-terminal-\n")
    else:
        tab = False  # the real parent is the test process
    r = sh.run('print -r -- "hooks=${+functions[_ghostty_tabs_precmd]}"\n', tab=tab)
    assert "hooks=0" in r.stdout
    assert not sh.live.exists()
    assert sh.prompts() == []


def test_cd_and_command_update_record(sh, tmp_path):
    sh.fake_parent()
    d = tmp_path / "work"
    d.mkdir()
    sh.run(f"cd {d}\nprint -r -- \"$(<$HOME/.local/state/ghostty-tabs/live/$$.json)\" > $PROBE_OUT/during\n")
    p = sh.prompts()
    assert p[0]["cwd"] == str(sh.cwd)
    assert p[1]["cwd"] == str(d)
    assert p[1]["running"] == ""
    during = json.loads((sh.out / "during").read_text())
    assert during["running"].startswith("print -r --")
    assert p[2]["running"] == ""
    assert set(p[0]) == {"pid", "start", "cwd", "running", "pending"}
    assert during["pid"] > 0 and during["start"] > 0


def test_hostile_text_gives_valid_json_with_original_text(sh, tmp_path):
    sh.fake_parent()
    d = tmp_path / 'we"ird\\dir\nwith\ttab'
    d.mkdir()
    cmd = ('print -r -- "$(<$HOME/.local/state/ghostty-tabs/live/$$.json)" > $PROBE_OUT/during; '
           "print -r -- 'a\"b\\c\nd\te' > /dev/null")
    sh.run(f'cd -- "$D"\n{cmd}\ntrue\n', env={"D": str(d)})
    line = (sh.out / "prompts").read_text().splitlines()
    assert all("\n" not in x for x in line)
    # json.tool accepts every line
    for x in line:
        r = subprocess.run(["python3", "-m", "json.tool"], input=x,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    p = sh.prompts()
    assert p[1]["cwd"] == str(d)
    assert json.loads((sh.out / "during").read_text())["running"] == cmd


def test_record_removed_at_exit_and_no_tmp_left(sh):
    sh.fake_parent()
    sh.run("true\n")
    assert list(sh.live.iterdir()) == []


def test_mv_and_rm_stay_external_commands(sh):
    sh.fake_parent()
    r = sh.run("print -r -- \"mv=$(whence -w mv) rm=$(whence -w rm)\"\n")
    assert "mv=mv: command" in r.stdout and "rm=rm: command" in r.stdout


def test_restored_tab_runs_command_once_with_live_record(sh):
    sh.fake_parent()
    run = ('print -r -- "$(<$HOME/.local/state/ghostty-tabs/live/$$.json)" '
           f'>> {sh.out}/during; print -r -- ran >> {sh.out}/ran')
    r = sh.run("print -r -- \"vars=${GHOSTTY_TABS_ID-u}${GHOSTTY_TABS_RUN-u}"
               "${GHOSTTY_TABS_PREFILL-u}${GHOSTTY_TABS_NOTE-u}\"\ntrue\n",
               env={"GHOSTTY_TABS_ID": "x", "GHOSTTY_TABS_RUN": run,
                    "GHOSTTY_TABS_PREFILL": "", "GHOSTTY_TABS_NOTE": "a note"})
    assert (sh.out / "ran").read_text() == "ran\n"
    during = (sh.out / "during").read_text().splitlines()
    assert len(during) == 1
    assert json.loads(during[0])["running"] == run
    assert "vars=uuuu" in r.stdout
    assert "a note" in r.stdout
    p = sh.prompts()
    assert p[0]["running"] == ""


def test_restored_tab_prefill_in_pending_and_buffer(sh):
    sh.fake_parent()
    hostile = '-rf \\ \\n "q" $(touch PWNED) ; ls\nnext line'
    r = sh.run("IFS= read -rzd '' buf\nprint -r -- \"$buf\" > $PROBE_OUT/buf\n",
               env={"GHOSTTY_TABS_ID": "x", "GHOSTTY_TABS_RUN": "",
                    "GHOSTTY_TABS_PREFILL": hostile, "GHOSTTY_TABS_NOTE": ""})
    p = sh.prompts()
    assert p[0]["pending"] == hostile
    assert p[-1]["pending"] == ""  # the next command clears it
    assert (sh.out / "buf").read_text().rstrip("\n") == hostile
    assert not (sh.cwd / "PWNED").exists()


def test_empty_home_creates_live_dir_0700_without_errors(sh):
    sh.fake_parent()
    r = sh.run("true\n")
    assert sh.prompts()
    r2 = sh.run("true\n")  # second tab shell: dir exists already
    for bad in ("no such", "permission", "zf_", "bad ", "not found"):
        assert bad not in r.stderr.lower()
        assert bad not in r2.stderr.lower()
    assert oct(sh.live.stat().st_mode & 0o777) == "0o700"


def test_subshells_do_not_touch_the_record(sh):
    sh.fake_parent()
    dump = 'print -r -- "$(<$HOME/.local/state/ghostty-tabs/live/$$.json)" >> $PROBE_OUT/during'
    sh.run(f"(cd /tmp; exit 0); {dump}\n"
           f"x=$(exit 1); {dump}\n"
           f"(exit 0); {dump}\n")
    during = [json.loads(x) for x in (sh.out / "during").read_text().splitlines()]
    assert len(during) == 3  # the record existed after each subshell
    assert all(x["cwd"] == str(sh.cwd) for x in during)


def test_failed_write_is_silent(sh):
    sh.fake_parent()
    state = sh.home / ".local/state"
    state.parent.mkdir()
    state.write_text("not a directory")
    r = sh.run("true\ntrue\n")
    assert "not a directory" not in r.stderr
    assert "_ghostty_tabs" not in r.stderr


def test_record_correct_with_hostile_shell_options(sh, tmp_path):
    sh.fake_parent()
    d = tmp_path / "dir with space {a,b} *"
    d.mkdir()
    sh.run("setopt sh_word_split glob_subst ignore_braces\n"
           'cd -- "$D"\ntrue\n', env={"D": str(d)})
    p = sh.prompts()
    assert p[-1]["cwd"] == str(d)


def test_record_start_equals_the_process_table_start_of_the_same_pid(sh):
    sh.fake_parent()
    p = subprocess.Popen(["zsh", "-i"], stdin=subprocess.PIPE, text=True, env=sh.environ(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=sh.cwd)
    try:
        record = sh.live / f"{p.pid}.json"
        for _ in range(100):
            if record.exists():
                break
            time.sleep(0.1)
        rec = json.loads(record.read_text())
        assert rec["pid"] == p.pid
        assert gt.read_process_table()[p.pid].start == rec["start"]
    finally:
        p.stdin.close()
        p.wait(timeout=30)


# ---- restore on the first window (Decision 9) ----

STUB_RESTORE = """#!/bin/bash
echo "$*" >> "$PROBE_OUT/stub_args"
cat "$PROBE_OUT/stub_out"
"""


def proc_start(pid):
    stat = Path(f"/proc/{pid}/stat").read_text()
    return int(stat[stat.rindex(")") + 2:].split()[19])


class FirstWindow:
    def __init__(self, sh):
        self.sh = sh
        sh.fake_parent()
        stub = sh.home / ".local/bin/ghostty-tabs"
        stub.parent.mkdir(parents=True)
        stub.write_text(STUB_RESTORE)
        stub.chmod(0o755)
        self.state = sh.home / ".local/state/ghostty-tabs"
        self.state.mkdir(parents=True)
        (self.state / "snapshot.json").write_text("[]")
        self.output(b"")
        self.env = {"GHOSTTY_SURFACE_ID": "0x1"}
        self.others = []

    def output(self, data):
        (self.sh.out / "stub_out").write_bytes(data)

    def other_tab_shell(self, pid, start):
        live = self.state / "live"
        live.mkdir(exist_ok=True)
        (live / f"{pid}.json").write_text(json.dumps(
            {"pid": pid, "start": start, "cwd": "/", "running": "", "pending": ""}))

    def older_tab_shell(self):
        p = subprocess.Popen(["sleep", "30"])
        self.others.append(p)
        self.other_tab_shell(p.pid, proc_start(p.pid))

    def run(self, script="pwd > $PROBE_OUT/pwd\n", env=None):
        return self.sh.run(script, env={**self.env, **(env or {})})

    def fired(self):
        args = self.sh.out / "stub_args"
        return args.read_text().splitlines() if args.exists() else []

    def pwd(self):
        return (self.sh.out / "pwd").read_text().strip()


@pytest.fixture
def fw(sh):
    f = FirstWindow(sh)
    yield f
    for p in f.others:
        p.kill()
        p.wait()


def test_first_tab_shell_takes_tab_entry_1(fw, tmp_path):
    d = tmp_path / "entry1"
    d.mkdir()
    fw.output(f"{d}\0print -r -- ran >> {fw.sh.out}/ran\0\0a note".encode())
    r = fw.run()
    assert fw.fired() == ["restore --here 0x1"]
    assert fw.pwd() == str(d)
    assert (fw.sh.out / "ran").read_text() == "ran\n"
    assert "a note" in r.stdout


def test_first_tab_shell_gets_the_prefill_of_tab_entry_1(fw, tmp_path):
    fw.output(f"{tmp_path}\0\0nvim x\0".encode())
    fw.run("IFS= read -rzd '' buf\nprint -r -- \"$buf\" > $PROBE_OUT/buf\n")
    assert (fw.sh.out / "buf").read_text().rstrip("\n") == "nvim x"
    assert fw.sh.prompts()[0]["pending"] == "nvim x"


def test_record_of_a_dead_shell_does_not_block_the_trigger(fw):
    fw.other_tab_shell(os.getpid(), proc_start(os.getpid()) + 1)  # start does not match
    fw.run()
    assert fw.fired() == ["restore --here 0x1"]


def test_younger_tab_shell_does_not_block_the_trigger(fw):
    rc = fw.sh.zdot / ".zshrc"
    rc.write_text(
        "sleep 30 < /dev/null > /dev/null 2>&1 &!\n"
        "print -r -- $! > $PROBE_OUT/younger\n"
        "zmodload zsh/system\n"
        "_s=$(</proc/$!/stat); _f=(${=_s##*) })\n"
        "mkdir -p $HOME/.local/state/ghostty-tabs/live\n"
        "print -r -- \"{\\\"pid\\\": $!, \\\"start\\\": $_f[20], \\\"cwd\\\": \\\"/\\\", "
        "\\\"running\\\": \\\"\\\", \\\"pending\\\": \\\"\\\"}\" "
        "> $HOME/.local/state/ghostty-tabs/live/$!.json\n" + rc.read_text())
    fw.run()
    os.kill(int((fw.sh.out / "younger").read_text()), 9)
    assert fw.fired() == ["restore --here 0x1"]


@pytest.mark.parametrize("case", ["restored-tab", "no-surface", "no-snapshot", "older-tab-shell"])
def test_trigger_does_not_fire(fw, case):
    env = {}
    if case == "restored-tab":
        env = {"GHOSTTY_TABS_ID": "x", "GHOSTTY_TABS_RUN": "", "GHOSTTY_TABS_PREFILL": "",
               "GHOSTTY_TABS_NOTE": ""}
    elif case == "no-surface":
        fw.env = {}
    elif case == "no-snapshot":
        (fw.state / "snapshot.json").unlink()
    else:
        fw.older_tab_shell()
    fw.run(env=env)
    assert fw.fired() == []


@pytest.mark.parametrize("data", [b"", b"/\0two\0three", b"/\0a\0b\0c\0e"])
def test_output_without_exactly_4_values_leaves_a_plain_shell(fw, data):
    fw.output(data)
    fw.run("pwd > $PROBE_OUT/pwd\ntrue\n")
    assert fw.fired() == ["restore --here 0x1"]
    assert fw.pwd() == str(fw.sh.cwd)
    assert [p["running"] for p in fw.sh.prompts()] == ["", "", ""]
