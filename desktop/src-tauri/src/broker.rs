//! Privileged broker: a local socket served by the Termx.app process.
//!
//! The Python backend owns HTTP/WebSocket handling but cannot perform
//! permission-gated work itself (macOS judges Screen Recording / Accessibility
//! against the performing process, not the app the user granted). The backend
//! connects to this socket and asks the shell to capture frames, inject input
//! and report its real permission state.
//!
//! Protocol: one JSON request per line. Responses are either a JSON line
//! (`{"ok":...}`) or, for `frame`, a binary reply: `b'F'` + 4-byte big-endian
//! length + JPEG bytes.

use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};

use std::sync::{Mutex, OnceLock};
use std::thread;

use serde_json::{json, Value};

static SOCKET_PATH: OnceLock<String> = OnceLock::new();
static APP_HANDLE: OnceLock<tauri::AppHandle> = OnceLock::new();

pub fn socket_path() -> Option<&'static str> {
    SOCKET_PATH.get().map(String::as_str)
}

#[cfg(target_os = "macos")]
pub fn start(app: tauri::AppHandle, log: impl Fn(&str) + Send + 'static) -> Option<String> {
    let _ = APP_HANDLE.set(app);

    let path = std::env::temp_dir().join(format!("termx-broker-{}.sock", std::process::id()));
    let _ = std::fs::remove_file(&path);
    let listener = match UnixListener::bind(&path) {
        Ok(listener) => listener,
        Err(error) => {
            log(&format!("broker: cannot bind {}: {error}", path.display()));
            return None;
        }
    };
    let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    let path_string = path.to_string_lossy().to_string();
    let _ = SOCKET_PATH.set(path_string.clone());


    let log_path = path_string.clone();
    thread::spawn(move || {
        log(&format!("broker: listening on {log_path}"));
        for stream in listener.incoming() {
            match stream {
                Ok(stream) => {
                    thread::spawn(move || {
                        if let Err(error) = serve(stream) {
                            let _ = error;
                        }
                    });
                }
                Err(_) => continue,
            }
        }
    });

    crate::capture::stop();
    Some(path_string)
}

#[cfg(not(target_os = "macos"))]
pub fn start(app: tauri::AppHandle, log: impl Fn(&str) + Send + 'static) -> Option<String> {
    let _ = (app, log);
    None
}

#[cfg(target_os = "macos")]
fn serve(mut stream: UnixStream) -> std::io::Result<()> {
    let reader = BufReader::new(stream.try_clone()?);
    for line in reader.lines() {
        let line = match line {
            Ok(line) => line,
            Err(_) => break,
        };
        if line.trim().is_empty() {
            continue;
        }
        let request: Value = match serde_json::from_str(&line) {
            Ok(value) => value,
            Err(error) => {
                write_json(&mut stream, json!({"ok": false, "error": format!("bad request: {error}")}))?;
                continue;
            }
        };
        if !handle_request(&mut stream, &request)? {
            break;
        }
    }
    Ok(())
}

#[cfg(target_os = "macos")]
fn write_json(stream: &mut UnixStream, value: Value) -> std::io::Result<()> {
    let mut payload = serde_json::to_vec(&value).unwrap_or_else(|_| b"{}".to_vec());
    payload.push(b'\n');
    stream.write_all(&payload)?;
    stream.flush()
}

#[cfg(target_os = "macos")]
fn handle_request(stream: &mut UnixStream, request: &Value) -> std::io::Result<bool> {
    let op = request.get("op").and_then(Value::as_str).unwrap_or("");
    match op {
        "ping" => {
            write_json(stream, json!({"ok": true, "pong": true}))?;
        }
        "status" => {
            write_json(
                stream,
                json!({
                    "ok": true,
                    "platform": "darwin",
                    "os_version": crate::capture::os_version(),
                    "screen_recording": crate::capture::screen_recording_granted(),
                    "accessibility": crate::capture::accessibility_granted(),
                    "capture_backend": "screencapturekit",
                    "shell_pid": std::process::id(),
                }),
            )?;
        }
        "request" => {
            let which = request.get("which").and_then(Value::as_str).unwrap_or("");
            // Ask from inside the app process (the identity the user manages in
            // System Settings) and open the matching settings pane when macOS
            // has already recorded a decision, where it will not prompt again.
            if let Some(app) = APP_HANDLE.get() {
                crate::ui::permission_event(app, which);
            }
            write_json(
                stream,
                json!({
                    "ok": true,
                    "granted": match which {
                        "screen_recording" => crate::capture::screen_recording_granted(),
                        "accessibility" => crate::capture::accessibility_granted(),
                        _ => false,
                    },
                    "screen_recording": crate::capture::screen_recording_granted(),
                    "accessibility": crate::capture::accessibility_granted(),
                }),
            )?;
        }
        "frame" => {
            // The backend may send the display id as a JSON number or string.
            let display = match request.get("display") {
                Some(Value::Number(number)) => number.as_u64().unwrap_or(0) as u32,
                Some(Value::String(text)) => text.parse::<u32>().unwrap_or(0),
                _ => 0,
            };
            let width = request.get("width").and_then(Value::as_i64).unwrap_or(0) as i32;
            let height = request.get("height").and_then(Value::as_i64).unwrap_or(0) as i32;
            let fps = request.get("fps").and_then(Value::as_i64).unwrap_or(12) as i32;
            let quality = request.get("quality").and_then(Value::as_i64).unwrap_or(55) as i32;
            let timeout = request
                .get("timeout_ms")
                .and_then(Value::as_i64)
                .unwrap_or(400) as i32;

            let _guard = CAPTURE_LOCK.lock().unwrap_or_else(|error| error.into_inner());
            if let Err(error) = crate::capture::start(display, width, height, fps) {
                write_json(stream, json!({"ok": false, "kind": "error", "error": error}))?;
                return Ok(true);
            }
            match crate::capture::frame(quality, timeout) {
                Ok(bytes) => {
                    stream.write_all(b"F")?;
                    stream.write_all(&(bytes.len() as u32).to_be_bytes())?;
                    stream.write_all(&bytes)?;
                    stream.flush()?;
                }
                Err(crate::capture::FrameError::Waiting) => {
                    write_json(stream, json!({"ok": false, "kind": "waiting", "error": "no frame yet"}))?;
                }
                Err(crate::capture::FrameError::Denied(message)) => {
                    write_json(stream, json!({"ok": false, "kind": "denied", "error": message}))?;
                }
                Err(crate::capture::FrameError::Other(message)) => {
                    write_json(stream, json!({"ok": false, "kind": "error", "error": message}))?;
                }
            }
        }
        "stop" => {
            crate::capture::stop();
            write_json(stream, json!({"ok": true}))?;
        }
        "input" => {
            let result = apply_input(request);
            match result {
                Ok(()) => write_json(stream, json!({"ok": true}))?,
                Err(error) => write_json(stream, json!({"ok": false, "error": error}))?,
            }
        }
        "" => {
            write_json(stream, json!({"ok": false, "error": "missing op"}))?;
        }
        other => {
            write_json(stream, json!({"ok": false, "error": format!("unknown op {other}")}))?;
        }
    }
    Ok(true)
}

#[cfg(target_os = "macos")]
static CAPTURE_LOCK: Mutex<()> = Mutex::new(());

#[cfg(target_os = "macos")]
fn apply_input(request: &Value) -> Result<(), String> {
    use crate::input_macos;

    let kind = request.get("kind").and_then(Value::as_str).unwrap_or("");
    let flags = request
        .get("modifiers")
        .and_then(Value::as_array)
        .map(|items| {
            let names: Vec<String> = items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect();
            input_macos::modifier_flags(&names)
        })
        .unwrap_or(0);
    match kind {
        "mouse" => {
            let x = request.get("x").and_then(Value::as_f64).unwrap_or(0.0);
            let y = request.get("y").and_then(Value::as_f64).unwrap_or(0.0);
            let action = request.get("event").and_then(Value::as_str).unwrap_or("move");
            let button = request.get("button").and_then(Value::as_i64).unwrap_or(1) as i32;
            let dragging = request.get("dragging").and_then(Value::as_bool).unwrap_or(false);
            input_macos::mouse(x, y, action, button, dragging, flags)
        }
        "scroll" => {
            let dy = request.get("dy").and_then(Value::as_f64).unwrap_or(0.0);
            let dx = request.get("dx").and_then(Value::as_f64).unwrap_or(0.0);
            input_macos::scroll(dy, dx, flags)
        }
        "key" => {
            let down = request.get("down").and_then(Value::as_bool).unwrap_or(true);
            if let Some(code) = request.get("code").and_then(Value::as_u64) {
                return input_macos::key(code as u16, down, flags);
            }
            let name = request.get("key").and_then(Value::as_str).unwrap_or("");
            match input_macos::key_code(name) {
                Some(code) => input_macos::key(code, down, flags),
                None => input_macos::text(name, flags),
            }
        }
        "text" => {
            let data = request.get("text").and_then(Value::as_str).unwrap_or("");
            input_macos::text(data, flags)
        }
        "release_all" => input_macos::release_all(),
        other => Err(format!("unknown input kind {other}")),
    }
}

// Silences unused warnings on non-macOS builds where the socket server is absent.
#[allow(dead_code)]
fn _unused(stream: &mut UnixStream) {
    let _ = stream.read(&mut [0u8; 0]);
}
