#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define TERMX_VD_OK 0
#define TERMX_VD_UNAVAILABLE -1
#define TERMX_VD_DESCRIPTOR_FAILED -2
#define TERMX_VD_APPLY_FAILED -3
#define TERMX_VD_UNKNOWN -4

/// Returns 1 when the private CGVirtualDisplay API is present, 0 otherwise.
int32_t termx_virtual_display_available(void);

/// Creates a virtual display and keeps it alive until the process exits or
/// termx_virtual_display_release_all is called. Writes the display id to out_id.
int32_t termx_virtual_display_create(
    uint32_t width,
    uint32_t height,
    double refresh,
    const char *name,
    uint32_t *out_id
);

/// Releases all displays created by this process.
void termx_virtual_display_release_all(void);

#ifdef __cplusplus
}
#endif
