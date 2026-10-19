# pkgwho: インストール元判別・アンインストール案内ツール

## 1. 目的

Ubuntu/Linux 上で、指定したコマンドが

- apt / dpkg
- snap
- flatpak
- pipx
- uv tool
- cargo
- Homebrew
- その他の手動インストール

のどれによって導入されたものかを推定し、可能であれば対応するアンインストールコマンドまで提示する CLI ツールを作成する。

Topgrade が「複数のパッケージマネージャを横断して更新するツール」なら、本ツールはその逆方向に近い位置づけとする。

想定利用例:

```bash
pkgwho topgrade
```

出力例:

```text
topgrade
  executable: /home/user/.cargo/bin/topgrade
  realpath:   /home/user/.cargo/bin/topgrade
  manager:    cargo
  package:    topgrade v17.12.3
  uninstall:  cargo uninstall topgrade
```

別の例:

```text
git
  executable: /usr/bin/git
  realpath:   /usr/bin/git
  manager:    dpkg
  package:    git
  uninstall:  sudo apt remove git
```

判定できない場合:

```text
foobar
  executable: /home/user/.local/bin/foobar
  realpath:   /home/user/.local/bin/foobar
  manager:    unknown
  note:       The executable does not appear to be managed by a supported package manager.
              It may have been installed manually or by an installer script.
```

---

## 2. 基本方針

まずコマンドの実体を特定する。

```bash
command -v COMMAND
readlink -f PATH
```

その後、各パッケージマネージャの管理情報と照合する。

単に実行ファイルのパスから推測するのではなく、可能な限り各マネージャのデータベースへ問い合わせる。

例:

```bash
dpkg -S /usr/bin/git
cargo install --list
pipx list
uv tool list
snap list
flatpak list
brew list
```

パスは補助情報として利用する。

例えば

```text
~/.cargo/bin/
```

なら Cargo の可能性が高いが、それだけで確定しない。

---

## 3. 対象プラットフォーム

当面は Ubuntu を主対象とする。

ただし実装は Linux 全般で動作しやすい形にしておきたい。

必須:

- Ubuntu 24.04 など一般的な Ubuntu
- bash / zsh から利用可能

将来的には Debian や他の Linux でも利用できる構造が望ましい。

---

## 4. コマンドライン仕様

基本:

```bash
pkgwho COMMAND
```

例:

```bash
pkgwho git
pkgwho topgrade
pkgwho ruff
```

パスを直接指定できてもよい。

```bash
pkgwho /usr/bin/git
pkgwho ~/.local/bin/foo
```

複数指定もあると便利:

```bash
pkgwho git topgrade ruff
```

ヘルプ:

```bash
pkgwho --help
```

バージョン:

```bash
pkgwho --version
```

---

## 5. 出力項目

最低限、次を表示する。

```text
command
executable
realpath
manager
package
uninstall
```

例:

```text
topgrade
  executable: /home/user/.cargo/bin/topgrade
  realpath:   /home/user/.cargo/bin/topgrade
  manager:    cargo
  package:    topgrade v17.12.3
  uninstall:  cargo uninstall topgrade
```

manager の候補:

```text
dpkg
snap
flatpak
pipx
uv
cargo
brew
unknown
```

必要なら複数候補を出す。

例:

```text
manager:    pipx
confidence: high
```

あるいは:

```text
possible managers:
  - pipx
  - manually installed under ~/.local/bin
```

---

## 6. 判定順序

おおむね以下の順序を想定する。

### 6.1 実行ファイルの取得

```bash
command -v COMMAND
```

見つからない場合:

```text
foo: command not found
```

で終了。

alias や shell function の場合は区別したい。

可能なら:

```bash
type -a COMMAND
```

相当の情報を利用する。

今回の主対象は実行ファイルなので、

- alias
- shell builtin
- shell function

の場合は

```text
manager: shell
```

などとしてもよい。

---

### 6.2 realpath

symlink を解決する。

```bash
readlink -f PATH
```

例えば `/usr/local/bin/foo` が別の場所への symlink である可能性があるため、元パスと realpath の両方を保持する。

---

## 7. 各パッケージマネージャの判定

### 7.1 dpkg / apt

最重要。

```bash
dpkg-query -S REALPATH
```

または

```bash
dpkg -S REALPATH
```

結果例:

```text
git: /usr/bin/git
```

この場合:

```text
manager: dpkg
package: git
uninstall: sudo apt remove git
```

可能なら purge はデフォルトでは提示しない。

```bash
sudo apt remove PACKAGE
```

を基本とする。

設定ファイルまで削除したい場合の候補として

```bash
sudo apt purge PACKAGE
```

を補足してもよいが、通常出力では不要。

注意:

同じファイルが複数パッケージに関連する特殊ケースにも耐えること。

---

### 7.2 Snap

```bash
snap list
```

ただしコマンド名と Snap パッケージ名が一致するとは限らない。

可能なら `/snap/...` や `/var/lib/snapd/snap/bin/...` のパス情報も利用する。

典型例:

```text
manager: snap
package: foo
uninstall: sudo snap remove foo
```

`snap list` の全文 grep だけで誤判定しないよう注意する。

---

### 7.3 Flatpak

Flatpak は通常デスクトップアプリ中心で、CLI コマンドの実体から直接紐付かない場合が多い。

```bash
flatpak list --app
```

を利用する。

アプリ ID 例:

```text
org.example.App
```

アンインストール:

```bash
flatpak uninstall org.example.App
```

CLI 名だけでは確定できないケースがあるため、無理に一致させない。

Flatpak 内部で提供される実行ファイルは、通常の PATH 上のコマンドと直接対応しないことも考慮する。

---

### 7.4 pipx

```bash
pipx list
```

できれば machine-readable な出力があればそれを優先する。

pipx のインストール済みアプリは典型的には:

```text
~/.local/bin/
```

に symlink が作られる。

pipx が管理している package と exposed apps の対応を取得し、

```text
executable -> package
```

を確認する。

例:

```text
manager: pipx
package: black
uninstall: pipx uninstall black
```

重要:

コマンド名と Python package 名が一致するとは限らない。

そのため、単純な

```bash
pipx list | grep COMMAND
```

だけでなく、exposed application の一覧から紐付ける。

---

### 7.5 uv tool

```bash
uv tool list
```

を利用する。

uv tool も pipx と同様、

```text
package -> command
```

の対応を正しく調べる必要がある。

例:

```text
manager: uv
package: ruff
uninstall: uv tool uninstall ruff
```

`~/.local/bin` に実行ファイルが存在するからといって pipx と決めつけない。

pipx と uv tool の両方を確認する。

---

### 7.6 Cargo

```bash
cargo install --list
```

Cargo インストール済み CLI は典型的には:

```text
~/.cargo/bin/
```

に置かれる。

`cargo install --list` は例えば:

```text
topgrade v17.12.3:
    topgrade
```

のような構造なので、

```text
binary -> crate
```

を対応付ける。

例:

```text
manager: cargo
package: topgrade v17.12.3
uninstall: cargo uninstall topgrade
```

コマンド名と crate 名が異なる可能性を考慮する。

---

### 7.7 Homebrew

Linuxbrew を考慮する。

```bash
brew list --formula
brew list --cask
```

実行ファイルの所有 package を調べられる方法があればそちらを使う。

典型的パス:

```text
/home/linuxbrew/.linuxbrew/bin/
```

または:

```text
~/.linuxbrew/
```

例:

```text
manager: brew
package: foo
uninstall: brew uninstall foo
```

PATH だけによる判定は補助に留める。

---

## 8. 手動インストールの判定

以下のようなケースは、通常のパッケージマネージャでは追跡できない。

```bash
curl ... | sh
```

```bash
wget ...
chmod +x foo
mv foo ~/.local/bin/
```

GitHub Releases から直接バイナリを置いたケースも含む。

各マネージャの管理対象でない場合:

```text
manager: unknown
```

とする。

ただし補助情報を表示する。

例:

```text
note: executable is under ~/.local/bin
note: no supported package manager claims this executable
note: it may have been installed manually or by an installer script
```

`/usr/local/bin` も手動インストールの可能性が比較的高い。

ただし断定しない。

---

## 9. 重要な設計原則

### パスだけで確定しない

例えば:

```text
~/.local/bin/
```

には

- pipx
- uv tool
- 手動インストール

などが混在する。

したがって必ず管理DBを優先する。

---

### コマンド名とパッケージ名を同一視しない

例:

```text
command != package
```

となるケースを正しく扱う。

内部的には次の構造がよい。

```text
manager
package_name
package_version
executables[]
uninstall_command
```

---

### アンインストールは自動実行しない

本ツールの目的は

```text
「誰が管理しているかを調べ、削除方法を示す」
```

ところまでとする。

デフォルトでアンインストールを実行してはいけない。

将来的に

```bash
pkgwho --uninstall foo
```

のような機能を追加する場合も、確認を必須にする。

---

## 10. 安全性

表示するアンインストールコマンドは shell escaping に注意する。

package 名は外部コマンド出力から取得するため、安易に文字列連結して shell 実行しない。

本ツール自身は基本的に subprocess を argv 配列で呼び出す。

例:

```python
subprocess.run(
    ["dpkg-query", "-S", realpath],
    ...
)
```

shell=True は避ける。

---

## 11. 実装言語

第一候補:

```text
Python 3
```

理由:

- Ubuntu に標準搭載されていることが多い
- subprocess 処理が容易
- テキスト解析が容易
- 将来的な拡張が容易

単一ファイルから始めてもよい。

例:

```text
pkgwho.py
```

後に package 化する。

構造例:

```text
pkgwho/
    __init__.py
    cli.py
    detectors/
        dpkg.py
        snap.py
        flatpak.py
        pipx.py
        uv.py
        cargo.py
        brew.py
```

最初は過剰設計を避け、1ファイル実装でも構わない。

---

## 12. Detector インターフェース案

内部的には各管理系を同じインターフェースにする。

例:

```python
@dataclass
class Match:
    manager: str
    package: str
    version: str | None
    uninstall_command: list[str]
    confidence: str
    details: str | None
```

Detector:

```python
class Detector:
    def detect(self, command: str, path: Path, realpath: Path) -> Match | None:
        ...
```

各 Detector:

```text
DpkgDetector
SnapDetector
FlatpakDetector
PipxDetector
UvDetector
CargoDetector
BrewDetector
```

---

## 13. 複数候補

複数の管理系が同じ名前を持つ場合がある。

例えば PATH 上では cargo 版が選択されているが、apt 版もインストールされているケース。

```bash
type -a foo
```

で複数 executable が見つかる場合:

```text
foo
  active executable:
    /home/user/.cargo/bin/foo
    manager: cargo

  other executable:
    /usr/bin/foo
    manager: dpkg
```

のように表示できると非常に便利。

初期版では active executable のみでもよいが、将来的には `type -a` 相当を扱う。

---

## 14. 推奨する初期 MVP

まず以下だけで十分に有用。

対応:

```text
dpkg
pipx
uv tool
cargo
snap
brew
unknown
```

Flatpak は CLI との対応付けが弱いため、後回しでもよい。

実行例:

```bash
pkgwho topgrade
pkgwho git
pkgwho black
pkgwho ruff
```

処理:

```text
1. command -v
2. realpath
3. dpkg ownership
4. pipx exposed apps
5. uv exposed apps
6. cargo installed binaries
7. snap
8. brew
9. unknown
```

---

## 15. テストケース

### dpkg

```bash
pkgwho git
```

期待:

```text
manager: dpkg
```

---

### Cargo

Cargo 版 topgrade がある環境:

```bash
pkgwho topgrade
```

期待:

```text
manager: cargo
package: topgrade
uninstall: cargo uninstall topgrade
```

---

### pipx

例えば:

```bash
pipx install black
pkgwho black
```

期待:

```text
manager: pipx
package: black
uninstall: pipx uninstall black
```

---

### uv

```bash
uv tool install ruff
pkgwho ruff
```

期待:

```text
manager: uv
package: ruff
uninstall: uv tool uninstall ruff
```

---

### 手動配置

```bash
cp /bin/true ~/.local/bin/mytrue
pkgwho mytrue
```

期待:

```text
manager: unknown
```

かつ:

```text
no supported package manager claims this executable
```

と表示する。

---

## 16. 将来的な拡張候補

以下は初期版には不要。

- npm / pnpm / yarn global
- gem
- go install
- conda
- micromamba
- nix
- AppImage
- mise
- asdf
- rustup components
- Docker CLI plugins
- GNOME Extensions
- VS Code extensions

特に npm global と Go binary は CLI ツールではよく使われるので、第2段階の候補。

---

## 17. プロジェクト名

仮称:

```text
pkgwho
```

意味:

```text
Who owns this package / command?
```

短く、CLI として使いやすい。

他候補:

```text
whoinstalled
pkgowner
cmdowner
unpkgwho
```

現時点では `pkgwho` が最も良い。

---

## 18. README 冒頭案

```markdown
# pkgwho

Find out which package manager installed a command on Linux.

```console
$ pkgwho topgrade

topgrade
  executable: /home/user/.cargo/bin/topgrade
  manager:    cargo
  package:    topgrade v17.12.3
  uninstall:  cargo uninstall topgrade
```

pkgwho checks package managers such as dpkg, pipx, uv, Cargo, Snap,
and Homebrew, and suggests the appropriate uninstall command.

It does not uninstall anything automatically.
```

---

## 19. 今回の背景

Ubuntu 上で Topgrade を利用しているが、Topgrade は複数のパッケージマネージャを横断して「更新」できる一方、

```text
このコマンドは何でインストールしたのか
```

を後から調べる統一的な手段が欲しい。

特に

```text
apt
pipx
uv
cargo
snap
flatpak
curl | sh
```

などが混在すると、アンインストール方法が分からなくなる。

そのため、

```text
pkgwho COMMAND
```

だけで、管理元と削除方法を調べられるツールを目指す。

---

## 20. PATH 設定箇所の表示

### 20.1 目的

PATH 上で見つかった実行ファイルについて、その実行ファイルのディレクトリを PATH に加えているシェル設定ファイルがあれば表示する。パッケージマネージャーの所有記録がない場合に、手がかりを補う用途を主とする。

これはインストール方法を特定する機能ではない。ユーザーが手動でバイナリを配置した場合も、インストーラースクリプトが配置した場合も同じ表示になり得る。出力は「PATH 設定の手がかり」とし、インストール方法を断定しない。

### 20.2 基本方針

- `$SHELL` や実行中のシェルを使って検索対象を絞らず、サポート対象のシェル設定をすべて調べる。
- Bash と Fish のように複数シェルを使い分けるユーザー向けに、該当記述がある設定ファイルをすべて表示する。
- 設定ファイルは読み取りのみとし、読み込み・実行・変更をしない。
- 実行ファイルの親ディレクトリを PATH に加える記述を、テキスト上の一致として報告する。シェルの構文を完全に評価したり、設定が実際に有効かを断定したりしない。
- パッケージマネージャーの判定結果と独立した補助情報として扱う。manager が `unknown` でない場合も表示可能とする。

### 20.3 検索対象

存在するファイルを調べる。環境変数で場所が変更されている場合は、標準位置より環境変数で指定された位置を優先する。

| シェル | ユーザー設定ファイル |
| --- | --- |
| Bash | `~/.bashrc`, `~/.bash_profile`, `~/.bash_login`, `~/.profile` |
| Zsh | `${ZDOTDIR:-$HOME}/.zshenv`, `.zprofile`, `.zshrc`, `.zlogin` |
| Fish | `${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish` および同じ `fish` ディレクトリ直下の `conf.d/*.fish` |

`ZDOTDIR` は Zsh の設定ディレクトリとして、`XDG_CONFIG_HOME` は Fish の設定ディレクトリとして解決する。これらを解決できない場合は表の既定位置を使う。

初期仕様ではユーザー単位の設定ファイルを対象とし、`/etc/profile` などのシステム全体の設定、シェル設定から `source` された任意のファイル、Fish の永続変数ファイルは検索対象外とする。この制限を理由に候補が見つからない場合も、PATH 設定が存在しないとは断定しない。

### 20.4 一致条件

検索対象の設定ファイルに、対象実行ファイルの親ディレクトリを PATH に含めることが読み取れる記述がある場合に一致とする。少なくとも次の形式を考慮する。

```bash
export PATH="$HOME/.opencode/bin:$PATH"
export PATH="$PATH:$HOME/.opencode/bin"
```

```zsh
export PATH="$HOME/.opencode/bin:$PATH"
```

```fish
fish_add_path ~/.opencode/bin
set -gx PATH ~/.opencode/bin $PATH
```

ホームディレクトリの表記（`~`, `$HOME`, `${HOME}`）を同じパスとして扱う。パスはディレクトリ単位で比較し、部分文字列だけの一致は候補にしない。コメントだけの行は一致として扱わない。

任意の shell code の評価やコマンド実行は行わない。変数展開、条件分岐、複数行構文などを安全に静的判定できない場合は、推測で一致にせず検出対象外とする。

### 20.5 テキスト出力

該当する記述がある場合、すべてのファイルを表示し、可能なら行番号と一致した行を添える。

```text
opencode
  PATH resolution:
    executable: /home/user/.opencode/bin/opencode
    realpath:   /home/user/.opencode/bin/opencode
  manager:    unknown
  PATH setup candidates:
    /home/user/.bashrc:12  export PATH="$HOME/.opencode/bin:$PATH"
    /home/user/.config/fish/config.fish:8  fish_add_path ~/.opencode/bin
  note:       PATH entries were found; they do not identify how the executable was installed
```

現在のシェルを根拠に候補を除外・優先表示しない。設定ファイルの順序はシェル名、パス、行番号で安定させる。

該当する記述がない場合は、確認した設定ファイルの範囲を明示する。

```text
  PATH setup candidates: none found in checked user shell configuration files
```

この表示は、他のファイルや動的設定を含む PATH 設定が存在しないことを意味しない。

### 20.6 JSON 出力

JSON の各コマンド結果に、検出した PATH 設定箇所を配列で追加する。既存のキーは変更しない。

```json
"path_setup": [
  {
    "file": "/home/user/.bashrc",
    "line": 12,
    "entry": "export PATH=\"$HOME/.opencode/bin:$PATH\""
  }
]
```

一致がない場合は空配列とする。設定ファイルの内容は機密情報を含み得るため、一致した行だけを出力し、ファイル全体を JSON に含めない。

### 20.7 受け入れ条件

- Bash の設定ファイルに一致があれば表示される。
- Fish の設定ファイルに一致があれば表示される。
- Bash と Fish の両方に一致があれば、両方表示される。`$SHELL` の値で片方を除外しない。
- Zsh と Fish の設定ディレクトリ指定環境変数があれば、指定先を調べる。
- 実行ファイルのディレクトリと部分的にしか一致しない別パスは表示しない。
- 設定ファイルが存在しない、読めない、または静的に判定できない場合もコマンド調査自体は成功し、PATH 設定の検出失敗をインストール方法の推定に使わない。
- この機能は設定ファイルを変更せず、設定行を実行しない。

---

ありがとうございます。普段遣いにしたいのでPythonで作ってみます。
仕様が固まれば、Rustなどで作り直しても良さそうです。

---

その進め方が良いと思います。最初は Python の方が、各パッケージマネージャの出力形式を試しながらパーサを直したり、対応対象を増やしたりするのが圧倒的に楽です。

特に `pkgwho` のようなツールは、処理速度よりも「各ツールの癖をどこまで吸収するか」が本質なので、まず Python で仕様を育てるのが向いています。`subprocess` と `dataclass` だけでも十分作れますし、テストもしやすいです。

仕様が安定してから Rust に移すなら、Rust 化のメリットはかなり明確です。単一バイナリで配布でき、Python 環境に依存せず、`~/.local/bin/pkgwho` に置くだけで使えるようになります。また、この種のシステム系 CLI は Rust と相性が良いです。

Python 版では、最初から detector ごとの境界だけきれいにしておくと移植しやすいと思います。たとえば、

```text
resolve command
    ↓
Detector[]
    ↓
Match[]
    ↓
format result
```

くらいに分離しておけば、Rust 版でもほぼ同じ設計をそのまま持っていけます。

それと普段使いなら、最終的には

```bash
pkgwho foo
```

だけでなく、

```bash
pkgwho --all foo
```

で PATH 上の同名コマンドを全部調べる機能がかなり役立ちそうです。`apt` 版と Cargo 版の両方が入っていて、実際には Cargo 版が先に見えている、といった状況を発見できます。

Python 版を作り始めて Codex の実装が出てきたら、こちらでも設計レビューや出力仕様の整理をお手伝いできます。
