mod backend;
mod broker;
mod capture;
mod config;
mod input_macos;
mod logging;
mod menu;
mod permissions;
mod tray;
mod ui;

use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{Manager, RunEvent, WindowEvent};

pub static QUITTING: AtomicBool = AtomicBool::new(false);

pub fn quit(app: &tauri::AppHandle) {
    if QUITTING.swap(true, Ordering::SeqCst) {
        return;
    }
    let handle = app.clone();
    std::thread::spawn(move || {
        if let Some(backend) = handle.try_state::<backend::Backend>() {
            backend.stop();
        }
        handle.exit(0);
    });
}

/// True when another Termx shell process is already running.
///
/// This replaces tauri-plugin-single-instance: that plugin blocks the main
/// thread in synchronous XPC when a previous instance left a stale
/// registration behind (observed: the app never finished launching), so the
/// guard is a plain process scan instead.
#[cfg(unix)]
fn other_instance_running() -> bool {
    let output = std::process::Command::new("/bin/ps")
        .args(["-A", "-o", "pid=,command="])
        .output();
    let Ok(output) = output else {
        return false;
    };
    let me = std::process::id().to_string();
    String::from_utf8_lossy(&output.stdout).lines().any(|line| {
        let line = line.trim();
        if !line.contains("Termx.app/Contents/MacOS/termx-desktop") {
            return false;
        }
        !line.starts_with(&format!("{me} "))
    })
}

#[cfg(not(unix))]
fn other_instance_running() -> bool {
    false
}

fn main() {
    let autostart = std::env::args().any(|arg| arg == "--autostart");
    ui::START_HIDDEN.store(autostart, Ordering::SeqCst);
    if other_instance_running() {
        eprintln!("termx: another Termx instance is already running");
        return;
    }

    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec!["--autostart"]),
        ))
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .on_menu_event(|app, event| menu::handle_menu_event(app, event.id().as_ref()));
    #[cfg(target_os = "macos")]
    let builder = builder.menu(menu::app_menu);

    builder
        .setup(|app| {
            let handle = app.handle().clone();
            logging::desktop(&handle, "setup: start");
            config::prepare_dirs(&handle);
            let desktop_config = config::load(&handle);
            logging::desktop(&handle, "setup: config loaded");
            ui::create_main_window(&handle)?;
            logging::desktop(&handle, "setup: window created");
            tray::init(&handle)?;
            logging::desktop(&handle, "setup: tray ready");
            menu::sync_autostart(&handle);
            logging::desktop(&handle, "setup: autostart syncing");
            let log_handle = handle.clone();
            if let Some(path) = broker::start(handle.clone(), move |line| logging::desktop(&log_handle, line)) {
                logging::desktop(&handle, &format!("privileged broker ready at {path}"));
            }
            let backend = backend::Backend::new(handle.clone());
            app.manage(backend.clone());
            backend.start();
            ui::first_run_onboarding(&handle, desktop_config.onboarded);
            logging::desktop(&handle, "termx desktop started");
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" && !QUITTING.load(Ordering::SeqCst) {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build Termx")
        .run(|app, event| match event {
            RunEvent::ExitRequested { api, code, .. } => {
                if code.is_none() && !QUITTING.load(Ordering::SeqCst) {
                    api.prevent_exit();
                    quit(app);
                }
            }
            RunEvent::Exit => {
                QUITTING.store(true, Ordering::SeqCst);
                if let Some(backend) = app.try_state::<backend::Backend>() {
                    backend.stop();
                }
            }
            _ => {}
        });
}
