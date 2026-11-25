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

# Ghostty tab restore
if ! command -v ghostty > /dev/null; then
  snap install ghostty --classic --channel=latest/stable
fi

link "$DIR/ghostty-tabs/ghostty-tabs" ~/.local/bin/ghostty-tabs
link "$DIR/ghostty-tabs/ghostty-tabs-snapshot.service" ~/.config/systemd/user/ghostty-tabs-snapshot.service
link "$DIR/ghostty-tabs/ghostty-tabs-snapshot.timer" ~/.config/systemd/user/ghostty-tabs-snapshot.timer
link "$DIR/ghostty-tabs/ghostty-tabs-restore.desktop" ~/.config/autostart/ghostty-tabs-restore.desktop
link "$DIR/ghostty/config.ghostty" ~/.config/ghostty/config.ghostty
# D-Bus activation starts Ghostty through this unit. Do not enable it.
link "$DIR/ghostty/com.mitchellh.ghostty.service" ~/.local/share/dbus-1/services/com.mitchellh.ghostty.service
link "$DIR/ghostty/app-com.mitchellh.ghostty.service" ~/.config/systemd/user/app-com.mitchellh.ghostty.service

# Magnolia theme for Ghostty and OpenCode
MAGNOLIA="$HOME/dev/magnolia-theme"
if [ -d "$MAGNOLIA" ]; then
  git -C "$MAGNOLIA" pull --ff-only
else
  git clone https://github.com/hipony/magnolia-theme.git "$MAGNOLIA"
fi
link "$MAGNOLIA/exports/ghostty/Magnolia.ghostty" ~/.config/ghostty/themes/Magnolia
link "$MAGNOLIA/exports/opencode/magnolia.json" ~/.config/opencode/themes/magnolia.json
# Select the theme in OpenCode; keep every other key of cli.json.
python3 - "$HOME/.config/opencode/cli.json" <<'PY'
import json, os, sys
path = sys.argv[1]
data = {}
if os.path.exists(path):
    with open(path) as f:
        data = json.load(f)
data["theme"] = {**data.get("theme", {}), "name": "magnolia"}
tmp = path + ".tmp"
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
PY

mkdir -p ~/.local/state/ghostty-tabs
chmod 700 ~/.local/state/ghostty-tabs

# A running session D-Bus may not watch a service directory that was missing
# at its start. Best effort: a failure does not stop the install.
if command -v busctl > /dev/null; then
  busctl --user call org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus ReloadConfig > /dev/null ||
    echo "warning: D-Bus reload failed; Ghostty D-Bus activation works after the next login" >&2
fi

systemctl --user daemon-reload
systemctl --user enable --now ghostty-tabs-snapshot.timer
