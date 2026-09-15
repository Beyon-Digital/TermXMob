//! CoreGraphics event injection executed inside the Termx.app process.
//!
//! Posting events requires the Accessibility grant, which macOS evaluates
//! against the process posting the event. Running this in the shell (instead of
//! the Python backend) means the user's grant for Termx actually applies.

#![cfg(target_os = "macos")]

use std::ffi::c_void;

type CGEventRef = *mut c_void;
type CGEventSourceRef = *mut c_void;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct CGPoint {
    pub x: f64,
    pub y: f64,
}

#[link(name = "CoreGraphics", kind = "framework")]
extern "C" {
    fn CGEventCreate(source: CGEventSourceRef) -> CGEventRef;
    fn CGEventGetLocation(event: CGEventRef) -> CGPoint;
    fn CGEventCreateMouseEvent(
        source: CGEventSourceRef,
        mouse_type: u32,
        mouse_cursor_position: CGPoint,
        mouse_button: u32,
    ) -> CGEventRef;
    fn CGEventCreateScrollWheelEvent2(
        source: CGEventSourceRef,
        units: u32,
        wheel_count: u32,
        wheel1: i32,
        wheel2: i32,
        wheel3: i32,
    ) -> CGEventRef;
    fn CGEventCreateKeyboardEvent(source: CGEventSourceRef, virtual_key: u16, key_down: bool)
        -> CGEventRef;
    fn CGEventKeyboardSetUnicodeString(event: CGEventRef, length: usize, string: *const u16);
    fn CGEventSetFlags(event: CGEventRef, flags: u64);
    fn CGEventPost(tap: u32, event: CGEventRef);
    fn CGEventSourceCreate(state_id: i32) -> CGEventSourceRef;
    fn CFRelease(value: *const c_void);
}

const HID_SYSTEM_STATE: i32 = 1;

const LEFT_MOUSE_DOWN: u32 = 1;
const LEFT_MOUSE_UP: u32 = 2;
const RIGHT_MOUSE_DOWN: u32 = 3;
const RIGHT_MOUSE_UP: u32 = 4;
const MOUSE_MOVED: u32 = 5;
const LEFT_MOUSE_DRAGGED: u32 = 6;
const RIGHT_MOUSE_DRAGGED: u32 = 7;

const SCROLL_UNIT_LINE: u32 = 1;

const FLAG_SHIFT: u64 = 1 << 17;
const FLAG_CONTROL: u64 = 1 << 18;
const FLAG_OPTION: u64 = 1 << 19;
const FLAG_COMMAND: u64 = 1 << 20;

fn source() -> CGEventSourceRef {
    unsafe { CGEventSourceCreate(HID_SYSTEM_STATE) }
}

fn post(event: CGEventRef) {
    if event.is_null() {
        return;
    }
    unsafe {
        CGEventPost(0, event);
        CFRelease(event as *const c_void);
    }
}

pub fn modifier_flags(names: &[String]) -> u64 {
    let mut flags = 0u64;
    for name in names {
        match name.as_str() {
            "shift" => flags |= FLAG_SHIFT,
            "control" | "ctrl" => flags |= FLAG_CONTROL,
            "option" | "alt" => flags |= FLAG_OPTION,
            "command" | "cmd" | "meta" => flags |= FLAG_COMMAND,
            _ => {}
        }
    }
    flags
}

pub fn mouse(x: f64, y: f64, action: &str, button: i32, dragging: bool, flags: u64) -> Result<(), String> {
    let point = CGPoint { x, y };
    let (down, up, drag) = if button == 2 {
        (RIGHT_MOUSE_DOWN, RIGHT_MOUSE_UP, RIGHT_MOUSE_DRAGGED)
    } else {
        (LEFT_MOUSE_DOWN, LEFT_MOUSE_UP, LEFT_MOUSE_DRAGGED)
    };
    let event_type = match action {
        "move" => {
            if dragging {
                drag
            } else {
                MOUSE_MOVED
            }
        }
        "drag" => drag,
        "down" => down,
        "up" => up,
        "click" => down,
        "right_click" => RIGHT_MOUSE_DOWN,
        _ => MOUSE_MOVED,
    };
    let event = unsafe {
        CGEventCreateMouseEvent(source(), event_type, point, (button - 1).max(0) as u32)
    };
    if event.is_null() {
        return Err("cannot create a mouse event".to_string());
    }
    if flags != 0 {
        unsafe { CGEventSetFlags(event, flags) };
    }
    post(event);
    if action == "click" || action == "right_click" {
        let up_type = if action == "right_click" {
            RIGHT_MOUSE_UP
        } else {
            LEFT_MOUSE_UP
        };
        let release =
            unsafe { CGEventCreateMouseEvent(source(), up_type, point, (button - 1).max(0) as u32) };
        if flags != 0 && !release.is_null() {
            unsafe { CGEventSetFlags(release, flags) };
        }
        post(release);
    }
    Ok(())
}

pub fn pointer_location() -> CGPoint {
    unsafe {
        let event = CGEventCreate(source());
        if event.is_null() {
            return CGPoint { x: 0.0, y: 0.0 };
        }
        let point = CGEventGetLocation(event);
        CFRelease(event as *const c_void);
        point
    }
}

/// Move the cursor by a delta, the way a hardware trackpad does.
pub fn relative_move(dx: f64, dy: f64, flags: u64) -> Result<(), String> {
    if dx == 0.0 && dy == 0.0 {
        return Ok(());
    }
    let current = pointer_location();
    mouse(current.x + dx, current.y + dy, "move", 1, false, flags)
}

pub fn scroll(dy: f64, dx: f64, flags: u64) -> Result<(), String> {
    if dy == 0.0 && dx == 0.0 {
        return Ok(());
    }
    let lines_v = if dy.abs() > 0.0 { (-dy) as i32 } else { 0 };
    let lines_h = dx as i32;
    let event = unsafe {
        CGEventCreateScrollWheelEvent2(source(), SCROLL_UNIT_LINE, 2, lines_v, lines_h, 0)
    };
    if event.is_null() {
        return Err("cannot create a scroll event".to_string());
    }
    if flags != 0 {
        unsafe { CGEventSetFlags(event, flags) };
    }
    post(event);
    Ok(())
}

pub fn key(code: u16, down: bool, flags: u64) -> Result<(), String> {
    let event = unsafe { CGEventCreateKeyboardEvent(source(), code, down) };
    if event.is_null() {
        return Err("cannot create a keyboard event".to_string());
    }
    if flags != 0 {
        unsafe { CGEventSetFlags(event, flags) };
    }
    post(event);
    Ok(())
}

pub fn text(data: &str, flags: u64) -> Result<(), String> {
    if data.is_empty() {
        return Ok(());
    }
    for character in data.chars() {
        let mut buffer: Vec<u16> = Vec::new();
        buffer.push(character as u16);
        if character as u32 > 0xFFFF {
            buffer.clear();
            let mut units = [0u16; 2];
            let encoded = character.encode_utf16(&mut units);
            buffer.extend_from_slice(encoded);
        }
        let event = unsafe { CGEventCreateKeyboardEvent(source(), 0, true) };
        if event.is_null() {
            return Err("cannot create a keyboard event".to_string());
        }
        unsafe { CGEventKeyboardSetUnicodeString(event, buffer.len(), buffer.as_ptr()) };
        if flags != 0 {
            unsafe { CGEventSetFlags(event, flags) };
        }
        post(event);
        let release = unsafe { CGEventCreateKeyboardEvent(source(), 0, false) };
        if !release.is_null() {
            unsafe { CGEventKeyboardSetUnicodeString(release, buffer.len(), buffer.as_ptr()) };
            if flags != 0 {
                unsafe { CGEventSetFlags(release, flags) };
            }
        }
        post(release);
    }
    Ok(())
}

/// Physical key codes for keys that have no Unicode representation.
pub fn key_code(name: &str) -> Option<u16> {    let code = match name {
        "return" | "enter" => 36,
        "tab" => 48,
        "space" | " " => 49,
        "delete" | "backspace" => 51,
        "escape" | "esc" => 53,
        "command" | "meta" => 55,
        "shift" => 56,
        "capslock" => 57,
        "option" | "alt" => 58,
        "control" | "ctrl" => 59,
        "right_shift" => 60,
        "right_option" => 61,
        "right_control" => 62,
        "function" | "fn" => 63,
        "f17" => 64,
        "volume_up" => 72,
        "volume_down" => 73,
        "mute" => 74,
        "f18" => 79,
        "f19" => 80,
        "f20" => 90,
        "f5" => 96,
        "f6" => 97,
        "f7" => 98,
        "f3" => 99,
        "f8" => 100,
        "f9" => 101,
        "f11" => 103,
        "f13" => 105,
        "f16" => 106,
        "f14" => 107,
        "f10" => 109,
        "f12" => 111,
        "f15" => 113,
        "help" | "insert" => 114,
        "home" => 115,
        "pageup" | "page_up" => 116,
        "forward_delete" | "delete_forward" => 117,
        "f4" => 118,
        "end" => 119,
        "f2" => 120,
        "pagedown" | "page_down" => 121,
        "f1" => 122,
        "left" | "arrowleft" => 123,
        "right" | "arrowright" => 124,
        "down" | "arrowdown" => 125,
        "up" | "arrowup" => 126,
        _ => return None,
    };
    Some(code)
}

/// US-layout virtual key codes for printable characters.
///
/// Modifier chords (⌘C, ⌘V, Shift+letter) only take effect on real key events;
/// `CGEventKeyboardSetUnicodeString` ignores modifier flags, so characters that
/// need modifiers must be posted by key code instead.
pub fn char_key_code(character: &str) -> Option<u16> {
    let code = match character {
        "a" => 0,
        "s" => 1,
        "d" => 2,
        "f" => 3,
        "h" => 4,
        "g" => 5,
        "z" => 6,
        "x" => 7,
        "c" => 8,
        "v" => 9,
        "b" => 11,
        "q" => 12,
        "w" => 13,
        "e" => 14,
        "r" => 15,
        "y" => 16,
        "t" => 17,
        "1" => 18,
        "2" => 19,
        "3" => 20,
        "4" => 21,
        "6" => 22,
        "5" => 23,
        "=" => 24,
        "9" => 25,
        "7" => 26,
        "-" => 27,
        "8" => 28,
        "0" => 29,
        "]" => 30,
        "o" => 31,
        "u" => 32,
        "[" => 33,
        "i" => 34,
        "p" => 35,
        "l" => 37,
        "j" => 38,
        "'" => 39,
        "k" => 40,
        ";" => 41,
        "\\" => 42,
        "," => 43,
        "/" => 44,
        "n" => 45,
        "m" => 46,
        "." => 47,
        "`" => 50,
        _ => return None,
    };
    Some(code)
}

pub fn release_all() -> Result<(), String> {
    for code in [55u16, 56, 58, 59, 60, 61, 62] {
        let _ = key(code, false, 0);
    }
    for button in [1, 2] {
        let _ = mouse(0.0, 0.0, "up", button, false, 0);
    }
    Ok(())
}
