"""Tests for install.sh. Each test runs the real script with a temporary HOME
and stubs for git, snap, systemctl, curl and busctl on PATH. The stubs append
their command line to $STUB_LOG."""
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
    ".local/share/dbus-1/services/com.mitchellh.ghostty.service": str(REPO / "ghostty/com.mitchellh.ghostty.service"),
    ".config/systemd/user/app-com.mitchellh.ghostty.service": str(REPO / "ghostty/app-com.mitchellh.ghostty.service"),
}

STUBS = {
    "git": 'echo "git $*" >> "$STUB_LOG"\n'
           'if [ "$1" = clone ]; then mkdir -p "${@: -1}"; fi\n',
    "snap": 'echo "snap $*" >> "$STUB_LOG"\n',
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

    def run(self):
        env = {"HOME": str(self.home), "PATH": self.path, "STUB_LOG": str(self.log)}
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
    assert any(c.startswith("snap install ghostty --classic --channel=latest/stable") for c in calls)


def test_second_run_changes_nothing(sb):
    assert sb.run().returncode == 0
    before = {rel: os.readlink(sb.link(rel)) for rel in LINKS}
    r = sb.run()
    assert r.returncode == 0, r.stderr
    assert {rel: os.readlink(sb.link(rel)) for rel in LINKS} == before
    assert not list(sb.home.rglob("*.bak"))


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
    tm = configparser.ConfigParser(interpolation=None, strict=False)
    tm.optionxform = str
    tm.read(REPO / "ghostty-tabs/ghostty-tabs-snapshot.timer")
    assert tm["Timer"]["OnBootSec"] == "2min"
    assert tm["Timer"]["OnUnitActiveSec"] == "60s"
    assert tm["Timer"]["AccuracySec"] == "1s"
    desk = (REPO / "ghostty-tabs/ghostty-tabs-restore.desktop").read_text()
    assert 'Exec=sh -c "exec ~/.local/bin/ghostty-tabs restore"' in desk.splitlines()
    assert shutil.which("desktop-file-validate"), "install desktop-file-utils to run this check"
    assert subprocess.run(["desktop-file-validate", str(REPO / "ghostty-tabs/ghostty-tabs-restore.desktop")]).returncode == 0


def test_ghostty_files_use_absolute_snap_path_and_dbus_names():
    exe = "/snap/bin/ghostty --gtk-single-instance=true --initial-window=false"
    dbus = configparser.ConfigParser(interpolation=None, strict=False)
    dbus.optionxform = str
    dbus.read(REPO / "ghostty/com.mitchellh.ghostty.service")
    assert dbus["D-BUS Service"]["Name"] == "com.mitchellh.ghostty"
    assert dbus["D-BUS Service"]["SystemdService"] == "app-com.mitchellh.ghostty.service"
    assert dbus["D-BUS Service"]["Exec"] == exe
    unit = configparser.ConfigParser(interpolation=None, strict=False)
    unit.optionxform = str
    unit.read(REPO / "ghostty/app-com.mitchellh.ghostty.service")
    assert unit["Service"]["ExecStart"] == exe
    assert unit["Service"]["BusName"] == "com.mitchellh.ghostty"


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


def test_ghostty_config_selects_magnolia():
    assert "theme = Magnolia" in (REPO / "ghostty/config.ghostty").read_text().splitlines()
