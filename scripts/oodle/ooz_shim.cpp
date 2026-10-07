/* A C entry point for paleditor.
 *
 * Upstream is C++ with no stable C ABI, so ctypes needs an unmangled symbol.
 * Decompression only: paleditor reads the Oodle (PlM) container and writes the
 * zlib (PlZ) one, so there is nothing here that compresses.
 */
#include <stddef.h>

typedef unsigned char byte;

int Kraken_Decompress(const byte *src, size_t src_len, byte *dst, size_t dst_len);

extern "C" int ooz_decompress(const byte *src, size_t src_len,
                              byte *dst, size_t dst_len) {
  return Kraken_Decompress(src, src_len, dst, dst_len);
}
