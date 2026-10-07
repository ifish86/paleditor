/* The MSVC intrinsics ooz uses, mapped onto GCC/Clang builtins.
 *
 * GCC's own headers already provide _rotl and the SSE intrinsics, so defining
 * those here collides with ia32intrin.h. Only the bit scans and byteswaps are
 * missing.
 */
#pragma once
#include <stdint.h>
#include <string.h>
#include <immintrin.h>

#ifndef __forceinline
#define __forceinline inline __attribute__((always_inline))
#endif

static inline unsigned char _BitScanReverse(unsigned long *index, unsigned long mask) {
  if (!mask) return 0;
  *index = 31 - __builtin_clz((unsigned int)mask);
  return 1;
}

static inline unsigned char _BitScanForward(unsigned long *index, unsigned long mask) {
  if (!mask) return 0;
  *index = __builtin_ctz((unsigned int)mask);
  return 1;
}

static inline unsigned short    _byteswap_ushort(unsigned short v)    { return __builtin_bswap16(v); }
static inline unsigned long     _byteswap_ulong(unsigned long v)      { return __builtin_bswap32((uint32_t)v); }
static inline unsigned long long _byteswap_uint64(unsigned long long v) { return __builtin_bswap64(v); }
