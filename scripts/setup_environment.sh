#!/usr/bin/env bash
# setup_environment.sh — host-environment doctor and bootstrapper.
#
#   bash scripts/setup_environment.sh              # doctor: report PASS/FIX, change nothing
#   bash scripts/setup_environment.sh --install    # install supported missing prerequisites
#
# Modes and tiers:
#   1. Detect existing tools first (PATH plus known locations). Anything already
#      present and version-adequate is used as-is; nothing is downloaded.
#   2. --install places missing user-space tools in a managed directory
#      (~/.iei-variant-review/tools by default): Node.js from nodejs.org and,
#      on macOS, a relocatable CPython build plus a container stack (Lima +
#      Colima + Docker CLI). No admin rights, Homebrew, Docker Desktop, or Xcode
#      Command Line Tools are required on a Mac. On Ubuntu/WSL2, missing core
#      tools, Python, and PyYAML use apt and may request the Linux user's sudo
#      password. Every direct download is version-pinned and SHA-256-verified.
#   3. On Linux/WSL2 a container runtime is a system component (kernel
#      namespaces need root to wire up); the script prints the exact commands
#      and runs them only after an explicit yes. Existing docker/podman/
#      singularity/apptainer installs are always detected and preferred.
#
# The script never edits shell profiles. Repo scripts (start_workbench.sh)
# probe the managed tools directory themselves.
#
# Flags:
#   --check            doctor only (default)
#   --install          perform tier-2 installs (and consented tier-3 on Linux)
#   --yes              assume yes for prompts (container VM start, image build)
#   --skip-container   skip every container-runtime check/install (used by CI)
#   --tools-dir DIR    override the managed tools directory
set -u -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"

# ---------------------------------------------------------------- pinned tools
PYTHON_VERSION="3.13.15"
PYTHON_BUILD_TAG="20260807"
PYTHON_SHA_DARWIN_ARM64="dbadb0ffe46f8bace50daaf8a0c5fc6903c003690776da9eb5269e33c856bb53"
PYTHON_SHA_DARWIN_X64="187eed2282e9c3a5b6b14953d564ee25a9f35cf2c209c9fa292186ee48b0e4a1"

NODE_VERSION="22.23.2"
NODE_SHA_DARWIN_ARM64="61130f394c1630d211dd50aecc4353d379480f36d3ac913cd85dbba1aed585c6"
NODE_SHA_DARWIN_X64="58e99022c2ff89395576cc7fd4d98cea24bb68081475d5f88b801ee8729fb026"
NODE_SHA_LINUX_ARM64="013b59cfd2819703a6f4a14ab891fc46fc2a4e3f5bcd92de3fb4929b43e35b30"
NODE_SHA_LINUX_X64="b294a556e639d64338823920e5866c21c02741742d2e1529ee1a225c1ec9252a"
NODE_MIN_MAJOR=22
NODE_MIN_MINOR=13

LIMA_VERSION="2.2.0"
LIMA_SHA_ARM64="bbdef91774885a0d05f7b048c4eb89ae2bcf3a0c252ae7ca7934e63df76d93c3"
LIMA_SHA_X64="0d6f99c19f6e4bc3c92730c4c29d929e6927f0cb0a0ba1a84383367135a8ff31"

COLIMA_VERSION="v0.10.3"
COLIMA_SHA_ARM64="980ad8bf61a4ca370243f4cb41401a61276dcd2c2502bee7b9b86f9250169f34"
COLIMA_SHA_X64="3082737fe8a98afda11cba7d9a20b6e56fe80c6153464beda04bec630758770b"

DOCKER_CLI_VERSION="29.7.2"
DOCKER_CLI_SHA_MAC_ARM64="b8683ed19d1f06048a496f9b8429e2c71d0b088d475b7487c054ea3666c02a3c"
DOCKER_CLI_SHA_MAC_X64="fb1f1aa7ac7af4364165b9eadfda92e96c8ced508fca74f53079719891367438"

# Reference-download footprint used for the disk advisory (GiB).
DISK_MIN_EXOME_GB=40

# ---------------------------------------------------------------- cli parsing
MODE="check"
ASSUME_YES=0
SKIP_CONTAINER=0
TOOLS_DIR="${IEI_TOOLS_DIR:-$HOME/.iei-variant-review/tools}"
while [ $# -gt 0 ]; do
    case "$1" in
        --check) MODE="check" ;;
        --install) MODE="install" ;;
        --yes) ASSUME_YES=1 ;;
        --skip-container) SKIP_CONTAINER=1 ;;
        --tools-dir) shift; TOOLS_DIR="${1:?--tools-dir needs a value}" ;;
        -h|--help)
            sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown flag: $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
done

# ---------------------------------------------------------------- helpers
PASS_COUNT=0
FIX_COUNT=0
WARN_COUNT=0
FIX_LINES=""

ok()   { printf '[ OK ] %s\n' "$1"; PASS_COUNT=$((PASS_COUNT + 1)); }
note() { printf '[NOTE] %s\n' "$1"; }
wrn()  { printf '[WARN] %s\n' "$1"; WARN_COUNT=$((WARN_COUNT + 1)); }
fix()  { # fix <what is wrong> <how to fix it>
    printf '[FIX ] %s\n' "$1"
    [ -n "${2:-}" ] && printf '       -> %s\n' "$2"
    FIX_COUNT=$((FIX_COUNT + 1))
    FIX_LINES="${FIX_LINES}  - $1\n"
}
die() { printf 'ERROR: %s\n' "$1" >&2; exit 2; }

confirm() { # confirm <question>  (respects --yes; non-interactive -> no)
    [ "$ASSUME_YES" = 1 ] && return 0
    [ -t 0 ] || return 1
    printf '%s [y/N] ' "$1"
    read -r answer
    case "$answer" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

sha256_file() {
    if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
    else die "neither shasum nor sha256sum is available"; fi
}

download_verified() { # download_verified <url> <dest> <sha256>
    local url="$1" dest="$2" want="$3" got
    if [ -f "$dest" ]; then
        got="$(sha256_file "$dest")"
        [ "$got" = "$want" ] && return 0
        rm -f "$dest"
    fi
    mkdir -p "$(dirname "$dest")"
    echo "  downloading $(basename "$dest") ..."
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 -o "$dest.part" "$url" || { rm -f "$dest.part"; return 1; }
    else
        wget -q -O "$dest.part" "$url" || { rm -f "$dest.part"; return 1; }
    fi
    got="$(sha256_file "$dest.part")"
    if [ "$got" != "$want" ]; then
        rm -f "$dest.part"
        die "checksum mismatch for $url (expected $want, got $got) — refusing to install"
    fi
    mv "$dest.part" "$dest"
}

APT_UPDATED=0
apt_install_packages() { # apt_install_packages <description> <package>...
    local description="$1"
    shift
    [ "$OS" = "Linux" ] && command -v apt-get >/dev/null 2>&1 || return 1
    confirm "Install $description? Runs: sudo apt-get install -y $*" || return 1
    if [ "$APT_UPDATED" = 0 ]; then
        echo "  refreshing Ubuntu/Debian package information ..."
        sudo apt-get update -qq || return 1
        APT_UPDATED=1
    fi
    sudo apt-get install -y "$@"
}

# ---------------------------------------------------------------- platform
OS="$(uname -s)"
ARCH="$(uname -m)"
IS_WSL=0
if [ "$OS" = "Linux" ] && grep -qi microsoft /proc/version 2>/dev/null; then IS_WSL=1; fi
case "$OS" in
    Darwin) PLATFORM_LABEL="macOS ($ARCH)" ;;
    Linux)  if [ "$IS_WSL" = 1 ]; then PLATFORM_LABEL="Windows WSL2 ($ARCH)"; else PLATFORM_LABEL="Linux ($ARCH)"; fi ;;
    *) die "unsupported platform: $OS. Native Windows shells cannot run this pipeline — use WSL2 (see README)." ;;
esac

echo "== IEI pipeline environment ${MODE} — ${PLATFORM_LABEL}"
echo "   repo: $ROOT"
echo "   managed tools dir: $TOOLS_DIR"
echo

# WSL2: warn when the clone lives on the Windows drive (very slow tabix I/O).
if [ "$IS_WSL" = 1 ]; then
    case "$ROOT" in
        /mnt/*) wrn "clone is on the Windows filesystem ($ROOT); move it (and reference data) into the WSL2 filesystem (~/...) — /mnt/c I/O badly hurts multi-GB reference reads" ;;
        *) ok "clone is on the WSL2 filesystem" ;;
    esac
fi

# ---------------------------------------------------------------- core tools
find_missing_core() {
    local missing="" tool
    for tool in tar gzip awk sed sort; do
        command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
    done
    if command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1; then :; else
        missing="$missing curl-or-wget"
    fi
    printf '%s' "$missing"
}
missing_core="$(find_missing_core)"
if [ -n "$missing_core" ] && [ "$MODE" = "install" ] && [ "$OS" = "Linux" ]; then
    echo "  installing required Ubuntu/Debian command-line tools ..."
    apt_install_packages "required GUIDE-IEI command-line tools" \
        ca-certificates curl tar gzip gawk sed coreutils || true
    missing_core="$(find_missing_core)"
fi
if [ -z "$missing_core" ]; then
    ok "core tools (tar, curl/wget, awk, sed, sort, gzip)"
else
    if [ "$OS" = "Darwin" ]; then
        fix "missing core tools:$missing_core" "xcode-select --install"
    else
        fix "missing core tools:$missing_core" "sudo apt-get install -y git curl tar gzip  (or your distro's equivalent)"
    fi
fi

# Git is needed only for developer source checkouts and terminal updates. The
# standalone app and its in-app updater do not require it. /usr/bin/git is an
# unusable Xcode-installation stub on a factory-fresh Mac, so test it instead
# of treating command discovery as success.
GIT_OK=0
if [ "$OS" = "Darwin" ] && ! xcode-select -p >/dev/null 2>&1; then
    note "git is unavailable without Xcode Command Line Tools (not required by the standalone app)"
elif command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1; then
    GIT_OK=1
    ok "git available (optional for the standalone app)"
else
    note "git not found (not required by the standalone app)"
fi

# ---------------------------------------------------------------- python + pyyaml
PYTHON_OK=0
PYYAML_OK=0
python_version_ok() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' \
        >/dev/null 2>&1
}

resolve_python() {
    local candidate
    if [ -n "${IEI_PYTHON_BIN:-}" ] && [ -x "$IEI_PYTHON_BIN" ] \
       && python_version_ok "$IEI_PYTHON_BIN"; then
        echo "$IEI_PYTHON_BIN"; return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        candidate="$(command -v python3)"
        if python_version_ok "$candidate"; then
            echo "$candidate"; return 0
        fi
    fi
    for candidate in \
        "$TOOLS_DIR/bin/python3" \
        "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
    do
        if [ -x "$candidate" ] && python_version_ok "$candidate"; then
            echo "$candidate"; return 0
        fi
    done
    return 1
}

install_python_macos() {
    local target_triple sha archive url target temporary
    case "$ARCH" in
        arm64)  target_triple="aarch64-apple-darwin"; sha="$PYTHON_SHA_DARWIN_ARM64" ;;
        x86_64) target_triple="x86_64-apple-darwin";  sha="$PYTHON_SHA_DARWIN_X64" ;;
        *) fix "no pinned Python build for macOS/$ARCH" "install Python >= 3.9"; return 1 ;;
    esac
    archive="$TOOLS_DIR/downloads/cpython-${PYTHON_VERSION}+${PYTHON_BUILD_TAG}-${target_triple}-install_only_stripped.tar.gz"
    url="https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_BUILD_TAG}/$(basename "$archive" | sed 's/+/%2B/')"
    download_verified "$url" "$archive" "$sha" || {
        fix "managed Python download failed" "$url"
        return 1
    }
    target="$TOOLS_DIR/python-${PYTHON_VERSION}-${target_triple}"
    temporary="$TOOLS_DIR/.python-install-${PYTHON_VERSION}-${target_triple}"
    rm -rf "$temporary"
    mkdir -p "$temporary"
    tar -xzf "$archive" -C "$temporary" || {
        rm -rf "$temporary"
        fix "managed Python extraction failed" "remove $archive and rerun"
        return 1
    }
    [ -x "$temporary/python/bin/python3" ] || {
        rm -rf "$temporary"
        fix "managed Python archive has an unexpected layout" "remove $archive and rerun"
        return 1
    }
    rm -rf "$target"
    mv "$temporary/python" "$target"
    rmdir "$temporary" 2>/dev/null || true
    mkdir -p "$TOOLS_DIR/bin"
    ln -sfn "$target/bin/python3" "$TOOLS_DIR/bin/python3"
    [ -x "$target/bin/pip3" ] && ln -sfn "$target/bin/pip3" "$TOOLS_DIR/bin/pip3"
    return 0
}

PYTHON_BIN=""
if PYTHON_BIN="$(resolve_python)"; then
    PYTHON_OK=1
elif [ "$MODE" = "install" ] && [ "$OS" = "Darwin" ]; then
    echo "  installing Python ${PYTHON_VERSION} into $TOOLS_DIR (relocatable, no Xcode or admin rights) ..."
    if install_python_macos && PYTHON_BIN="$(resolve_python)"; then
        PYTHON_OK=1
    fi
elif [ "$MODE" = "install" ] && [ "$OS" = "Linux" ] && command -v apt-get >/dev/null 2>&1; then
    echo "  installing Python and PyYAML with Ubuntu/Debian packages ..."
    apt_install_packages "Python and its GUIDE-IEI configuration parser" python3 python3-yaml || true
    if PYTHON_BIN="$(resolve_python)"; then
        PYTHON_OK=1
    fi
fi

if [ "$PYTHON_OK" = 1 ]; then
    ok "python3 $("$PYTHON_BIN" -c 'import platform; print(platform.python_version())') at $PYTHON_BIN (>= 3.9)"
    if [ "$OS" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
        PY_ARCH="$("$PYTHON_BIN" -c 'import platform; print(platform.machine())' 2>/dev/null)"
        if [ "$PY_ARCH" = "x86_64" ]; then
            wrn "python3 is an Intel build running under Rosetta on this Apple Silicon Mac — use the managed native Python by rerunning with --install"
        fi
    fi
else
    if [ "$OS" = "Darwin" ]; then
        fix "usable Python >= 3.9 not found" "rerun with --install (managed native Python; no Xcode or admin rights)"
    else
        fix "usable Python >= 3.9 not found" "sudo apt-get install -y python3 python3-pip  (or your distro's equivalent)"
    fi
fi
if [ "$PYTHON_OK" = 1 ]; then
    if "$PYTHON_BIN" -c 'import yaml' 2>/dev/null; then
        ok "PyYAML importable (config parser dependency)"
        PYYAML_OK=1
    elif [ "$MODE" = "install" ]; then
        if [ "$OS" = "Linux" ] && command -v apt-get >/dev/null 2>&1; then
            echo "  installing PyYAML with the Ubuntu/Debian package manager ..."
            apt_install_packages "the GUIDE-IEI configuration parser" python3-yaml || true
        fi
        if "$PYTHON_BIN" -c 'import yaml' 2>/dev/null; then
            ok "PyYAML installed"
            PYYAML_OK=1
        else
            echo "  installing PyYAML for $PYTHON_BIN ..."
        fi
        if [ "$PYYAML_OK" = 0 ] && case "$PYTHON_BIN" in
               "$TOOLS_DIR"/*) "$PYTHON_BIN" -m pip install --disable-pip-version-check -r "$ROOT/requirements.txt" >/dev/null 2>&1 ;;
               *) "$PYTHON_BIN" -m pip install --user -r "$ROOT/requirements.txt" >/dev/null 2>&1 \
                  || "$PYTHON_BIN" -m pip install --user --break-system-packages -r "$ROOT/requirements.txt" >/dev/null 2>&1 ;;
           esac
        then
            ok "PyYAML installed"
            PYYAML_OK=1
        elif [ "$PYYAML_OK" = 0 ]; then
            fix "PyYAML install failed" "sudo apt-get install -y python3-yaml  (or: $PYTHON_BIN -m pip install pyyaml)"
        fi
    else
        fix "PyYAML not importable" "rerun with --install"
    fi
fi

# ---------------------------------------------------------------- node.js
node_version_ok() { # node_version_ok <node-binary>
    "$1" -e "const [maj, min] = process.versions.node.split('.').map(Number); process.exit(maj > $NODE_MIN_MAJOR || (maj === $NODE_MIN_MAJOR && min >= $NODE_MIN_MINOR) ? 0 : 1)" 2>/dev/null
}
node_has_npm() { # the workbench install needs npm next to node
    [ -x "$(dirname "$1")/npm" ] && return 0
    # a PATH-resolved node may pair with a PATH-resolved npm
    [ "$1" = "$(command -v node 2>/dev/null)" ] && command -v npm >/dev/null 2>&1
}
resolve_node() {
    local candidate
    if command -v node >/dev/null 2>&1 \
       && node_version_ok "$(command -v node)" && node_has_npm "$(command -v node)"; then
        command -v node; return 0
    fi
    for candidate in \
        "$TOOLS_DIR/bin/node" \
        /opt/homebrew/bin/node \
        /usr/local/bin/node \
        "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
    do
        if [ -x "$candidate" ] && node_version_ok "$candidate" && node_has_npm "$candidate"; then
            echo "$candidate"; return 0
        fi
    done
    return 1
}

install_node() {
    local suffix sha url tarball extracted
    case "$OS/$ARCH" in
        Darwin/arm64)         suffix="darwin-arm64"; sha="$NODE_SHA_DARWIN_ARM64" ;;
        Darwin/x86_64)        suffix="darwin-x64";   sha="$NODE_SHA_DARWIN_X64" ;;
        Linux/aarch64|Linux/arm64) suffix="linux-arm64"; sha="$NODE_SHA_LINUX_ARM64" ;;
        Linux/x86_64)         suffix="linux-x64";    sha="$NODE_SHA_LINUX_X64" ;;
        *) fix "no pinned Node build for $OS/$ARCH" "install Node >= ${NODE_MIN_MAJOR}.${NODE_MIN_MINOR} from https://nodejs.org"; return 1 ;;
    esac
    tarball="$TOOLS_DIR/downloads/node-v${NODE_VERSION}-${suffix}.tar.gz"
    url="https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-${suffix}.tar.gz"
    download_verified "$url" "$tarball" "$sha" || { fix "Node download failed" "$url"; return 1; }
    extracted="$TOOLS_DIR/node-v${NODE_VERSION}-${suffix}"
    rm -rf "$extracted"
    tar -xzf "$tarball" -C "$TOOLS_DIR" || { fix "Node tarball extraction failed" ""; return 1; }
    mkdir -p "$TOOLS_DIR/bin"
    local name
    for name in node npm npx; do
        ln -sfn "$extracted/bin/$name" "$TOOLS_DIR/bin/$name"
    done
    return 0
}

NODE_BIN=""
if NODE_BIN="$(resolve_node)"; then
    ok "node $("$NODE_BIN" --version) at $NODE_BIN (>= ${NODE_MIN_MAJOR}.${NODE_MIN_MINOR})"
elif [ "$MODE" = "install" ]; then
    echo "  installing Node v${NODE_VERSION} into $TOOLS_DIR (official nodejs.org build) ..."
    if install_node && NODE_BIN="$(resolve_node)"; then
        ok "node $("$NODE_BIN" --version) installed at $NODE_BIN"
    else
        NODE_BIN=""
    fi
else
    fix "Node >= ${NODE_MIN_MAJOR}.${NODE_MIN_MINOR} not found" "rerun with --install (user-space install, no admin rights) or install from https://nodejs.org"
fi

NPM_BIN=""
if [ -n "$NODE_BIN" ]; then
    if [ -x "$(dirname "$NODE_BIN")/npm" ]; then
        NPM_BIN="$(dirname "$NODE_BIN")/npm"
    elif command -v npm >/dev/null 2>&1; then
        NPM_BIN="$(command -v npm)"
    fi
    if [ -z "$NPM_BIN" ]; then
        fix "npm not found beside node" "rerun with --install to use the managed Node (its npm is bundled)"
    fi
fi

# ---------------------------------------------------------------- webui deps
if [ -d "$ROOT/webui/node_modules" ]; then
    ok "webui/node_modules present"
elif [ "$MODE" = "install" ] && [ -n "$NPM_BIN" ]; then
    echo "  installing webui dependencies (npm ci) ..."
    if (cd "$ROOT/webui" && PATH="$(dirname "$NODE_BIN"):$PATH" "$NPM_BIN" ci --no-fund --no-audit >/dev/null 2>&1); then
        ok "webui dependencies installed"
    else
        fix "npm ci failed in webui/" "cd webui && npm ci  (rerun to see the error output)"
    fi
else
    fix "webui dependencies not installed" "rerun with --install, or: cd webui && npm ci"
fi

# ------------------------------------------------- native htslib tools (optional)
# The pipeline is fully functional without these (every htslib operation
# falls back to the container), but native bcftools/tabix/bgzip avoid
# containerized I/O over bind mounts — typically 5-20x faster on macOS — and
# remove the container write-visibility race class entirely. The pinned
# in-container bcftools/liftover used for GRCh37 intake is unaffected.
find_conda() {
    local candidate
    command -v conda 2>/dev/null && return 0
    for candidate in "$HOME/opt/anaconda3/bin/conda" "$HOME/anaconda3/bin/conda" \
        "$HOME/miniconda3/bin/conda" "$HOME/opt/miniconda3/bin/conda" \
        /opt/homebrew/Caskroom/miniconda/base/bin/conda; do
        [[ -x "$candidate" ]] && { echo "$candidate"; return 0; }
    done
    return 1
}
if command -v bcftools >/dev/null 2>&1 && command -v tabix >/dev/null 2>&1 \
   && command -v bgzip >/dev/null 2>&1; then
    ok "native bcftools/tabix/bgzip found — htslib I/O runs without container overhead"
elif [ "$MODE" = "install" ]; then
    CONDA_BIN="$(find_conda || true)"
    if [ -n "$CONDA_BIN" ]; then
        echo "  installing native bcftools/htslib via conda (optional performance component; the solver can take several minutes)..."
        if "$CONDA_BIN" install -y -q -c conda-forge -c bioconda bcftools htslib >/dev/null 2>&1; then
            ok "native bcftools/htslib installed into conda"
        else
            wrn "conda could not install bcftools/htslib (dependency conflicts are common in large base environments); the container fallback remains fully functional. Alternative: conda create -n hts -c conda-forge -c bioconda bcftools htslib, then link the binaries onto PATH"
        fi
    elif command -v brew >/dev/null 2>&1; then
        echo "  installing native bcftools/htslib via Homebrew (optional performance component)..."
        brew install bcftools htslib >/dev/null 2>&1 \
            && ok "native bcftools/htslib installed via Homebrew" \
            || wrn "brew install bcftools htslib failed; the container fallback remains fully functional"
    elif [ "$OS" = "Linux" ] && command -v apt-get >/dev/null 2>&1 \
         && confirm "Install native bcftools/tabix (recommended for I/O speed)? Runs: sudo apt-get install -y bcftools tabix"; then
        sudo apt-get install -y bcftools tabix >/dev/null 2>&1 \
            && ok "native bcftools/tabix installed" \
            || wrn "apt install failed; the container fallback remains fully functional"
    else
        wrn "native bcftools/tabix not found and no supported installer detected (conda/brew/apt); htslib I/O will run via the container, which is substantially slower"
    fi
else
    wrn "native bcftools/tabix not found (optional): htslib I/O runs via the container, typically 5-20x slower on macOS bind mounts — rerun with --install to add them via conda/brew/apt"
fi

# ---------------------------------------------------------------- container runtime
read_config_scalar() { # best-effort: needs python3 + pyyaml, else prints nothing
    [ "$PYYAML_OK" = 1 ] || return 0
    "$PYTHON_BIN" - "$ROOT/config/annotation.config.yaml" "$1" <<'PYEOF' 2>/dev/null
import sys, yaml
try:
    with open(sys.argv[1]) as handle:
        data = yaml.safe_load(handle) or {}
    for part in sys.argv[2].split("."):
        data = data.get(part) if isinstance(data, dict) else None
    if data is not None:
        print(data)
except Exception:
    pass
PYEOF
}

CONTAINER_RUNTIME=""
CONTAINER_BIN=""
DAEMON_UP=0
if [ "$SKIP_CONTAINER" = 1 ]; then
    note "container checks skipped (--skip-container)"
else
    CONFIG_RUNTIME="$(read_config_scalar container.runtime)"
    [ -z "$CONFIG_RUNTIME" ] && CONFIG_RUNTIME="docker"
    for candidate in "$CONFIG_RUNTIME" docker podman singularity apptainer; do
        bin_path="$(command -v "$candidate" 2>/dev/null || true)"
        [ -z "$bin_path" ] && [ -x "$TOOLS_DIR/bin/$candidate" ] && bin_path="$TOOLS_DIR/bin/$candidate"
        if [ -n "$bin_path" ]; then
            CONTAINER_RUNTIME="$candidate"
            CONTAINER_BIN="$bin_path"
            break
        fi
    done

    install_macos_container_stack() {
        local lima_sha colima_sha docker_sha lima_arch colima_arch docker_arch
        case "$ARCH" in
            arm64)  lima_arch="arm64";  lima_sha="$LIMA_SHA_ARM64";  colima_arch="arm64";  colima_sha="$COLIMA_SHA_ARM64"; docker_arch="aarch64"; docker_sha="$DOCKER_CLI_SHA_MAC_ARM64" ;;
            x86_64) lima_arch="x86_64"; lima_sha="$LIMA_SHA_X64";    colima_arch="x86_64"; colima_sha="$COLIMA_SHA_X64";   docker_arch="x86_64";  docker_sha="$DOCKER_CLI_SHA_MAC_X64" ;;
            *) fix "no pinned container stack for macOS/$ARCH" ""; return 1 ;;
        esac
        mkdir -p "$TOOLS_DIR/bin" "$TOOLS_DIR/downloads"
        # Lima (VM layer; uses macOS Virtualization.framework, no admin rights)
        local lima_tar="$TOOLS_DIR/downloads/lima-${LIMA_VERSION}-Darwin-${lima_arch}.tar.gz"
        download_verified "https://github.com/lima-vm/lima/releases/download/v${LIMA_VERSION}/lima-${LIMA_VERSION}-Darwin-${lima_arch}.tar.gz" \
            "$lima_tar" "$lima_sha" || return 1
        rm -rf "$TOOLS_DIR/lima-${LIMA_VERSION}"
        mkdir -p "$TOOLS_DIR/lima-${LIMA_VERSION}"
        tar -xzf "$lima_tar" -C "$TOOLS_DIR/lima-${LIMA_VERSION}" || return 1
        # symlink preserves the binary's relative ../share/lima lookup
        ln -sfn "$TOOLS_DIR/lima-${LIMA_VERSION}/bin/limactl" "$TOOLS_DIR/bin/limactl"
        # Colima (docker daemon convenience wrapper over Lima)
        local colima_bin="$TOOLS_DIR/downloads/colima-Darwin-${colima_arch}"
        download_verified "https://github.com/abiosoft/colima/releases/download/${COLIMA_VERSION}/colima-Darwin-${colima_arch}" \
            "$colima_bin" "$colima_sha" || return 1
        install -m 0755 "$colima_bin" "$TOOLS_DIR/bin/colima"
        # Docker CLI (client only; talks to Colima's daemon)
        local docker_tar="$TOOLS_DIR/downloads/docker-${DOCKER_CLI_VERSION}-${docker_arch}.tgz"
        download_verified "https://download.docker.com/mac/static/stable/${docker_arch}/docker-${DOCKER_CLI_VERSION}.tgz" \
            "$docker_tar" "$docker_sha" || return 1
        tar -xzf "$docker_tar" -C "$TOOLS_DIR/downloads" docker/docker || return 1
        install -m 0755 "$TOOLS_DIR/downloads/docker/docker" "$TOOLS_DIR/bin/docker"
        rm -rf "$TOOLS_DIR/downloads/docker"
        return 0
    }

    if [ -z "$CONTAINER_BIN" ]; then
        if [ "$OS" = "Darwin" ] && [ "$MODE" = "install" ]; then
            echo "  installing user-space container stack (Lima ${LIMA_VERSION} + Colima ${COLIMA_VERSION} + Docker CLI ${DOCKER_CLI_VERSION}) ..."
            if install_macos_container_stack; then
                CONTAINER_RUNTIME="docker"
                CONTAINER_BIN="$TOOLS_DIR/bin/docker"
                ok "container stack installed in $TOOLS_DIR (no admin rights used)"
            else
                fix "container stack install failed" "see messages above; Docker Desktop or 'brew install colima docker' are alternatives"
            fi
        elif [ "$OS" = "Darwin" ]; then
            fix "no container runtime found (docker/podman/singularity)" "rerun with --install for a no-admin Lima+Colima stack, or install Docker Desktop"
        elif [ "$IS_WSL" = 1 ] && [ "${IEI_WSL_LAUNCHER:-0}" = 1 ]; then
            # The Windows double-click launcher must never install a second,
            # native Docker engine just because Docker Desktop integration is
            # off. The workbench itself is useful without VEP/container work.
            if [ "${IEI_WSL_DOCKER_DESKTOP:-0}" = 1 ]; then
                fix "Docker Desktop is not reachable inside this WSL2 distribution" "start Docker Desktop and enable Settings > Resources > WSL Integration for this distribution"
            else
                fix "no container runtime found in WSL2" "install Docker Desktop and enable WSL Integration, or install Docker manually inside WSL2"
            fi
        else
            # Linux/WSL2: a runtime is a system component (kernel namespaces
            # need root to wire up) — consented commands only.
            if [ "$IS_WSL" = 1 ]; then
                runtime_cmd="curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker \$USER"
                runtime_hint="inside the WSL2 distro (systemd is enabled by default; Docker Desktop is NOT required)"
            elif command -v apt-get >/dev/null 2>&1; then
                runtime_cmd="curl -fsSL https://get.docker.com | sudo sh && sudo usermod -aG docker \$USER"
                runtime_hint="official Docker convenience script"
            elif command -v dnf >/dev/null 2>&1; then
                runtime_cmd="sudo dnf install -y podman"
                runtime_hint="podman (set container.runtime: podman in the config)"
            else
                runtime_cmd="install docker or podman with your distro's package manager"
                runtime_hint=""
            fi
            if [ "$MODE" = "install" ] && confirm "Install a container runtime now? Runs: $runtime_cmd"; then
                if sh -c "$runtime_cmd"; then
                    ok "container runtime installed ($runtime_hint)"
                    note "log out and back in so the docker group membership takes effect"
                    CONTAINER_RUNTIME="docker"
                    CONTAINER_BIN="$(command -v docker || true)"
                else
                    fix "container runtime install failed" "$runtime_cmd"
                fi
            else
                fix "no container runtime found (docker/podman/singularity)" "$runtime_cmd   # $runtime_hint"
            fi
        fi
    fi

    # ---- daemon / VM health
    if [ -n "$CONTAINER_BIN" ]; then
        case "$CONTAINER_RUNTIME" in
            docker|podman)
                if "$CONTAINER_BIN" info >/dev/null 2>&1; then
                    DAEMON_UP=1
                    ok "$CONTAINER_RUNTIME daemon reachable ($CONTAINER_BIN)"
                elif [ "$OS" = "Darwin" ] && [ -x "$TOOLS_DIR/bin/colima" ]; then
                    if [ "$MODE" = "install" ] && confirm "Start the Colima VM now (first start downloads a ~1 GB VM image)?"; then
                        host_cpus="$(sysctl -n hw.ncpu 2>/dev/null || echo 4)"
                        host_mem_gb="$(( $(sysctl -n hw.memsize 2>/dev/null || echo 17179869184) / 1073741824 ))"
                        vm_cpus=$(( host_cpus > 8 ? 8 : (host_cpus > 2 ? host_cpus - 1 : 2) ))
                        vm_mem=$(( host_mem_gb / 2 )); [ "$vm_mem" -gt 12 ] && vm_mem=12; [ "$vm_mem" -lt 4 ] && vm_mem=4
                        if PATH="$TOOLS_DIR/bin:$PATH" "$TOOLS_DIR/bin/colima" start --cpu "$vm_cpus" --memory "$vm_mem" --disk 120; then
                            DAEMON_UP=1
                            ok "Colima VM started (${vm_cpus} CPU, ${vm_mem} GiB RAM, 120 GiB disk)"
                        else
                            fix "Colima VM failed to start" "PATH=\"$TOOLS_DIR/bin:\$PATH\" colima start --cpu 4 --memory 8 --disk 120"
                        fi
                    else
                        fix "$CONTAINER_RUNTIME daemon not running" "PATH=\"$TOOLS_DIR/bin:\$PATH\" colima start --cpu 4 --memory 8 --disk 120"
                    fi
                elif [ "$OS" = "Darwin" ]; then
                    fix "$CONTAINER_RUNTIME daemon not running" "start Docker Desktop (or rerun with --install for the no-admin Colima stack)"
                elif [ "$IS_WSL" = 1 ] && [ "${IEI_WSL_LAUNCHER:-0}" = 1 ]; then
                    if [ "${IEI_WSL_DOCKER_DESKTOP:-0}" = 1 ]; then
                        fix "Docker Desktop is not reachable inside this WSL2 distribution" "start Docker Desktop and enable Settings > Resources > WSL Integration for this distribution"
                    else
                        fix "$CONTAINER_RUNTIME is installed but not running in WSL2" "start it manually, or install Docker Desktop and enable WSL Integration"
                    fi
                else
                    fix "$CONTAINER_RUNTIME daemon not running" "sudo systemctl enable --now docker   # then log out/in if you were just added to the docker group"
                fi
                ;;
            singularity|apptainer)
                if "$CONTAINER_BIN" --version >/dev/null 2>&1; then
                    DAEMON_UP=1
                    ok "$CONTAINER_RUNTIME available ($("$CONTAINER_BIN" --version 2>/dev/null | head -1))"
                    note "set container.runtime: $CONTAINER_RUNTIME in config/annotation.config.yaml (or a local copy) if not already"
                fi
                ;;
        esac
    fi

    # ---- VM sizing + architecture advisories (docker/podman only)
    if [ "$DAEMON_UP" = 1 ] && { [ "$CONTAINER_RUNTIME" = "docker" ] || [ "$CONTAINER_RUNTIME" = "podman" ]; }; then
        engine_info="$("$CONTAINER_BIN" info --format '{{.NCPU}} {{.MemTotal}} {{.Architecture}}' 2>/dev/null || true)"
        engine_cpus="$(echo "$engine_info" | awk '{print $1}')"
        engine_mem="$(echo "$engine_info" | awk '{print $2}')"
        engine_arch="$(echo "$engine_info" | awk '{print $3}')"
        if [ -n "$engine_cpus" ] && [ "$engine_cpus" -lt 4 ] 2>/dev/null; then
            wrn "container engine has only $engine_cpus CPUs; VEP fork parallelism suffers — resize the VM (colima: colima stop && colima start --cpu 4+)"
        fi
        if [ -n "$engine_mem" ] && [ "$engine_mem" -lt 8000000000 ] 2>/dev/null; then
            wrn "container engine has < 8 GiB RAM; large tabix references (dbNSFP, SpliceAI) need memory — resize the VM (colima: --memory 8+; Docker Desktop: Settings -> Resources)"
        fi
        if [ "$OS" = "Darwin" ] && [ "$ARCH" = "arm64" ] && echo "$engine_arch" | grep -qi "aarch64\|arm64"; then
            note "Apple Silicon: the amd64 VEP image runs through Colima's default binfmt emulation (works, but slower); advanced users may enable Rosetta with 'colima stop && colima start --vm-type vz --vz-rosetta'"
        fi

        # ---- image identity + reference mount reachability
        # A tag such as vep-annotate:latest is mutable. Match a deterministic
        # label against this checkout so an image from an older GUIDE-IEI
        # version is rebuilt instead of failing only after a job is submitted.
        IMAGE="$(read_config_scalar container.image)"
        [ -z "$IMAGE" ] && IMAGE="vep-annotate:latest"
        EXPECTED_IMAGE_FINGERPRINT="$(bash "$ROOT/docker/image_fingerprint.sh" 2>/dev/null || true)"
        IMAGE_PRESENT=0
        IMAGE_CURRENT=0
        ACTUAL_IMAGE_FINGERPRINT=""
        if "$CONTAINER_BIN" image inspect "$IMAGE" >/dev/null 2>&1; then
            IMAGE_PRESENT=1
            ACTUAL_IMAGE_FINGERPRINT="$(
                "$CONTAINER_BIN" image inspect --format \
                    '{{ index .Config.Labels "org.guide-iei.source-fingerprint" }}' \
                    "$IMAGE" 2>/dev/null || true
            )"
            if [ -n "$EXPECTED_IMAGE_FINGERPRINT" ] \
                && [ "$ACTUAL_IMAGE_FINGERPRINT" = "$EXPECTED_IMAGE_FINGERPRINT" ]; then
                IMAGE_CURRENT=1
            fi
        fi

        if [ "$IMAGE_CURRENT" = 1 ]; then
            ok "container image $IMAGE matches this GUIDE-IEI version"
        elif [ "$MODE" = "install" ] && confirm "$([ "$IMAGE_PRESENT" = 1 ] && echo 'Rebuild the outdated VEP container image now?' || echo 'Build the VEP container image now (downloads the ~2 GB base image)?')"; then
            if (cd "$ROOT" && RUNTIME="$CONTAINER_RUNTIME" PATH="$(dirname "$CONTAINER_BIN"):$PATH" bash docker/build.sh); then
                ACTUAL_IMAGE_FINGERPRINT="$(
                    "$CONTAINER_BIN" image inspect --format \
                        '{{ index .Config.Labels "org.guide-iei.source-fingerprint" }}' \
                        "$IMAGE" 2>/dev/null || true
                )"
                if [ -n "$EXPECTED_IMAGE_FINGERPRINT" ] \
                    && [ "$ACTUAL_IMAGE_FINGERPRINT" = "$EXPECTED_IMAGE_FINGERPRINT" ]; then
                    IMAGE_CURRENT=1
                    ok "container image built for this GUIDE-IEI version"
                else
                    fix "image build completed but its identity could not be verified" "bash docker/build.sh"
                fi
            else
                fix "image build failed" "bash docker/build.sh"
            fi
        elif [ "$IMAGE_PRESENT" = 1 ]; then
            fix "container image $IMAGE is from an older GUIDE-IEI version" "restart GUIDE-IEI to rebuild it automatically, or run: bash docker/build.sh"
        else
            fix "container image $IMAGE not built" "bash docker/build.sh   (one-time, downloads the ~2 GB base image)"
        fi

        if [ "$IMAGE_CURRENT" = 1 ]; then
            if "$CONTAINER_BIN" run --rm -v "$ROOT":/probe:ro --entrypoint sh "$IMAGE" -c 'test -d /probe/scripts' >/dev/null 2>&1; then
                ok "repo directory is mountable inside the container"
            else
                fix "cannot bind-mount $ROOT into the container" "colima: restart with --mount \"\$HOME:w\" covering your data; Docker Desktop: add the folder under Settings -> Resources -> File sharing"
            fi
        fi
    fi
fi

# ---------------------------------------------------------------- disk space
free_gb="$(df -Pk "$ROOT" 2>/dev/null | awk 'NR==2 {printf "%d", $4 / 1048576}')"
if [ -n "$free_gb" ]; then
    if [ "$free_gb" -ge "$DISK_MIN_EXOME_GB" ]; then
        ok "disk: ${free_gb} GiB free (exome reference set needs ~${DISK_MIN_EXOME_GB} GiB; dbNSFP adds ~50 GiB download + ~200 GiB one-time scratch; WGS extras are larger)"
    else
        wrn "disk: only ${free_gb} GiB free — the exome reference set alone needs ~${DISK_MIN_EXOME_GB} GiB (the Storage page can place datasets on another drive)"
    fi
fi

# ---------------------------------------------------------------- smoke test
if [ "$PYYAML_OK" = 1 ]; then
    if (cd "$ROOT" && PATH="$(dirname "$PYTHON_BIN"):$PATH" bash test/test_dry_run.sh >/dev/null 2>&1); then
        ok "pipeline smoke test passed (test/test_dry_run.sh — no container or references needed)"
    else
        fix "pipeline smoke test failed" "bash test/test_dry_run.sh   (rerun to see the failure)"
    fi
else
    note "pipeline smoke test skipped (needs PyYAML)"
fi

# ---------------------------------------------------------------- summary
echo
echo "== Summary: $PASS_COUNT ok, $WARN_COUNT warning(s), $FIX_COUNT to fix"
if [ "$FIX_COUNT" -gt 0 ]; then
    printf '%b' "$FIX_LINES"
    if [ "$MODE" = "check" ]; then
        echo "Run with --install to fix the user-space items automatically."
    fi
    exit 2
fi
echo "Environment ready. Next steps:"
echo "  bash scripts/start_workbench.sh            # launch the review workbench"
echo "  bash scripts/install_recommended_datasets.sh config/annotation.config.yaml exome"
echo "                                             # or use 'Set up annotation datasets' in the UI"
if [ "$DAEMON_UP" = 1 ]; then
    echo "  bash scripts/verify_container_stack.sh --quick   # optional: prove the container stack"
fi
exit 0
