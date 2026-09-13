use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;

use crate::backend::{api_get, api_post, Backend, ReadyInfo};
use crate::{config, logging};

pub static START_HIDDEN: AtomicBool = AtomicBool::new(false);

pub fn main_window(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("main")
}

pub fn main_window_visible(app: &AppHandle) -> bool {
    main_window(app)
        .map(|window| window.is_visible().unwrap_or(false))
        .unwrap_or(false)
}

pub fn create_main_window(app: &AppHandle) -> tauri::Result<WebviewWindow> {
    let builder = WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
        .title("Termx")
        .inner_size(1280.0, 820.0)
        .min_inner_size(760.0, 520.0)
        .visible(false)
        .resizable(true);
    #[cfg(not(target_os = "macos"))]
    let builder = builder.menu(crate::menu::app_menu(app)?);
    builder.build()
}

pub fn show_main_window(app: &AppHandle) {
    if let Some(window) = main_window(app) {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    } else if let Ok(window) = create_main_window(app) {
        let _ = window.show();
    }
}

pub fn toggle_main_window(app: &AppHandle) {
    match main_window(app) {
        Some(window) => {
            if window.is_visible().unwrap_or(false) {
                let _ = window.hide();
            } else {
                let _ = window.show();
                let _ = window.set_focus();
            }
        }
        None => show_main_window(app),
    }
}

pub fn on_backend_ready(app: &AppHandle, info: &ReadyInfo) {
    let window = main_window(app).or_else(|| create_main_window(app).ok());
    if let Some(window) = window {
        if let Ok(url) = info.window_url().parse() {
            let _ = window.navigate(url);
        }
        if !START_HIDDEN.load(Ordering::SeqCst) {
            let _ = window.show();
        }
    }
    if let Some(connect) = app.get_webview_window("connect") {
        if let Ok(url) = connect_url(info).parse() {
            let _ = connect.navigate(url);
        }
    }
}

pub fn on_backend_failed(app: &AppHandle, message: &str) {
    error_dialog(app, "Termx backend failed", message);
    if let Some(window) = main_window(app) {
        let script = format!(
            "document.getElementById('status') && (document.getElementById('status').textContent = {});",
            serde_json::to_string(message).unwrap_or_else(|_| "\"Backend failed\"".into())
        );
        let _ = window.eval(&script);
        let _ = window.show();
    }
}

pub fn notify(app: &AppHandle, title: &str, body: &str) {
    let _ = app
        .notification()
        .builder()
        .title(title)
        .body(body)
        .show();
}

fn connect_url(info: &ReadyInfo) -> String {
    format!(
        "http://127.0.0.1:{}/_/connect.html?k={}",
        info.port, info.passcode
    )
}

pub fn open_connect_window(app: &AppHandle) {
    let Some(info) = app.try_state::<Backend>().and_then(|backend| backend.info()) else {
        notify(app, "Termx is starting", "Connection details are not ready yet.");
        return;
    };
    let url = connect_url(&info);
    if let Some(window) = app.get_webview_window("connect") {
        if let Ok(parsed) = url.parse() {
            let _ = window.navigate(parsed);
        }
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    let builder = WebviewWindowBuilder::new(app, "connect", WebviewUrl::External(url.parse().unwrap()))
        .title("Termx — Connect")
        .inner_size(440.0, 760.0)
        .min_inner_size(360.0, 520.0)
        .resizable(true);
    match builder.build() {
        Ok(window) => {
            let _ = window.show();
        }
        Err(error) => {
            logging::desktop(app, &format!("failed to open connect window: {error}"));
        }
    }
}

pub fn copy_connect_link(app: &AppHandle) {
    let Some(info) = app.try_state::<Backend>().and_then(|backend| backend.info()) else {
        notify(app, "Termx is starting", "Connection details are not ready yet.");
        return;
    };
    let link = match api_get(app, "/api/connect")
        .and_then(|value| value.get("connect_url").and_then(|item| item.as_str()).map(str::to_string))
    {
        Some(link) => link,
        None => info.window_url(),
    };
    match app.clipboard().write_text(link.clone()) {
        Ok(()) => notify(app, "Connection link copied", &link),
        Err(error) => logging::desktop(app, &format!("clipboard write failed: {error}")),
    }
}

pub fn open_in_browser(app: &AppHandle) {
    let Some(info) = app.try_state::<Backend>().and_then(|backend| backend.info()) else {
        return;
    };
    let url = info.window_url();
    let _ = app.opener().open_url(url, None::<&str>);
}

pub fn open_logs(app: &AppHandle) {
    let path = config::log_dir(app);
    let _ = app.opener().open_path(path.to_string_lossy(), None::<&str>);
}

pub fn open_permission_settings(app: &AppHandle) {
    let _ = app.opener().open_url(
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        None::<&str>,
    );
}

pub fn open_accessibility_settings(app: &AppHandle) {
    let _ = app.opener().open_url(
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        None::<&str>,
    );
}

fn permission_summary(app: &AppHandle) -> String {
    let value = api_get(app, "/api/permissions");
    let get = |key: &str| {
        value
            .as_ref()
            .and_then(|item| item.get(key))
            .and_then(|item| item.as_str())
            .unwrap_or("unknown")
            .to_string()
    };
    let mut lines = vec![
        format!("Screen Recording: {}", get("screen_recording")),
        format!("Accessibility: {}", get("accessibility")),
    ];
    if value.is_none() {
        lines = vec!["Termx backend is not running yet.".to_string()];
    }
    lines.join("\n")
}

pub fn permission_dialog(app: &AppHandle) {
    if !cfg!(target_os = "macos") {
        notify(
            app,
            "No desktop permissions required",
            "Screen capture on this platform is handled by the compositor or is unsupported.",
        );
        return;
    }
    let summary = permission_summary(app);
    let handle = app.clone();
    app.dialog()
        .message(format!(
            "Termx uses two macOS permissions:\n\n• Screen Recording — stream your desktop to your phone\n• Accessibility — send pointer and keyboard input\n\nCurrent status:\n{summary}\n\nRequest permissions now?",
        ))
        .title("Termx Permissions")
        .buttons(MessageDialogButtons::OkCustom("Request".to_string()))
        .show(move |requested| {
            if requested {
                let _ = api_post(&handle, "/api/permissions/request", serde_json::json!({}));
            }
            config::set_onboarded(&handle);
            let status = api_get(&handle, "/api/permissions");
            let denied = |key: &str| {
                status
                    .as_ref()
                    .and_then(|value| value.get(key))
                    .and_then(|value| value.as_str())
                    .map(|value| value == "denied")
                    .unwrap_or(false)
            };
            if denied("screen_recording") {
                open_permission_settings(&handle);
            } else if denied("accessibility") {
                open_accessibility_settings(&handle);
            }
            notify(
                &handle,
                "Grant permissions to Termx",
                "Enable Screen Recording and Accessibility for Termx, then restart the app if capture still fails.",
            );
        });
}

pub fn error_dialog(app: &AppHandle, title: &str, message: &str) {
    app.dialog()
        .message(message)
        .title(title)
        .buttons(MessageDialogButtons::Ok)
        .show(|_| {});
}

#[cfg(target_os = "macos")]
pub fn first_run_onboarding(app: &AppHandle, onboarded: bool) {
    if onboarded {
        return;
    }
    let handle = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_secs(2));
        for _ in 0..80 {
            let ready = handle
                .try_state::<Backend>()
                .and_then(|backend| backend.info())
                .is_some();
            if ready {
                break;
            }
            std::thread::sleep(std::time::Duration::from_millis(250));
        }
        permission_dialog(&handle);
    });
}

#[cfg(not(target_os = "macos"))]
pub fn first_run_onboarding(_app: &AppHandle, _onboarded: bool) {}
