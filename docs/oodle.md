# The Oodle library

paleditor cannot read this server's saves without a native Oodle decompressor,
and it does not ship one.

## Why it is not included

`Level.sav` uses the `PlM` container, which is Oodle Mermaid. There is no
pure-Python decoder, so paleditor loads a shared library through `ctypes`.

The library everyone uses is [powzix/ooz](https://github.com/powzix/ooz), an
open reimplementation of the Oodle decompressor. **It declares no licence.**
Under default copyright that means all rights reserved: we have no permission
to redistribute it, as a binary or as vendored source, and certainly not inside
a GPL-3.0 repository. Shipping it would be convenient and wrong.

So paleditor builds it instead. Nothing is redistributed; the source is fetched
on your machine, at a commit this repo pins, and compiled there.

## Building it

```bash
scripts/oodle/build.sh --save-dir "/path/to/SaveGames/0/<world-id>"
```

That fetches commit `0503806` of powzix/ooz, compiles a decompressor, **checks
it can actually decompress your world**, and only then installs
`lib/libooz.so`. Without `--save-dir` it checks the library loads and the
symbol is callable, which is weaker; pass a save if you have one.

To install somewhere paleditor searches by default:

```bash
sudo scripts/oodle/build.sh --prefix /opt/paleditor/lib \
  --save-dir "/home/palworld/palworld-server/Pal/Saved/SaveGames/0/<world-id>"
```

Search order: `./lib`, `/opt/paleditor/lib`, `~/.local/lib`, `/usr/local/lib`,
`/usr/lib`. Anywhere else, set `[palworld] oodle_library`.

## What gets built

Decompression only. paleditor reads the Oodle container and writes the zlib
one, so a compressor would be dead weight — and the forks that carry one are
not dependable: the `OodLZ_Compress` exported by some builds segfaults when
called. That is why the write path produces `PlZ`, and why
`paleditor check-write` exists.

Upstream is Windows C++, so the build supplies a small compatibility layer in
`scripts/oodle/compat/`: stubs for `SDKDDKVer.h`, `tchar.h` and `Windows.h`,
plus the handful of MSVC intrinsics GCC spells differently (`_BitScanReverse`,
`_byteswap_*`). GCC already provides `_rotl` and the SSE intrinsics, so
defining those again collides with `ia32intrin.h`.

The upstream command-line driver is dropped: it resolves the real Oodle DLL
through `LoadLibrary`/`GetProcAddress`, which cannot build or run here. The
build cuts from that declaration onward and checks `Kraken_Decompress` survived,
so an upstream reshuffle fails loudly rather than producing a library missing
its decoder.

## Verification status

The verification step — select the newest completed snapshot, decompress it,
confirm the payload is `GVAS` — has been run against the real world and
correctly handled a 31,466,305-byte save.

The **compile** step has not been executed end to end here: the sandbox this
was written in blocks compiling fetched source. The pieces were taken as far as
they could be, and the script refuses to install anything that does not produce
an `ooz_decompress` symbol and pass the decompression check, so a bad build
fails rather than being quietly installed. If it fails on your machine, the
compiler error is the interesting part; send it over.

## If you already have a build

Any `libooz.so` exporting `ooz_decompress` with the signature

```c
int ooz_decompress(const uint8_t *src, size_t src_len, uint8_t *dst, size_t dst_len);
```

will do. Point at it:

```toml
[palworld]
oodle_library = "/path/to/libooz.so"
```
