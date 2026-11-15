# pkgwho

Find out which supported package manager installed a command on Linux. pkgwho
checks dpkg/apt, Snap, Flatpak, pipx, uv tools, Cargo, and Homebrew, and suggests
how to remove the package. It never uninstalls anything automatically.

```console
$ pkgwho --type command git
```

Pass one or more command names, or a path to an executable:

```console
pkgwho git snap flatpak
pkgwho /usr/local/bin/my-command
```

By default, each query searches commands, installed packages, and fonts.
Use `--type command` to resolve a command in PATH and identify its owner,
`--type package` to search installed package names, or `--type font` to
search fonts. `--type all` explicitly selects the default.
For example:

```console
pkgwho --type font "Noto Sans"
pkgwho --type font "Noto Sans" --json
```

Font searches use Fontconfig's `fc-list` and match names case- and
punctuation-insensitively. Each matching face includes its font file and,
when found in the dpkg or Homebrew formula database, its package and suggested
uninstall command. An unowned file is still shown with an unknown package.
Install Fontconfig if `fc-list` is unavailable. The default text output shows
up to 20 faces; `--no-limit` shows all faces. JSON includes all matching faces. A missing font or
unavailable `fc-list` yields a nonzero exit status in font-only mode. In
combined mode, a query succeeds if any search finds a result; an unavailable
`fc-list` is reported in the font result. The overall exit status is nonzero
if any query has no result in the selected search types.

Command searches resolve names in `PATH` and query package manager file or
executable records. Package searches use case-insensitive partial matches on
installed package names and include libraries and other non-application
packages. For example, `pkgwho --type package chrome` can find an installed
package named `google-chrome-stable`. A command search does not fall back to
package-name search; the default combined search performs both independently.
For resolved commands, pkgwho also checks common Bash, Zsh, and Fish user
configuration files for static entries that add the executable directory to
`PATH`. It also checks Fish's persistent `fish_user_paths`. It reports every
matching source as a PATH record, not as proof of how the executable was
installed. User configuration files are read but never executed or modified.
Text output shows up to 20 results per search. Use `--no-limit` to display
all matches.

Use `--json` to emit a JSON array for one or more queries:

```console
pkgwho git unknown-tool --json
pkgwho git --json --output results.json
```

In combined mode, each array element contains `query`, `command`, `package`,
and `font` fields. In single-type mode each element contains that search's
result directly. JSON output includes all matches without the text output's
default 20-match limit.
Use `-o FILE` or `--output FILE` to save the selected output format to a file
instead of printing it to standard output. Existing files are overwritten.

Install from a checkout with `pip install .`, then run `pkgwho --help` for
usage details. If no supported manager claims the executable, pkgwho reports
`unknown` rather than guessing from its location.

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
