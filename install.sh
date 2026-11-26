#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# link <target> <link>: point <link> at <target>. A regular file at <link>
# moves to <link>.bak first. A link that is correct already stays unchanged.
link() {
  local target="$1" name="$2"
  mkdir -p "$(dirname "$name")"
  if [ -L "$name" ]; then
    [ "$(readlink "$name")" = "$target" ] || ln -sfn "$target" "$name"
    return
  fi
  if [ -e "$name" ]; then
    if [ -e "$name.bak" ]; then
      echo "Cannot back up $name: $name.bak exists" >&2
      exit 1
    fi
    mv "$name" "$name.bak"
    echo "Moved $name to $name.bak"
  fi
  ln -s "$target" "$name"
}

if [ ! -d "$HOME/.oh-my-zsh" ]; then
  sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended
else
  echo "Oh My Zsh already installed"
fi

ln -sf $DIR/hipony.zsh-theme ~/.oh-my-zsh/custom/themes/hipony.zsh-theme
ln -sf $DIR/zshrc ~/.zshrc

[ -d ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions ] || git clone https://github.com/zsh-users/zsh-autosuggestions ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions
[ -d ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting ] || git clone https://github.com/zsh-users/zsh-syntax-highlighting ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting

# Ghostty from ppa:mkasberg/ghostty-ubuntu. The snap Ghostty renders on the CPU
# when the NVIDIA driver drives the display.
# GHOSTTY_PIN, GHOSTTY_APT_SOURCES and GHOSTTY_SNAP_BIN let a test point these
# paths elsewhere. The pin comes first, so the PPA never has the default priority.
GHOSTTY_PIN="${GHOSTTY_PIN:-/etc/apt/preferences.d/ghostty-ppa}"
if ! cmp -s "$DIR/ghostty/ghostty-ppa.pref" "$GHOSTTY_PIN"; then
  sudo install -m 644 "$DIR/ghostty/ghostty-ppa.pref" "$GHOSTTY_PIN"
fi
if ! compgen -G "${GHOSTTY_APT_SOURCES:-/etc/apt/sources.list.d}/mkasberg-ubuntu-ghostty-ubuntu-*.sources" > /dev/null; then
  sudo add-apt-repository -y ppa:mkasberg/ghostty-ubuntu
fi
if [ "$(dpkg-query -W -f='${Status}' ghostty 2> /dev/null)" != "install ok installed" ]; then
  sudo apt-get install -y ghostty
fi
if [ -e "${GHOSTTY_SNAP_BIN:-/snap/bin/ghostty}" ]; then
  echo "warning: the snap Ghostty is still installed; run sudo snap remove ghostty" >&2
fi

link "$DIR/ghostty-tabs/ghostty-tabs" ~/.local/bin/ghostty-tabs
link "$DIR/ghostty-tabs/ghostty-tabs-snapshot.service" ~/.config/systemd/user/ghostty-tabs-snapshot.service
link "$DIR/ghostty-tabs/ghostty-tabs-snapshot.timer" ~/.config/systemd/user/ghostty-tabs-snapshot.timer
link "$DIR/ghostty-tabs/ghostty-tabs-restore.desktop" ~/.config/autostart/ghostty-tabs-restore.desktop
link "$DIR/ghostty/config.ghostty" ~/.config/ghostty/config.ghostty
# The Ghostty package supplies the D-Bus file and app-com.mitchellh.ghostty.service,
# and enables that unit for all users: Ghostty starts at login without a window.
# Remove the links of an older install.sh to repo copies of these files.
for old in ~/.local/share/dbus-1/services/com.mitchellh.ghostty.service \
           ~/.config/systemd/user/app-com.mitchellh.ghostty.service; do
  if [ -L "$old" ] && [[ "$(readlink "$old")" == "$DIR"/* ]]; then
    rm "$old"
  fi
done

MAGNOLIA="$HOME/dev/magnolia-theme"
if [ -d "$MAGNOLIA" ]; then
  git -C "$MAGNOLIA" pull --ff-only ||
    echo "warning: git pull of $MAGNOLIA failed; the theme may be out of date" >&2
else
  git clone https://github.com/hipony/magnolia-theme.git "$MAGNOLIA" ||
    echo "warning: git clone of magnolia-theme failed; the theme links stay broken" >&2
fi
link "$MAGNOLIA/exports/ghostty/Magnolia.ghostty" ~/.config/ghostty/themes/Magnolia
link "$MAGNOLIA/exports/opencode/magnolia.json" ~/.config/opencode/themes/magnolia.json
# Keep every other key of cli.json.
python3 - "$HOME/.config/opencode/cli.json" <<'PY'
import json, os, sys
path = sys.argv[1]
data = {}
if os.path.exists(path):
    with open(path) as f:
        data = json.load(f)
theme = data.get("theme")
data["theme"] = {**theme, "name": "magnolia"} if isinstance(theme, dict) else {"name": "magnolia"}
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
PY

mkdir -p ~/.local/state/ghostty-tabs
chmod 700 ~/.local/state/ghostty-tabs

# The session D-Bus keeps a removed service file until a reload.
# Best effort: a failure does not stop the install.
if command -v busctl > /dev/null; then
  busctl --user call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ReloadConfig > /dev/null ||
    echo "warning: D-Bus reload failed; Ghostty D-Bus activation works after the next login" >&2
fi

systemctl --user daemon-reload
systemctl --user enable --now ghostty-tabs-snapshot.timer
