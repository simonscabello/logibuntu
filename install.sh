#!/bin/sh
# Run from a source checkout: udev rule (needs sudo) + menu launcher and icon for the current user.
# Prefer the .deb from the Releases page for a normal install.
set -e
DIR=$(cd "$(dirname "$0")" && pwd)

sudo cp "$DIR/70-logibuntu.rules" /etc/udev/rules.d/
sudo udevadm control --reload
sudo udevadm trigger --subsystem-match=hidraw --action=change

install -Dm644 "$DIR/data/io.github.logibuntu.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/io.github.logibuntu.svg"
mkdir -p "$HOME/.local/share/applications"
sed "s#^Exec=.*#Exec=python3 -m logibuntu\nPath=$DIR#" "$DIR/data/io.github.logibuntu.desktop" \
  > "$HOME/.local/share/applications/io.github.logibuntu.desktop"
echo "Pronto: procure 'Logibuntu' no menu de aplicativos."
