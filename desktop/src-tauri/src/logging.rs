use std::fs::OpenOptions;
use std::io::Write;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::AppHandle;

use crate::config;

fn timestamp() -> String {
    match SystemTime::now().duration_since(UNIX_EPOCH) {
        Ok(duration) => format!("{}.{:03}", duration.as_secs(), duration.subsec_millis()),
        Err(_) => "0.000".to_string(),
    }
}

fn append(app: &AppHandle, file: &str, line: &str) {
    let path = config::log_dir(app).join(file);
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(mut handle) = OpenOptions::new().create(true).append(true).open(path) {
        let _ = writeln!(handle, "[{}] {}", timestamp(), line);
    }
}

pub fn desktop(app: &AppHandle, line: &str) {
    append(app, "termx-desktop.log", line);
}

pub fn backend(app: &AppHandle, line: &str) {
    append(app, "termx-backend.log", line);
}
