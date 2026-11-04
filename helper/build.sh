#!/bin/sh
# Build lazymusic-helper. lazymusic picks it up on its next start; until it is
# built, everything goes through osascript as before.
#
# Needs the Swift compiler, which comes with Xcode's command line tools:
#   xcode-select --install
set -e
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$here/build"
swiftc -O -swift-version 5 -o "$here/build/lazymusic-helper" "$here/main.swift"
echo "built $here/build/lazymusic-helper"
