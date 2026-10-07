use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
#[cfg(target_os = "macos")]
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_opener::OpenerExt;

use crate::backend::{gql_app, Backend, ReadyInfo};
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
    let window = builder.build()?;
    crate::workspace::restore_placement(app, &window);
    crate::workspace::track_placement(app, &window);
    Ok(window)
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
    let _ = app.notification().builder().title(title).body(body).show();
}

fn connect_url(info: &ReadyInfo) -> String {
    format!("http://127.0.0.1:{}/?manager=access", info.port)
}

pub fn open_connect_window(app: &AppHandle) {
    open_connect_window_impl(app, false);
}

/// First-run variant: opens the same status window but tells the page to
/// reveal the pairing QR immediately — the wizard promised "Show QR & link".
/// Only the macOS onboarding flow calls it.
#[cfg(target_os = "macos")]
pub fn open_connect_window_qr(app: &AppHandle) {
    open_connect_window_impl(app, true);
}

fn open_connect_window_impl(app: &AppHandle, reveal_qr: bool) {
    let Some(info) = app
        .try_state::<Backend>()
        .and_then(|backend| backend.info())
    else {
        notify(
            app,
            "Termx is starting",
            "Connection details are not ready yet.",
        );
        return;
    };
    let url = if reveal_qr {
        format!("{}&show_connection=1", connect_url(&info))
    } else {
        connect_url(&info)
    };
    if let Some(window) = app.get_webview_window("connect") {
        if let Ok(parsed) = url.parse() {
            let _ = window.navigate(parsed);
        }
        let _ = window.show();
        let _ = window.set_focus();
        return;
    }
    let builder =
        WebviewWindowBuilder::new(app, "connect", WebviewUrl::External(url.parse().unwrap()))
            .title("Termx — Machine Status")
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
    let Some(info) = app
        .try_state::<Backend>()
        .and_then(|backend| backend.info())
    else {
        notify(
            app,
            "Termx is starting",
            "Connection details are not ready yet.",
        );
        return;
    };
    let link = match gql_app(app, "{ connect_info { connect_url } }", None).and_then(|value| {
        value
            .pointer("/connect_info/connect_url")
            .and_then(|item| item.as_str())
            .map(str::to_string)
    }) {
        Some(link) => link,
        None => info.window_url(),
    };
    match app.clipboard().write_text(link.clone()) {
        Ok(()) => notify(app, "Connection link copied", &link),
        Err(error) => logging::desktop(app, &format!("clipboard write failed: {error}")),
    }
}

pub fn open_in_browser(app: &AppHandle) {
    let Some(info) = app
        .try_state::<Backend>()
        .and_then(|backend| backend.info())
    else {
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

/// Called when the backend reports that a permission is missing (e.g. the
/// phone opened the desktop mirror without Screen Recording granted).
pub fn permission_event(app: &AppHandle, which: &str) {
    if !cfg!(target_os = "macos") {
        return;
    }
    match which {
        "screen_recording" => {
            let status = crate::permissions::status();
            if !status.screen_recording {
                notify(
                    app,
                    "Screen Recording needed",
                    "Grant Termx Screen Recording to mirror your Mac to your phone.",
                );
                let handle = app.clone();
                let _ = handle.run_on_main_thread(move || {
                    crate::permissions::request_screen_recording();
                });
                let handle = app.clone();
                let _ = handle
                    .clone()
                    .run_on_main_thread(move || open_permission_settings(&handle));
            }
        }
        "accessibility" => {
            let status = crate::permissions::status();
            if !status.accessibility {
                notify(
                    app,
                    "Accessibility needed",
                    "Grant Termx Accessibility to control your Mac from your phone.",
                );
                let handle = app.clone();
                let _ = handle.run_on_main_thread(move || {
                    crate::permissions::request_accessibility();
                });
                let handle = app.clone();
                let _ = handle
                    .clone()
                    .run_on_main_thread(move || open_accessibility_settings(&handle));
            }
        }
        "open_settings" => open_permission_settings(app),
        _ => {}
    }
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
    // Prompts come from the app process, not the Python sidecar, so macOS
    // attributes the grant to Termx.app and never signals the backend.
    let before = crate::permissions::status();
    let handle = app.clone();
    app.dialog()
        .message(format!(
            "Termx uses two macOS permissions:\n\n• Screen Recording — stream your desktop to your phone\n• Accessibility — send pointer and keyboard input\n\nCurrent status:\nScreen Recording: {}\nAccessibility: {}\n\nRequest permissions now?",
            before.screen_recording_label(),
            before.accessibility_label(),
        ))
        .title("Termx Permissions")
        .buttons(MessageDialogButtons::OkCustom("Request".to_string()))
        .show(move |requested| {
            if requested {
                let prompt = handle.clone();
                let _ = handle.run_on_main_thread(move || {
                    crate::permissions::request_screen_recording();
                    crate::permissions::request_accessibility();
                });
                let _ = prompt;
            }
            if config::set_onboarded(&handle).is_err() {
                notify(&handle, "Termx", "Native workspace configuration is not private");
                return;
            }
            let status = crate::permissions::status();
            if !status.screen_recording {
                open_permission_settings(&handle);
            }
            if !status.accessibility {
                open_accessibility_settings(&handle);
            }
            if requested {
                notify(
                    &handle,
                    "Grant permissions to Termx, then restart",
                    "Screen Recording and Accessibility are listed under Termx in System Settings. Use Termx → Restart Termx afterwards.",
                );
            }
        });
}

pub fn error_dialog(app: &AppHandle, title: &str, message: &str) {
    app.dialog()
        .message(message)
        .title(title)
        .buttons(MessageDialogButtons::Ok)
        .show(|_| {});
}

/// First-run onboarding (macOS): Welcome → Screen Recording → Accessibility →
/// Launch at Login → Machine ready → QR/pairing link. Each step is a native
/// dialog so permission prompts stay attributed to Termx.app in TCC; the
/// final step opens the pairing window.
#[cfg(target_os = "macos")]
fn onboarding_welcome(app: AppHandle) {
    app.dialog()
        .message(
            "Termx turns this machine into a workspace you can reach from your phone — chats, files, terminals, previews, and the desktop itself.\n\nSetup takes about a minute: two macOS permissions, then a QR code to pair.",
        )
        .title("Welcome to Termx")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Get started".to_string(),
            "Skip setup".to_string(),
        ))
        .show(move |proceed| {
            if proceed {
                onboarding_screen_recording(&app);
            } else {
                // Skipping still completes first run — the pairing window opens
                // so the machine is reachable without a nagging wizard.
                if config::set_onboarded(&app).is_err() {
                    notify(&app, "Termx", "Native workspace configuration is not private");
                    return;
                }
                open_connect_window(&app);
            }
        });
}

#[cfg(target_os = "macos")]
fn onboarding_screen_recording(app: &AppHandle) {
    if crate::permissions::status().screen_recording {
        onboarding_accessibility(app);
        return;
    }
    let handle = app.clone();
    app.dialog()
        .message(
            "Screen Recording lets Termx mirror this display to your phone.\n\nChoose Grant to approve Termx in the macOS prompt. If the prompt was already dismissed, enable Termx under System Settings → Privacy & Security → Screen Recording.",
        )
        .title("Termx — Screen Recording")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Grant Screen Recording".to_string(),
            "Not now".to_string(),
        ))
        .show(move |grant| {
            if grant {
                let prompt = handle.clone();
                let _ = prompt.run_on_main_thread(move || {
                    crate::permissions::request_screen_recording();
                });
                open_permission_settings_when_settled(&handle, true);
            }
            onboarding_accessibility(&handle);
        });
}

#[cfg(target_os = "macos")]
fn onboarding_accessibility(app: &AppHandle) {
    if crate::permissions::status().accessibility {
        onboarding_autostart(app);
        return;
    }
    let handle = app.clone();
    app.dialog()
        .message(
            "Accessibility lets Termx send pointer and keyboard input when you control this machine.\n\nChoose Grant to approve Termx in the macOS prompt, or add Termx under System Settings → Privacy & Security → Accessibility.",
        )
        .title("Termx — Accessibility")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Grant Accessibility".to_string(),
            "Not now".to_string(),
        ))
        .show(move |grant| {
            if grant {
                let prompt = handle.clone();
                let _ = prompt.run_on_main_thread(move || {
                    crate::permissions::request_accessibility();
                });
                open_permission_settings_when_settled(&handle, false);
            }
            onboarding_autostart(&handle);
        });
}

#[cfg(target_os = "macos")]
fn onboarding_autostart(app: &AppHandle) {
    let handle = app.clone();
    app.dialog()
        .message("Start Termx automatically when you log in?\n\nYou can change this later under Termx → Launch at Login.")
        .title("Termx — Launch at Login")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Enable".to_string(),
            "Not now".to_string(),
        ))
        .show(move |enable| {
            if enable {
                match handle.autolaunch().enable() {
                    Ok(()) => crate::menu::sync_autostart(&handle),
                    Err(error) => notify(&handle, "Termx", &format!("Could not enable launch at login: {error}")),
                }
            }
            onboarding_ready(&handle);
        });
}

#[cfg(target_os = "macos")]
fn onboarding_ready(app: &AppHandle) {
    if config::set_onboarded(app).is_err() {
        notify(
            app,
            "Termx",
            "Native workspace configuration is not private",
        );
        return;
    }
    let handle = app.clone();
    app.dialog()
        .message(
            "This machine is ready.\n\nScan the QR code or copy the link to connect your phone.",
        )
        .title("Termx — Machine ready")
        .buttons(MessageDialogButtons::OkCustom("Show QR & link".to_string()))
        .show(move |_| {
            open_connect_window_qr(&handle);
        });
}

/// The TCC prompt a grant request triggers is still pending when the dialog
/// callback runs — checking status immediately reads the stale "denied" and
/// yanks the user into System Settings alongside the system prompt. Poll
/// until the request settles; only open Settings if the grant never lands.
#[cfg(target_os = "macos")]
fn open_permission_settings_when_settled(app: &AppHandle, screen_recording: bool) {
    let handle = app.clone();
    std::thread::spawn(move || {
        for _ in 0..20 {
            std::thread::sleep(std::time::Duration::from_millis(1500));
            let status = crate::permissions::status();
            if if screen_recording {
                status.screen_recording
            } else {
                status.accessibility
            } {
                return;
            }
        }
        let handle_for_closure = handle.clone();
        let _ = handle.run_on_main_thread(move || {
            if screen_recording {
                open_permission_settings(&handle_for_closure);
            } else {
                open_accessibility_settings(&handle_for_closure);
            }
        });
    });
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
        let app_handle = handle.clone();
        let _ = handle.run_on_main_thread(move || onboarding_welcome(app_handle));
    });
}

#[cfg(not(target_os = "macos"))]
pub fn first_run_onboarding(_app: &AppHandle, _onboarded: bool) {}
