#!/usr/bin/env bash
# Fork-only helper (not upstream code).
#
# Rebuild the macOS Electron desktop bundle from the SAME revision as the
# installed Homebrew CLI, so the desktop app and the CLI never drift apart.
#
# Why this exists: `brew upgrade` only refreshes the CLI. The desktop bundle at
# "$HERMES_HOME/hermes-agent/apps/desktop/release/mac-arm64/Hermes.app" is built
# from source by the bootstrap installer, so it silently stays at whatever
# revision it was first built from (and then talks to a much newer backend).
#
# Usage:
#   scripts/rebuild-desktop-macos.sh [<tag-or-commit>]
#
#   With no argument it uses the newest tag reachable from HEAD (i.e. the tag a
#   `brew upgrade` of this tap just installed). Pass a tag to pin explicitly.
#
# Requirements: macOS, node/npm, a clean checkout of this repo.
#
# NOTE: this builds whatever revision you point it at. Only run it when that
# revision's desktop actually works — a known upstream regression crashes the
# workspace contribution at startup (see the fork's notes), so verify before
# replacing a working bundle. The previous bundle is kept as
# `Hermes.app.replaced-<timestamp>`; delete it once the new one is verified.

set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
HERMES_HOME=${HERMES_HOME:-$HOME/.hermes}
INSTALL_ROOT="$HERMES_HOME/hermes-agent"
DESKTOP_DIR="$INSTALL_ROOT/apps/desktop"
BUNDLE_DIR="$DESKTOP_DIR/release/mac-arm64"
APP="$BUNDLE_DIR/Hermes.app"

die() { echo "✗ $*" >&2; exit 1; }
info() { echo "→ $*"; }

[ "$(uname -s)" = "Darwin" ] || die "this helper only builds the macOS bundle"

command -v node >/dev/null || die "node not found on PATH"
command -v npm  >/dev/null || die "npm not found on PATH"

[ -d "$REPO_ROOT/.git" ] || die "$REPO_ROOT is not a git checkout"
[ -d "$INSTALL_ROOT" ] || die "$INSTALL_ROOT not found (is the desktop installed?)"

git -C "$REPO_ROOT" diff --quiet && git -C "$REPO_ROOT" diff --cached --quiet \
  || die "$REPO_ROOT has uncommitted changes — commit or stash them first"

info "fetching tags"
git -C "$REPO_ROOT" fetch --tags origin >/dev/null

REF=${1:-}
if [ -z "$REF" ]; then
  REF=$(git -C "$REPO_ROOT" describe --tags --abbrev=0)
fi
git -C "$REPO_ROOT" rev-parse --verify --quiet "$REF^{commit}" >/dev/null \
  || die "unknown revision: $REF"
COMMIT=$(git -C "$REPO_ROOT" rev-parse "$REF^{commit}")

ORIGINAL=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)
restore() {
  if [ "$ORIGINAL" != "HEAD" ]; then
    git -C "$REPO_ROOT" checkout --quiet "$ORIGINAL" || true
  fi
}
trap restore EXIT

info "checking out $REF ($COMMIT)"
git -C "$REPO_ROOT" checkout --quiet --detach "$COMMIT"

# The repo's committed lockfile covers the root workspace only, so the desktop's
# devDependencies (electron, electron-builder, …) are not in it. npm also inherits
# `omit=dev` from some environments, which would silently skip them — hence both
# installs below. `--no-package-lock` keeps the repo's lockfile untouched.
info "installing root node dependencies"
( cd "$REPO_ROOT" && npm ci --no-audit --no-fund )

info "installing desktop node dependencies (incl. devDependencies)"
( cd "$REPO_ROOT" && npm install -w apps/desktop --include=dev --no-package-lock --no-audit --no-fund )

info "packaging the desktop app"
( cd "$REPO_ROOT/apps/desktop" \
  && CSC_IDENTITY_AUTO_DISCOVERY=false \
     ELECTRON_MIRROR=${ELECTRON_MIRROR:-https://npmmirror.com/mirrors/electron/} \
     npm run pack )

BUILT="$REPO_ROOT/apps/desktop/release/mac-arm64/Hermes.app"
[ -d "$BUILT" ] || die "build produced no bundle at $BUILT"

VERSION=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$BUILT/Contents/Info.plist")
info "built Hermes.app $VERSION from $REF"

info "quitting the running desktop (if any)"
pkill -f "$APP/Contents/MacOS/Hermes" 2>/dev/null || true
sleep 1

if [ -d "$APP" ]; then
  KEEP="$BUNDLE_DIR/Hermes.app.replaced-$(date +%Y%m%d-%H%M%S)"
  info "keeping the previous bundle at $(basename "$KEEP")"
  mv "$APP" "$KEEP"
fi

info "installing the rebuilt bundle"
/usr/bin/ditto "$BUILT" "$APP"

# Build stamp the desktop reports, and the launcher's "install completed" marker
# (existence-only for the launcher; schema-checked by the desktop).
mkdir -p "$DESKTOP_DIR/build"
if [ -f "$REPO_ROOT/apps/desktop/build/install-stamp.json" ]; then
  cp "$REPO_ROOT/apps/desktop/build/install-stamp.json" "$DESKTOP_DIR/build/install-stamp.json"
fi
HERMES_HOME="$HERMES_HOME" COMMIT="$COMMIT" VERSION="$VERSION" \
python3 - <<'PY'
import json, os, pathlib, time
home = pathlib.Path(os.environ["HERMES_HOME"])
marker = home / "hermes-agent/.hermes-bootstrap-complete"
data = json.loads(marker.read_text()) if marker.exists() else {"schemaVersion": 1}
data.update({
    "schemaVersion": 1,
    "pinnedCommit": os.environ["COMMIT"],
    "pinnedBranch": "main",
    "completedAtUnix": int(time.time()),
    "completedAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
    "desktopVersion": os.environ["VERSION"],
})
marker.write_text(json.dumps(data, indent=2) + "\n")
print("→ marker pinned to", os.environ["COMMIT"][:12])
PY

info "relaunching via /Applications/Hermes.app"
open /Applications/Hermes.app || info "open the app manually"

cat <<EOF

✓ Desktop bundle updated to $VERSION ($COMMIT).

Verify it actually boots before trusting it:
  tail -f "$HERMES_HOME/logs/desktop.log"
and check for repeated "Maximum update depth exceeded" / "renderer crash".
If it is broken, restore the previous bundle:
  rm -rf "$APP" && mv "$BUNDLE_DIR"/Hermes.app.replaced-* "$APP"
EOF
