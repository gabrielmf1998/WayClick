#!/usr/bin/env bash
# Builds wayclick-<version>.noarch.rpm into dist/. Needs rpm-build.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(grep -oP '^VERSION = "\K[^"]+' wayclick.py)
TOP=$(mktemp -d)
trap 'rm -rf "$TOP"' EXIT
mkdir -p "$TOP"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}
STAGE="$TOP/wayclick-$VERSION"
mkdir -p "$STAGE"
cp -r wayclick.py LICENSE README.md README.pt-BR.md packaging "$STAGE/"
tar -C "$TOP" -czf "$TOP/SOURCES/wayclick-$VERSION.tar.gz" "wayclick-$VERSION"
sed "s/^Version:.*/Version:        $VERSION/" packaging/wayclick.spec > "$TOP/SPECS/wayclick.spec"
rpmbuild --define "_topdir $TOP" -bb "$TOP/SPECS/wayclick.spec"
mkdir -p dist
find "$TOP/RPMS" -name '*.rpm' -exec cp {} dist/ \;
echo "built: $(ls dist/*.rpm)"
