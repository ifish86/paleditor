#!/usr/bin/env bash
# Build the Oodle decompressor paleditor needs to read PlM saves.
#
# paleditor does not ship this library. Its upstream, powzix/ooz, declares no
# licence at all, which under default copyright means it is not ours to
# redistribute - not as a binary, not as vendored source. So this fetches a
# pinned commit, builds a decompressor from it, and checks the result actually
# works before installing anything.
#
#   scripts/oodle/build.sh                      # install into ./lib
#   scripts/oodle/build.sh --prefix /opt/paleditor/lib
#   scripts/oodle/build.sh --save-dir /path/to/world   # verify against a real save
#
# Only decompression is built. paleditor reads the Oodle container and writes
# the zlib one, so a compressor would be dead weight - and the forks that have
# one are not reliable.
set -euo pipefail

UPSTREAM="https://github.com/powzix/ooz.git"
# Pinned. An unpinned clone of an unlicensed, unmaintained repository is not
# something to run through a compiler unattended.
COMMIT="05038060aa68f9187ae9923b2388ca8db40e58d1"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
PREFIX="$REPO_ROOT/lib"
SAVE_DIR=""
KEEP_BUILD=0

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix)    PREFIX="$2"; shift 2 ;;
    --save-dir)  SAVE_DIR="$2"; shift 2 ;;
    --keep)      KEEP_BUILD=1; shift ;;
    -h|--help)   sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }

for tool in git g++ python3; do
  command -v "$tool" >/dev/null || die "$tool is required but not installed"
done

BUILD_DIR="$(mktemp -d -t paleditor-oodle-XXXXXX)"
cleanup() { [ "$KEEP_BUILD" -eq 1 ] || rm -rf "$BUILD_DIR"; }
trap cleanup EXIT

echo "==> fetching $UPSTREAM at $COMMIT"
git -C "$BUILD_DIR" init -q
git -C "$BUILD_DIR" remote add origin "$UPSTREAM"
git -C "$BUILD_DIR" fetch -q --depth 1 origin "$COMMIT"
git -C "$BUILD_DIR" checkout -q FETCH_HEAD

actual="$(git -C "$BUILD_DIR" rev-parse HEAD)"
[ "$actual" = "$COMMIT" ] || die "expected commit $COMMIT, got $actual"

echo "==> preparing sources"
# The upstream command-line driver links against the real Oodle DLL through
# LoadLibrary/GetProcAddress. Everything from its first declaration onward is
# dropped; what remains is the decoder.
sed '/^typedef int WINAPI OodLZ_CompressFunc(/,$d' "$BUILD_DIR/kraken.cpp" \
  > "$BUILD_DIR/kraken_lib.cpp"
grep -q 'Kraken_Decompress' "$BUILD_DIR/kraken_lib.cpp" \
  || die "Kraken_Decompress missing after trimming; upstream layout changed"
cp "$HERE/ooz_shim.cpp" "$BUILD_DIR/"

echo "==> compiling"
g++ -O2 -fPIC -shared -std=c++14 -w \
  -I"$HERE/compat" -include msvc_types.h \
  -o "$BUILD_DIR/libooz.so" \
  "$BUILD_DIR/bitknit.cpp" "$BUILD_DIR/kraken_lib.cpp" \
  "$BUILD_DIR/lzna.cpp" "$BUILD_DIR/ooz_shim.cpp"

nm -D --defined-only "$BUILD_DIR/libooz.so" | grep -q ' ooz_decompress$' \
  || die "the build produced no ooz_decompress symbol"

echo "==> verifying"
if [ -z "$SAVE_DIR" ]; then
  echo "    no --save-dir given; checking the library loads and is callable only."
  echo "    Re-run with --save-dir to decompress a real save before installing."
fi
SAVE_DIR="$SAVE_DIR" python3 - "$BUILD_DIR/libooz.so" <<'PYEOF'
import ctypes, os, struct, sys
from pathlib import Path

lib_path = sys.argv[1]
lib = ctypes.CDLL(lib_path)
lib.ooz_decompress.argtypes = [ctypes.c_char_p, ctypes.c_size_t,
                               ctypes.c_char_p, ctypes.c_size_t]
lib.ooz_decompress.restype = ctypes.c_int
print("    loaded and ooz_decompress is callable")

save_dir = os.environ.get("SAVE_DIR") or ""
if not save_dir:
    sys.exit(0)

root = Path(save_dir)
candidates = []
backups = root / "backup" / "world"
if backups.is_dir():
    candidates += sorted(
        (d / "Level.sav" for d in backups.iterdir() if (d / "Level.sav").is_file()),
        key=lambda p: p.parent.name,
    )[-1:]
if (root / "Level.sav").is_file():
    candidates.append(root / "Level.sav")
if not candidates:
    sys.exit(f"    no Level.sav found under {root}")

target = candidates[0]
raw = target.read_bytes()
uncompressed, compressed = struct.unpack_from("<II", raw, 0)
magic = raw[8:11]
if magic != b"PlM":
    print(f"    {target.name} is {magic!r}, not an Oodle save; nothing to verify")
    sys.exit(0)
if compressed != len(raw) - 12:
    sys.exit(f"    {target} looks truncated; pick a completed snapshot")

buf = ctypes.create_string_buffer(uncompressed + 64)
written = lib.ooz_decompress(raw[12:], len(raw) - 12, buf, uncompressed)
if written != uncompressed:
    sys.exit(f"    decompression returned {written}, expected {uncompressed}")
if buf.raw[:4] != b"GVAS":
    sys.exit(f"    decompressed payload starts {buf.raw[:4]!r}, not GVAS")
print(f"    decompressed {target.name}: {written:,} bytes of GVAS")
PYEOF

echo "==> installing"
mkdir -p "$PREFIX"
install -m 0644 "$BUILD_DIR/libooz.so" "$PREFIX/libooz.so"
echo "    $PREFIX/libooz.so"
echo
echo "paleditor searches ./lib, /opt/paleditor/lib, ~/.local/lib, /usr/local/lib"
echo "and /usr/lib. If you installed elsewhere, set [palworld] oodle_library."
