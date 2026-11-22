# Editing the CrownOS icons in Figma

The app icons come from the **Icons** page of the CrownOS-Design file:
<https://www.figma.com/design/A5U3QqLmbl7DnTkYuHVSy3/CrownOS-Design?node-id=188-237>

- File key: `A5U3QqLmbl7DnTkYuHVSy3`
- Icons page: `188:237`

## Rules

- Make icon changes in Figma with the Figma MCP (`use_figma`). Load the
  `figma-use` skill before the first `use_figma` call. If the skill is not
  installed, read `skill://figma/figma-use/SKILL.md` from the MCP server.
- Do not edit `src/apps/scalable/` or `dark/apps/scalable/` by hand. The
  Figma sync writes these folders. A hand edit is lost at the next sync.
- Update the local files from Figma only when the user asks. See
  [Getting changes into the repository](#getting-changes-into-the-repository).
- Do not save a named version in Figma. Only the user does this, because a
  named version starts a sync pull request.

## Page layout

| Section | Grid node | Exported to |
| --- | --- | --- |
| Light Icons > CrownOS Apps | `352:242` | `src/apps/scalable` |
| Light Icons > Third Party Icons | `191:263` | `src/apps/scalable` |
| Dark Icons > CrownOS Apps | `352:248` | `dark/apps/scalable` |
| Dark Icons > Dark Icons | `191:269` | `dark/apps/scalable` |

`figma/config.json` holds the same list. If you add, move or remove a grid,
update `figma/config.json` too.

Each grid is a wrapping auto-layout frame. Each child is one tile.

## Tile structure

```
<icon-name>            FRAME 160x159, vertical auto-layout   <- the tile (grid child)
├── <icon-name>        FRAME 128x128                         <- this frame is exported as SVG
│   ├── Vector         drop shadow (layer blur)
│   ├── Clip path group / Artwork
│   │   ├── *-crwn-clip or Plate mask   circle mask
│   │   └── artwork    Lens, Logo, Traced art, Glyph mask, Shadow ...
│   └── Vector x3      rim layers above the artwork
└── Name               TEXT, display label (Inter Medium 16)
```

- The tile name is the file name: tile `firefox` becomes `firefox.svg`. Use
  the icon name that the app requests in its desktop file.
- The sync exports the first FRAME child of the tile. Keep it 128x128, give it
  the same name as the tile, and give it no visible fill.
- The `Name` text is only a label for people. It can be different from the
  tile name.
- Keep the artwork inside the circle mask. Do not add an inner plate or
  squircle that shows inside the circle.
- Use vectors only. Do not use image fills. The sync does not write an icon
  that contains a raster image.

## Adding or changing an icon

1. Duplicate a tile in the same grid that has a similar style.
2. Rename the tile and its 128x128 frame to the icon name.
3. Change the artwork. Change the `Name` label.
4. Make the same change in the light grid and in the dark grid.
5. Make sure that the name is unique across the two grids of the same theme.
   The sync does not write a name that is used two times.

The dark theme uses the light icon when a dark icon is missing. A dark tile
is necessary only when the dark icon looks different.

## Using `use_figma` on this page

- Switch to the page with `await figma.setCurrentPageAsync(page)` one time in
  each script.
- Start from a grid node ID and not from the full page. A grid has more than
  1600 tiles. A script that walks the full page, or takes screenshots in a
  loop, can run for more than 2 minutes.
- Find a tile by name: `grid.children.find(tile => tile.name === 'firefox')`.
- Return the IDs of all the nodes that you change.

## Getting changes into the repository

1. The user saves a named version in Figma (File > Save to version history).
   The sync ignores changes without a named version.
2. The `Figma sync` workflow (`.github/workflows/figma-sync.yml`) runs every
   30 minutes. It runs `figma/sync.py`, which exports only the icons that
   changed since the last sync.
3. The workflow opens or updates a pull request from the `figma-sync` branch.
   Its body lists the icons that were added, updated or not written.

To export again without a new version, run the workflow manually with the
`export` input (icon names, separated by spaces) or the `force` input:

```sh
gh workflow run figma-sync.yml -f export="firefox steam"
```

To run the sync locally, set `FIGMA_TOKEN` to a token with the
`file_content:read` and `file_versions:read` scopes and run
`python3 figma/sync.py`. The sync records the exported version and a
fingerprint of each icon in `figma/manifest.json`.

## Check before you finish

- The tile and frame names match and are safe file names (no `/`, no leading `.`).
- No name is used two times in the same theme.
- The light and dark grids have the same icon names, or the difference is intentional.
- The page has no image fills.
