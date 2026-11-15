# Changelog

## 0.0.7 - 2026-11-16

- Remove the `--font` alias; use `--type font` for font searches.
- Prepare package metadata for distribution on PyPI.

## 0.0.6 - 2026-11-16

- Search installed fonts by family or full name using Fontconfig, showing font
  files and their dpkg or Homebrew package owners when available.
- Search commands and fonts by default; use `--type command`, `--type font`,
  or `--type all` to select the search scope.
- Support JSON output and display limits for font search results.

## 0.0.5 - 2026-10-19

- Report shell configuration sources that add a resolved command's directory
  to PATH, including Bash, Zsh, Fish, and Fish's persistent `fish_user_paths`.
- Distinguish PATH configuration records from evidence of package ownership.

## 0.0.4 - 2026-10-07

- Add JSON output for one or more commands.
- Add `-o` / `--output` to save results to a file.

## 0.0.3 - 2026-10-07

- Add package detection for pipx, uv tools, Cargo, and Homebrew.
- Search installed package names across all supported package managers.

## 0.0.2 - 2026-10-07

- Add application-only filtering and a match limit to package search results.
- Add `--apps-only true|false` to choose whether to include non-application
  packages.
- Include Debian package sections in search results to improve default
  filtering.
