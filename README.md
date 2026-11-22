# CrownOS icon theme

The CrownOS-light and CrownOS-dark icon themes.

The app icons are designed in Figma, on the
[Icons page of CrownOS-Design](https://www.figma.com/design/A5U3QqLmbl7DnTkYuHVSy3/CrownOS-Design?node-id=188-237).
The `Figma sync` workflow exports them to `src/apps/scalable` and
`dark/apps/scalable` when a new named version is saved in Figma.
[AGENTS.md](AGENTS.md) tells you how to edit them.

## Install

```sh
./install.sh                 # to ~/.local/share/icons, or /usr/share/icons as root
./install.sh --dest DIR
```

## Release

```sh
release/make-release.sh      # writes release/crownos-icon-theme.tar.xz
```

## License

See [COPYING](COPYING).
