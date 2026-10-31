#!/usr/bin/env bash
# Build the Linux release: a pure-Python wheel + sdist (installs on x86_64 and arm64 via pipx/pip),
# plus a .tar.gz with the wheel, the .desktop file, the icon and install notes. Writes SHA256SUMS.
# Usage: linux-app/packaging/build.sh [outdir]      (default: <repo>/dist)
# Needs python3 with pip (no other tools). Bundles <repo>/animations/json as the first-run library when present.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
OUT="${1:-$REPO/dist}"
VER="$(python3 -c "import re;print(re.search(r'VERSION = \"([^\"]+)\"', open('$HERE/unicorn_studio/__init__.py').read()).group(1))")"
mkdir -p "$OUT"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cp -R "$HERE" "$WORK/src"
rm -rf "$WORK/src/tests" "$WORK/src/packaging" "$WORK/src/build" "$WORK/src"/*.egg-info "$WORK/src/unicorn_studio/animations"
find "$WORK/src" -name __pycache__ -prune -exec rm -rf {} +
for f in LICENSE LICENSE-ASSETS NOTICE; do [ -f "$REPO/$f" ] && cp "$REPO/$f" "$WORK/src/$f"; done
if [ -d "$REPO/animations/json" ]; then
  mkdir -p "$WORK/src/unicorn_studio/animations"
  cp -R "$REPO/animations/json/." "$WORK/src/unicorn_studio/animations/"
  [ -f "$REPO/animations/CREDITS.md" ] && cp "$REPO/animations/CREDITS.md" "$WORK/src/unicorn_studio/animations/"
  [ -f "$REPO/animations/LICENSE.md" ] && cp "$REPO/animations/LICENSE.md" "$WORK/src/unicorn_studio/animations/"
  [ -f "$REPO/LICENSE-ASSETS" ] && cp "$REPO/LICENSE-ASSETS" "$WORK/src/unicorn_studio/animations/LICENSE-ASSETS.md"
  echo "bundling $(find "$WORK/src/unicorn_studio/animations" -name '*.json' | wc -l | tr -d ' ') animations"
else
  echo "no animations/json next to linux-app: building without bundled animations"
fi
( cd "$WORK/src" && SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1767225600}" python3 -m pip wheel --no-deps --no-build-isolation -q -w "$WORK/dist" . 2>/dev/null \
  || SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1767225600}" python3 -m pip wheel --no-deps -q -w "$WORK/dist" . )
( cd "$WORK/src" && find . -exec touch -d "@${SOURCE_DATE_EPOCH:-1767225600}" {} + 2>/dev/null && SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1767225600}" python3 -m build --sdist -o "$WORK/dist" . >/dev/null 2>&1 ) || echo "(python3 -m build not available: sdist skipped)"
WHL="$(ls "$WORK/dist"/unicorn_studio-"$VER"-py3-none-any.whl)"
PKG="$WORK/unicorn-studio-$VER-linux"
mkdir -p "$PKG"
cp "$WHL" "$PKG/"
cp "$HERE/unicorn_studio/data/unicorn-studio.desktop" "$HERE/unicorn_studio/data/unicorn-studio.svg" "$HERE/config.env.example" "$PKG/"
cp "$HERE/README.md" "$PKG/README.md"
for f in LICENSE LICENSE-ASSETS NOTICE CONTRIBUTORS.md; do [ -f "$REPO/$f" ] && cp "$REPO/$f" "$PKG/"; done
cat > "$PKG/install.sh" <<'EOS'
#!/usr/bin/env bash
# Installs Unicorn Studio for the current user with pipx (preferred) or a private venv. No root needed.
set -euo pipefail
cd "$(dirname "$0")"
WHL="$(ls unicorn_studio-*-py3-none-any.whl | head -1)"
if command -v pipx >/dev/null; then
  pipx install --force "./$WHL"
  BIN="$(pipx environment --value PIPX_BIN_DIR 2>/dev/null || echo "${PIPX_BIN_DIR:-$HOME/.local/bin}")/unicorn-studio"
else
  python3 -m venv "$HOME/.local/share/unicorn-studio/venv"
  "$HOME/.local/share/unicorn-studio/venv/bin/pip" install -q "./$WHL"
  mkdir -p "$HOME/.local/bin"
  ln -sf "$HOME/.local/share/unicorn-studio/venv/bin/unicorn-studio" "$HOME/.local/bin/unicorn-studio"
  BIN="$HOME/.local/bin/unicorn-studio"
fi
"$BIN" --install-desktop
echo "Installed. Start it from your app menu or run: unicorn-studio"
EOS
chmod +x "$PKG/install.sh"
python3 - "$WORK" "unicorn-studio-$VER-linux" "$OUT/unicorn-studio-$VER-linux.tar.gz" "${SOURCE_DATE_EPOCH:-1767225600}" <<'EOP'
import gzip, os, sys, tarfile
work, top, out, mt = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
def norm(ti):
    ti.uid = ti.gid = 0; ti.uname = ti.gname = ""; ti.mtime = mt
    ti.mode = 0o755 if (ti.isdir() or ti.mode & 0o100) else 0o644
    return ti
with open(out, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=mt, filename="") as gz, tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tf:
    for root, dirs, files in os.walk(os.path.join(work, top)):
        dirs.sort()
        rel = os.path.relpath(root, work)
        tf.add(root, rel, recursive=False, filter=norm)
        for f in sorted(files):
            tf.add(os.path.join(root, f), os.path.join(rel, f), filter=norm)
EOP
cp "$WORK/dist"/* "$OUT/"
if command -v sha256sum >/dev/null; then SHA="sha256sum"; else SHA="shasum -a 256"; fi   # macOS may lack sha256sum
( cd "$OUT" && $SHA unicorn-studio-"$VER"-linux.tar.gz unicorn_studio-"$VER"* > SHA256SUMS-linux.txt && cat SHA256SUMS-linux.txt )
