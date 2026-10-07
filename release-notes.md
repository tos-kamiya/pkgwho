# Changelog

## 0.0.3 - 2026-10-07

- Add package detection for pipx, uv tools, Cargo, and Homebrew.
- Search installed package names across all supported package managers.

## 0.0.2 - 2026-10-07

- Add application-only filtering and a match limit to package search results.
- Add `--apps-only true|false` to choose whether to include non-application
  packages.
- Include Debian package sections in search results to improve default
  filtering.
