# pkgwho

Find out which supported package manager installed a command on Linux. The
initial version checks dpkg/apt, Snap, and Flatpak and suggests how to remove
the package. It never uninstalls anything automatically.

```console
$ pkgwho git
git
  executable: /usr/bin/git
  realpath:   /usr/bin/git
  manager:    dpkg
  package:    git
  uninstall:  sudo apt remove git
```

Pass one or more command names, or a path to an executable:

```console
pkgwho git snap flatpak
pkgwho /usr/local/bin/my-command
```

If a command is not found in `PATH`, pkgwho searches installed dpkg, Snap,
and Flatpak package names using a case-insensitive partial match. For example,
`pkgwho chrome` can find an installed package named `google-chrome-stable`.
For a command found in `PATH`, pkgwho also checks other managers for matching
package names and reports those alongside related database records.
The output separates PATH resolution from package-manager database records and
labels path records separately from package-name matches.
For dpkg package searches, pkgwho shows a whitelist of user-facing sections
and recognizable tool package names by default. Kernel, firmware, library,
documentation, and other unlisted packages are hidden. Filtering and display
limits are controlled independently:

| Options | Packages selected | Display limit |
| --- | --- | --- |
| Default (`--apps-only true`) | Application/tool candidates | 20 |
| `--no-limit` | Application/tool candidates | Unlimited |
| `--apps-only false` | All matching packages, including libraries | 20 |
| `--apps-only false --no-limit` | All matching packages, including libraries | Unlimited |

For example, `pkgwho nvidia --no-limit` displays all selected application/tool
candidates. `--apps-only` accepts `true` or `false` and defaults to `true`. The
earlier `--all-packages` and `--all-non-libraries` options have been replaced
by these independent controls.

Install from a checkout with `pip install .`, then run `pkgwho --help` for
usage details. If no supported manager claims the executable, pkgwho reports
`unknown` rather than guessing from its location.

## Supported managers

- **dpkg/apt**: queries dpkg's file ownership database.
- **Snap**: checks commands exposed in `/snap/bin` against installed snaps.
- **Flatpak**: recognizes application launchers exported in Flatpak's
  `exports/bin` directories.

## License

`pkgwho` is distributed under the terms of the [MIT](https://spdx.org/licenses/MIT.html) license.
