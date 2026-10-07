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
