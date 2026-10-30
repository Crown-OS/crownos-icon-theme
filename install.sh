#!/usr/bin/env bash
# Installs the CrownOS-light and CrownOS-dark icon themes.
#
#   install.sh [--dest DIR]
#
#   --dest  where the themes go [default: /usr/share/icons as root, ~/.local/share/icons otherwise]

set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
light=CrownOS-light
dark=CrownOS-dark
recolored_dirs=(
    actions/{16,22,24,32,symbolic}
    apps/{16,22,32,symbolic}
    categories/{22,symbolic}
    devices/{16,22,24,32,symbolic}
    emblems/symbolic
    mimes/symbolic
    places/{16,22,24,symbolic}
    status/symbolic
)
dark_dirs=("${recolored_dirs[@]}" places/scalable)

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

dest=$HOME/.local/share/icons
((EUID != 0)) || dest=/usr/share/icons
while (($#)); do
    case $1 in
        --dest) dest=${2:?--dest needs a directory}; shift 2 ;;
        *) die "unknown argument: $1" ;;
    esac
done

write_index() {
    sed "s/^Name=.*/Name=$1/" "$root/src/index.theme" >"$dest/$1/index.theme"
}

install_light() {
    local theme=$dest/$light
    mkdir -p -- "$theme"
    cp -r -- "$root/src/." "$theme"
    rm -f -- "$theme"/places/scalable/user-trash{,-full}-dark.svg
    cp -r -- "$root/links/." "$theme"
    write_index "$light"
}

use_dark_trash() {
    local scalable=$dest/$dark/places/scalable trash
    for trash in user-trash user-trash-full; do
        mv -f -- "$scalable/$trash-dark.svg" "$scalable/$trash.svg"
    done
}

link_light_dirs() {
    local dir
    for dir in "$root"/src/*/*/; do
        dir=${dir#"$root/src/"}
        dir=${dir%/}
        [[ -e $dest/$dark/$dir ]] && continue
        mkdir -p -- "$dest/$dark/${dir%/*}"
        ln -s -- "../../$light/$dir" "$dest/$dark/$dir"
    done
}

install_dark() {
    local theme=$dest/$dark dir
    for dir in "${dark_dirs[@]}"; do
        mkdir -p -- "$theme/${dir%/*}"
        cp -r -- "$root/src/$dir" "$theme/$dir"
    done
    use_dark_trash
    for dir in "${recolored_dirs[@]}"; do
        sed -i 's/#363636/#dedede/g' "$theme/$dir"/*.svg
    done
    for dir in "${dark_dirs[@]}"; do
        cp -r -- "$root/links/$dir/." "$theme/$dir"
    done
    link_light_dirs
    write_index "$dark"
}

finish() {
    local theme=$dest/$1 context
    for context in "$theme"/*/; do
        context=${context%/}
        ln -sfn -- "${context##*/}" "$context@2x"
    done
    gtk-update-icon-cache "$theme"
}

rm -rf -- "${dest:?}/$light" "${dest:?}/$dark"
install_light
install_dark
finish "$light"
finish "$dark"
