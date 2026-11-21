# pkgwho

Find which package manager installed a command, font, or desktop application on Linux, or search
installed packages by name. pkgwho checks dpkg/apt, Snap, Flatpak, pipx, uv
tools, Cargo, and Homebrew, and suggests how to remove packages. It never
uninstalls anything automatically.

## Installation

Install directly from GitHub with [pipx](https://pipx.pypa.io/):

```console
pipx install git+https://github.com/tos-kamiya/pkgwho.git
```

## Usage

Pass one or more search terms. By default, pkgwho searches commands, packages,
fonts, and desktop applications for each term:

```console
pkgwho git snap flatpak
pkgwho /usr/local/bin/my-command
```

The exit status is nonzero if any query has no result in the selected search
types. In combined mode, a query succeeds if any of the searches finds a result.

### `--type {command,package,font,desktop,all}`

Choose what to search. The default is `all`.

| Type | Search |
| --- | --- |
| `command` | Resolve a command in `PATH` or a file path and identify its package owner. |
| `package` | Find installed package names by case-insensitive partial match, including libraries. |
| `font` | Find installed font family or full names by case- and punctuation-insensitive partial match. |
| `desktop` | Search desktop application names, including translations, or `.desktop` filenames. |
| `all` | Search all four independently. A package may appear in more than one result. |

```console
pkgwho --type command git
pkgwho --type package chrome
pkgwho --type font "Noto Sans"
pkgwho --type desktop "スクリーンショット"
pkgwho --type desktop screenshot.desktop
```

Command searches report `unknown` when no supported manager claims the file.
They also report matching PATH entries in Bash, Zsh, and Fish configuration,
including Fish's `fish_user_paths`. These entries are not proof of installation;
configuration files are read but never executed or modified.

Font searches require Fontconfig's `fc-list`. Results include the font file and,
when found in the dpkg or Homebrew formula database, its package and suggested
uninstall command. Unowned files are still shown. An unavailable `fc-list` is
reported and causes a nonzero exit status in font-only mode.

Desktop searches match `Name` and all translated `Name[...]` values by
case-insensitive substring. A query ending in `.desktop` searches filenames
instead; a `.desktop` path containing `/` examines that file directly. Searches
use `applications` beneath `XDG_DATA_HOME` (default `~/.local/share`) and each
entry of `XDG_DATA_DIRS` (default `/usr/local/share:/usr/share`), recursively.
When desktop IDs collide, the first directory takes precedence. Hidden entries
and non-application entries are excluded. Results show the file and any package
owner found through dpkg, Homebrew, Snap or Flatpak records; an unknown owner
does not prevent a match. Snap and Flatpak exports are searched when their data
directories are included in `XDG_DATA_DIRS`.

### `--no-limit`

Text output shows up to 20 matches per search. Use `--no-limit` to display all
matches:

```console
pkgwho --type package nvidia --no-limit
```

### `--json`

Emit a JSON array for one or more queries:

```console
pkgwho git unknown-tool --json
```

In combined mode, each array element contains `query`, `command`, `package`,
`font`, and `desktop` fields. In single-type mode, each element contains that search's
result directly. JSON includes all matches without the text output's default
20-match limit.

### `-o FILE`, `--output FILE`

Write the selected output format to a file instead of standard output. Existing
files are overwritten:

```console
pkgwho git --json --output results.json
```

### `-h`, `--help`

Show command-line usage and exit.

### `--version`

Show the installed pkgwho version and exit.

## Supported managers

- **dpkg/apt**: queries dpkg's file ownership database.
- **Snap**: checks commands exposed in `/snap/bin` against installed snaps.
- **Flatpak**: recognizes application launchers exported in Flatpak's
  `exports/bin` directories.
- **pipx**: maps exposed applications from `pipx list` to their environments.
- **uv**: maps exposed executables from `uv tool list` to installed tools.
- **Cargo**: maps binaries from `cargo install --list` to installed crates.
- **Homebrew**: queries installed formulae and matches files listed by Homebrew.

## License

`pkgwho` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
