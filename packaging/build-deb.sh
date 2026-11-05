#!/bin/sh
# Usage: packaging/build-deb.sh [version]  ->  dist/logibuntu_<version>_all.deb
set -e
cd "$(dirname "$0")/.."
VERSION=${1:-$(python3 -c 'import logibuntu; print(logibuntu.__version__)')}
VERSION=${VERSION#v}
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT
chmod 755 "$ROOT"

install -Dm644 -t "$ROOT/usr/share/logibuntu/logibuntu" logibuntu/*.py
install -Dm644 70-logibuntu.rules "$ROOT/usr/lib/udev/rules.d/70-logibuntu.rules"
install -Dm644 data/io.github.logibuntu.desktop "$ROOT/usr/share/applications/io.github.logibuntu.desktop"
install -Dm644 data/io.github.logibuntu.svg "$ROOT/usr/share/icons/hicolor/scalable/apps/io.github.logibuntu.svg"
install -d "$ROOT/usr/bin" "$ROOT/DEBIAN"
printf '#!/bin/sh\nPYTHONPATH=/usr/share/logibuntu exec python3 -m logibuntu "$@"\n' > "$ROOT/usr/bin/logibuntu"
chmod 755 "$ROOT/usr/bin/logibuntu"

cat > "$ROOT/DEBIAN/control" <<CTRL
Package: logibuntu
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0 (>= 4.10), gir1.2-adw-1 (>= 1.4)
Recommends: pipewire-bin, wireplumber
Maintainer: Simon Scabello <scabello57@gmail.com>
Homepage: https://github.com/simonscabello/logibuntu
Description: Configurador leve para mouses e headsets Logitech G
 Alternativa ao Logitech G HUB para Ubuntu: DPI, taxa de polling, botões,
 macros e iluminação gravados na memória do mouse; bateria, sidetone e
 equalizador (via PipeWire) do headset. Fala HID++ 2.0 direto via hidraw.
CTRL
# Reload udev so the uaccess rule applies to already-plugged devices.
for s in postinst postrm; do
  printf '#!/bin/sh\nset -e\nif command -v udevadm >/dev/null; then\n  udevadm control --reload || true\n  udevadm trigger --subsystem-match=hidraw --action=change || true\nfi\n' > "$ROOT/DEBIAN/$s"
  chmod 755 "$ROOT/DEBIAN/$s"
done

mkdir -p dist
dpkg-deb --root-owner-group --build "$ROOT" "dist/logibuntu_${VERSION}_all.deb"
