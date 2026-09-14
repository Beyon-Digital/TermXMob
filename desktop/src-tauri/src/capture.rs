//! Screen capture and permission state that must run inside the Termx.app
//! process.
//!
//! macOS evaluates Screen Recording and Accessibility permissions against the
//! process performing the operation. Helpers spawned by the Python backend are
//! judged by their own code identity, so they never see the grant the user made
//! for Termx. The desktop shell therefore owns these privileged operations and
//! exposes them to the backend over a local socket (see `broker`).

#![cfg(target_os = "macos")]

use std::ffi::{c_char, c_void, CStr};

extern "C" {
    fn termx_capture_start(display_id: u32, max_width: i32, max_height: i32, fps: i32) -> i32;
    fn termx_capture_frame(out: *mut *mut u8, out_len: *mut usize, timeout_ms: i32, quality: i32)
        -> i32;
    fn termx_capture_stop();
    fn termx_bridge_last_error() -> *const c_char;
    fn termx_bridge_free(ptr: *mut c_void);
    fn termx_permission_preflight() -> i32;
    fn termx_accessibility_preflight() -> i32;
    fn termx_bridge_os_version() -> i32;
}

pub fn last_error() -> String {
    unsafe {
        let raw = termx_bridge_last_error();
        if raw.is_null() {
            return "unknown error".to_string();
        }
        CStr::from_ptr(raw).to_string_lossy().into_owned()
    }
}

pub fn os_version() -> String {
    let raw = unsafe { termx_bridge_os_version() };
    format!("{}.{}.{}", raw / 10000, (raw / 100) % 100, raw % 100)
}

#[derive(Debug)]
pub enum FrameError {
    /// No frame arrived within the timeout; the caller should keep waiting.
    Waiting,
    Denied(String),
    Other(String),
}

const SCREEN_RECORDING_DENIED: i32 = -3801;

pub fn start(display_id: u32, width: i32, height: i32, fps: i32) -> Result<(), String> {
    let code = unsafe { termx_capture_start(display_id, width, height, fps) };
    if code == 0 {
        return Ok(());
    }
    let message = last_error();
    if code == SCREEN_RECORDING_DENIED {
        Err(format!(
            "Screen Recording permission is required to mirror this Mac. {message}"
        ))
    } else {
        Err(message)
    }
}

pub fn frame(quality: i32, timeout_ms: i32) -> Result<Vec<u8>, FrameError> {
    let mut out: *mut u8 = std::ptr::null_mut();
    let mut len: usize = 0;
    let code = unsafe { termx_capture_frame(&mut out, &mut len, timeout_ms, quality) };
    if code == 0 && !out.is_null() && len > 0 {
        let data = unsafe { std::slice::from_raw_parts(out, len).to_vec() };
        unsafe { termx_bridge_free(out as *mut c_void) };
        return Ok(data);
    }
    if !out.is_null() {
        unsafe { termx_bridge_free(out as *mut c_void) };
    }
    let message = last_error();
    match code {
        -3 => Err(FrameError::Waiting),
        SCREEN_RECORDING_DENIED => Err(FrameError::Denied(message)),
        _ => Err(FrameError::Other(message)),
    }
}

pub fn stop() {
    unsafe { termx_capture_stop() };
}

pub fn screen_recording_granted() -> bool {
    unsafe { termx_permission_preflight() == 1 }
}

pub fn accessibility_granted() -> bool {
    unsafe { termx_accessibility_preflight() == 1 }
}
