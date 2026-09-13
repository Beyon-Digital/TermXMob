mod backend;
mod config;
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

fn main() {
    let autostart = std::env::args().any(|arg| arg == "--autostart");
    ui::START_HIDDEN.store(autostart, Ordering::SeqCst);

    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            ui::show_main_window(app);
        }))
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
            config::prepare_dirs(&handle);
            let desktop_config = config::load(&handle);
            ui::create_main_window(&handle)?;
            tray::init(&handle)?;
            menu::sync_autostart(&handle);
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
