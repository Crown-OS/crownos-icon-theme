#!/usr/bin/env bash
# Packs CrownOS-light and CrownOS-dark into crownos-icon-theme.tar.xz next to this script.

set -euo pipefail

release=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
themes=(CrownOS-light CrownOS-dark)

"$release/../install.sh" --dest "$release"
tar -C "$release" -cJf "$release/crownos-icon-theme.tar.xz" "${themes[@]}"
rm -rf -- "${themes[@]/#/$release/}"
