/* Just enough of the Windows surface for ooz to compile.
 *
 * The timing calls are reached only from the upstream command-line driver,
 * which this build leaves out entirely, so stubs are sufficient.
 */
#pragma once
#include <stdint.h>

typedef struct _LARGE_INTEGER { int64_t QuadPart; } LARGE_INTEGER;

static inline int QueryPerformanceCounter(LARGE_INTEGER *p)   { p->QuadPart = 0; return 1; }
static inline int QueryPerformanceFrequency(LARGE_INTEGER *p) { p->QuadPart = 1; return 1; }
