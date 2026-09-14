#ifndef TERMX_CAPTURE_BRIDGE_H
#define TERMX_CAPTURE_BRIDGE_H

#include <stddef.h>
#include <stdint.h>

// Screen capture and permission helpers that must run inside the Termx.app
// process: macOS evaluates Screen Recording / Accessibility against the
// process that performs the operation, so helper binaries spawned by the
// backend are judged by their own identity and never see the app's grant.

// Start (or switch to) a stream for a CoreGraphics display id. 0 selects the
// main display. max_width/max_height of 0 keep the display's native pixel size.
// Returns 0 on success, negative on error (see termx_bridge_last_error).
int32_t termx_capture_start(uint32_t display_id, int32_t max_width, int32_t max_height, int32_t fps);

// Copy the newest JPEG frame into *out (malloc'ed, free with termx_bridge_free).
// Returns 0 on success, -3 when no frame arrived within timeout_ms, negative on
// error.
int32_t termx_capture_frame(uint8_t **out, size_t *out_len, int32_t timeout_ms, int32_t quality);

void termx_capture_stop(void);

// Last error string (thread-local static buffer, valid until the next call).
const char *termx_bridge_last_error(void);

void termx_bridge_free(void *ptr);

// Permission state as seen by this process.
int32_t termx_permission_preflight(void);
int32_t termx_permission_request(void);
int32_t termx_accessibility_preflight(void);
int32_t termx_accessibility_request(void);

// macOS version without importing AppKit in Rust.
int32_t termx_bridge_os_version(void);

#endif
