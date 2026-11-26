"""Each test runs the real script with a temporary HOME
and stubs for git, systemctl, curl, busctl, sudo and the apt tools on PATH.
The stubs append their command line to $STUB_LOG. The sudo stub runs its
command. The apt paths point into the temporary directory."""
import configparser
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALL = REPO / "install.sh"

LINKS = {
    ".local/bin/ghostty-tabs": str(REPO / "ghostty-tabs/ghostty-tabs"),
    ".config/systemd/user/ghostty-tabs-snapshot.service": str(REPO / "ghostty-tabs/ghostty-tabs-snapshot.service"),
    ".config/systemd/user/ghostty-tabs-snapshot.timer": str(REPO / "ghostty-tabs/ghostty-tabs-snapshot.timer"),
    ".config/autostart/ghostty-tabs-restore.desktop": str(REPO / "ghostty-tabs/ghostty-tabs-restore.desktop"),
    ".config/ghostty/config.ghostty": str(REPO / "ghostty/config.ghostty"),
}

# The old install.sh linked these to repo files that the Ghostty package now supplies.
OLD_GHOSTTY_LINKS = {
    ".local/share/dbus-1/services/com.mitchellh.ghostty.service": str(REPO / "ghostty/com.mitchellh.ghostty.service"),
    ".config/systemd/user/app-com.mitchellh.ghostty.service": str(REPO / "ghostty/app-com.mitchellh.ghostty.service"),
}

STUBS = {
    "git": 'echo "git $*" >> "$STUB_LOG"\n'
           'if [ -n "$GIT_FAIL_MAGNOLIA" ] && [[ "$*" == *magnolia-theme* ]]; then exit 1; fi\n'
           'if [ "$1" = clone ]; then mkdir -p "${@: -1}"; fi\n',
    "sudo": 'echo "sudo $*" >> "$STUB_LOG"\nexec "$@"\n',
    "add-apt-repository": 'echo "add-apt-repository $*" >> "$STUB_LOG"\n'
                          'touch "$GHOSTTY_APT_SOURCES/mkasberg-ubuntu-ghostty-ubuntu-resolute.sources"\n',
    "apt-get": 'echo "apt-get $*" >> "$STUB_LOG"\ntouch "$STUB_LOG.installed"\n',
    "dpkg-query": '[ -e "$STUB_LOG.installed" ] || exit 1\nprintf "install ok installed"\n',
    "systemctl": 'echo "systemctl $*" >> "$STUB_LOG"\n',
    "busctl": 'echo "busctl $*" >> "$STUB_LOG"\n',
    "curl": 'echo "curl $*" >> "$STUB_LOG"\n'
            'echo \'mkdir -p "$HOME/.oh-my-zsh/custom/themes" "$HOME/.oh-my-zsh/custom/plugins"\'\n',
}


class Sandbox:
    def __init__(self, tmp_path):
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.log = tmp_path / "stub.log"
        self.log.write_text("")
        bindir = tmp_path / "bin"
        bindir.mkdir()
        for name, body in STUBS.items():
            p = bindir / name
            p.write_text("#!/bin/bash\n" + body)
            p.chmod(0o755)
        self.path = f"{bindir}:/usr/bin:/bin"
        self.snap_bin = tmp_path / "snap-bin-ghostty"  # absent until a test creates it
        self.pin = tmp_path / "apt/preferences.d/ghostty-ppa"
        self.pin.parent.mkdir(parents=True)
        self.sources = tmp_path / "apt/sources.list.d"
        self.sources.mkdir()

    def run(self, **extra):
        env = {"HOME": str(self.home), "PATH": self.path, "STUB_LOG": str(self.log),
               "GHOSTTY_SNAP_BIN": str(self.snap_bin), "GHOSTTY_PIN": str(self.pin),
               "GHOSTTY_APT_SOURCES": str(self.sources), **extra}
        return subprocess.run([str(INSTALL)], env=env, capture_output=True, text=True, timeout=60)

    def calls(self):
        return self.log.read_text().splitlines()

    def link(self, rel):
        return self.home / rel


@pytest.fixture
def sb(tmp_path):
    return Sandbox(tmp_path)


def test_creates_every_link_state_dir_and_enables_timer(sb):
    r = sb.run()
    assert r.returncode == 0, r.stderr
    for rel, target in LINKS.items():
        assert os.readlink(sb.link(rel)) == target, rel
    state = sb.home / ".local/state/ghostty-tabs"
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    calls = sb.calls()
    assert "systemctl --user daemon-reload" in calls
    assert "systemctl --user enable --now ghostty-tabs-snapshot.timer" in calls


def test_ghostty_comes_from_the_ppa_after_the_pin(sb):
    r = sb.run()
    assert r.returncode == 0, r.stderr
    assert [c for c in sb.calls() if c.startswith(("sudo ", "snap "))] == [
        f"sudo install -m 644 {REPO}/ghostty/ghostty-ppa.pref {sb.pin}",
        "sudo add-apt-repository -y ppa:mkasberg/ghostty-ubuntu",
        "sudo apt-get install -y ghostty",
    ]
    assert sb.pin.read_text() == (REPO / "ghostty/ghostty-ppa.pref").read_text()


def test_second_run_changes_nothing(sb):
    assert sb.run().returncode == 0
    before = {rel: os.readlink(sb.link(rel)) for rel in LINKS}
    r = sb.run()
    assert r.returncode == 0, r.stderr
    assert {rel: os.readlink(sb.link(rel)) for rel in LINKS} == before
    assert not list(sb.home.rglob("*.bak"))


def test_second_run_does_not_call_sudo(sb):
    assert sb.run().returncode == 0
    sb.log.write_text("")
    r = sb.run()
    assert r.returncode == 0, r.stderr
    assert not [c for c in sb.calls() if c.startswith("sudo ")]


def test_old_ghostty_links_into_the_repo_are_removed(sb):
    for rel, target in OLD_GHOSTTY_LINKS.items():
        sb.link(rel).parent.mkdir(parents=True, exist_ok=True)
        sb.link(rel).symlink_to(target)
    r = sb.run()
    assert r.returncode == 0, r.stderr
    for rel in OLD_GHOSTTY_LINKS:
        assert not os.path.lexists(sb.link(rel)), rel


def test_ghostty_files_not_linked_into_the_repo_stay(sb):
    for rel in OLD_GHOSTTY_LINKS:
        sb.link(rel).parent.mkdir(parents=True, exist_ok=True)
        sb.link(rel).write_text("mine\n")
    assert sb.run().returncode == 0
    for rel in OLD_GHOSTTY_LINKS:
        assert sb.link(rel).read_text() == "mine\n", rel


def test_regular_file_at_target_moves_to_bak(sb):
    cfg = sb.link(".config/ghostty/config.ghostty")
    cfg.parent.mkdir(parents=True)
    cfg.write_text("font-size = 14\n")
    assert sb.run().returncode == 0
    assert os.readlink(cfg) == LINKS[".config/ghostty/config.ghostty"]
    assert cfg.with_name("config.ghostty.bak").read_text() == "font-size = 14\n"


def test_state_dir_mode_is_fixed_when_it_existed(sb):
    state = sb.home / ".local/state/ghostty-tabs"
    state.mkdir(parents=True)
    state.chmod(0o755)
    assert sb.run().returncode == 0
    assert stat.S_IMODE(state.stat().st_mode) == 0o700


def test_plugins_cloned_only_when_missing(sb):
    assert sb.run().returncode == 0
    clones = [c for c in sb.calls() if c.startswith("git clone") and "oh-my-zsh" in c]
    assert len(clones) == 2
    assert any("zsh-autosuggestions" in c for c in clones)
    assert any("zsh-syntax-highlighting" in c for c in clones)
    sb.log.write_text("")
    assert sb.run().returncode == 0
    assert not [c for c in sb.calls() if c.startswith("git clone") and "oh-my-zsh" in c]


def test_units_and_autostart_use_paths_without_path_lookup():
    svc = configparser.ConfigParser(interpolation=None, strict=False)
    svc.optionxform = str
    svc.read(REPO / "ghostty-tabs/ghostty-tabs-snapshot.service")
    assert svc["Service"]["ExecStart"] == "%h/.local/bin/ghostty-tabs snapshot"
    desk = (REPO / "ghostty-tabs/ghostty-tabs-restore.desktop").read_text()
    assert 'Exec=sh -c "exec ~/.local/bin/ghostty-tabs restore"' in desk.splitlines()
    assert shutil.which("desktop-file-validate"), "install desktop-file-utils to run this check"
    assert subprocess.run(["desktop-file-validate", str(REPO / "ghostty-tabs/ghostty-tabs-restore.desktop")]).returncode == 0


def test_pin_takes_only_ghostty_from_the_ppa():
    stanzas = [dict(line.split(": ", 1) for line in block.splitlines() if not line.startswith("#"))
               for block in (REPO / "ghostty/ghostty-ppa.pref").read_text().split("\n\n")]
    origin = "release o=LP-PPA-mkasberg-ghostty-ubuntu"
    assert stanzas == [
        {"Package": "*", "Pin": origin, "Pin-Priority": "1"},
        {"Package": "ghostty", "Pin": origin, "Pin-Priority": "500"},
    ]


def magnolia(sb):
    return sb.home / "dev/magnolia-theme"


def test_magnolia_theme_is_cloned_when_missing_and_linked(sb):
    assert sb.run().returncode == 0
    clone = magnolia(sb)
    assert f"git clone https://github.com/hipony/magnolia-theme.git {clone}" in sb.calls()
    assert os.readlink(sb.link(".config/ghostty/themes/Magnolia")) == str(clone / "exports/ghostty/Magnolia.ghostty")
    assert os.readlink(sb.link(".config/opencode/themes/magnolia.json")) == str(clone / "exports/opencode/magnolia.json")


def test_magnolia_theme_is_pulled_when_present(sb):
    magnolia(sb).mkdir(parents=True)
    assert sb.run().returncode == 0
    assert f"git -C {magnolia(sb)} pull --ff-only" in sb.calls()
    assert not any(c.startswith("git clone https://github.com/hipony/magnolia-theme") for c in sb.calls())


def test_opencode_theme_is_selected_and_other_keys_stay(sb):
    cli = sb.link(".config/opencode/cli.json")
    cli.parent.mkdir(parents=True)
    cli.write_text(json.dumps({"theme": {"name": "system"}, "plugins": ["-x"], "animations": True}))
    assert sb.run().returncode == 0
    data = json.loads(cli.read_text())
    assert data == {"theme": {"name": "magnolia"}, "plugins": ["-x"], "animations": True}


def test_opencode_cli_json_is_created_when_missing(sb):
    assert sb.run().returncode == 0
    assert json.loads(sb.link(".config/opencode/cli.json").read_text())["theme"] == {"name": "magnolia"}


def test_opencode_theme_that_is_not_an_object_is_replaced(sb):
    cli = sb.link(".config/opencode/cli.json")
    cli.parent.mkdir(parents=True)
    cli.write_text(json.dumps({"theme": "system", "plugins": ["-x"]}))
    r = sb.run()
    assert r.returncode == 0, r.stderr
    assert json.loads(cli.read_text()) == {"theme": {"name": "magnolia"}, "plugins": ["-x"]}


def test_opencode_theme_object_keeps_its_other_keys(sb):
    cli = sb.link(".config/opencode/cli.json")
    cli.parent.mkdir(parents=True)
    cli.write_text(json.dumps({"theme": {"name": "system", "dark": True}}))
    assert sb.run().returncode == 0
    assert json.loads(cli.read_text())["theme"] == {"name": "magnolia", "dark": True}


def test_installed_snap_ghostty_prints_a_warning(sb):
    sb.snap_bin.write_text("#!/bin/sh\n")
    sb.snap_bin.chmod(0o755)
    r = sb.run()
    assert r.returncode == 0, r.stderr
    assert "warning: the snap Ghostty is still installed; run sudo snap remove ghostty" in r.stderr


def test_no_snap_warning_without_the_snap(sb):
    r = sb.run()
    assert r.returncode == 0, r.stderr
    assert "snap" not in r.stderr


@pytest.mark.parametrize("magnolia_exists", [True, False])
def test_failed_magnolia_git_warns_and_the_install_continues(sb, magnolia_exists):
    if magnolia_exists:
        magnolia(sb).mkdir(parents=True)
    r = sb.run(GIT_FAIL_MAGNOLIA="1")
    assert r.returncode == 0, r.stderr
    assert "warning" in r.stderr and "magnolia" in r.stderr
    assert "systemctl --user enable --now ghostty-tabs-snapshot.timer" in sb.calls()


def test_regular_file_with_an_existing_bak_stops_the_install_and_changes_nothing(sb):
    cfg = sb.link(".config/ghostty/config.ghostty")
    cfg.parent.mkdir(parents=True)
    cfg.write_text("new\n")
    bak = cfg.with_name("config.ghostty.bak")
    bak.write_text("old\n")
    r = sb.run()
    assert r.returncode != 0
    assert "Cannot back up" in r.stderr
    assert not cfg.is_symlink() and cfg.read_text() == "new\n"
    assert bak.read_text() == "old\n"
