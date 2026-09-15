use tauri::menu::{AboutMetadataBuilder, CheckMenuItemBuilder, MenuBuilder, MenuItemBuilder, SubmenuBuilder};
use tauri::{AppHandle, Manager};
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_updater::UpdaterExt;

use crate::backend::Backend;
use crate::ui;

pub fn app_menu(app: &AppHandle) -> tauri::Result<tauri::menu::Menu<tauri::Wry>> {
    let connect = MenuItemBuilder::with_id("connect", "Connection Info…")
        .accelerator("CmdOrCtrl+Shift+C")
        .build(app)?;
    let copy_link = MenuItemBuilder::with_id("copy_link", "Copy Phone Link").build(app)?;
    let permissions = MenuItemBuilder::with_id("permissions", "Permissions…").build(app)?;
    let restart = MenuItemBuilder::with_id("restart", "Restart Backend").build(app)?;
    let restart_app = MenuItemBuilder::with_id("restart_app", "Restart Termx").build(app)?;
    let check_updates = MenuItemBuilder::with_id("check_updates", "Check for Updates…").build(app)?;
    let autostart = CheckMenuItemBuilder::with_id("autostart", "Launch at Login")
        .checked(false)
        .build(app)?;
    let open_logs = MenuItemBuilder::with_id("open_logs", "Open Logs Folder").build(app)?;
    let quit = MenuItemBuilder::with_id("quit", "Quit Termx")
        .accelerator("CmdOrCtrl+Q")
        .build(app)?;

    let app_menu = SubmenuBuilder::new(app, "Termx")
        .about(Some(
            AboutMetadataBuilder::new()
                .name(Some("Termx"))
                .version(Some(env!("CARGO_PKG_VERSION")))
                .build(),
        ))
        .separator()
        .item(&connect)
        .item(&copy_link)
        .separator()
        .item(&permissions)
        .item(&restart)
        .item(&restart_app)
        .item(&check_updates)
        .item(&autostart)
        .item(&open_logs)
        .separator()
        .item(&quit)
        .build()?;

    let edit = SubmenuBuilder::new(app, "Edit")
        .undo()
        .redo()
        .separator()
        .cut()
        .copy()
        .paste()
        .select_all()
        .build()?;

    let reload = MenuItemBuilder::with_id("reload", "Reload UI")
        .accelerator("CmdOrCtrl+R")
        .build(app)?;
    let browser = MenuItemBuilder::with_id("browser", "Open in Browser").build(app)?;
    let view = SubmenuBuilder::new(app, "View")
        .item(&reload)
        .item(&browser)
        .separator()
        .fullscreen()
        .build()?;

    let help_logs = MenuItemBuilder::with_id("open_logs", "Open Logs Folder").build(app)?;
    let help_browser = MenuItemBuilder::with_id("browser", "Open in Browser").build(app)?;
    let help = SubmenuBuilder::new(app, "Help")
        .item(&help_browser)
        .item(&help_logs)
        .build()?;

    MenuBuilder::new(app)
        .items(&[&app_menu, &edit, &view, &help])
        .build()
}

pub fn sync_autostart(app: &AppHandle) {
    // `is_enabled()` for the LaunchAgent launcher talks to a system daemon over
    // XPC. That call blocks until the daemon answers, and running it on the
    // main thread during startup leaves the app stuck with no window if the
    // daemon is slow or wedged — so query it off the main thread and only touch
    // the menu once the answer arrives.
    let handle = app.clone();
    std::thread::spawn(move || {
        let enabled = handle.autolaunch().is_enabled().unwrap_or(false);
        let handle_for_menu = handle.clone();
        let _ = handle.run_on_main_thread(move || {
            if let Some(menu) = handle_for_menu.menu() {
                if let Some(check) = menu.get("autostart").and_then(|item| item.as_check_menuitem().cloned()) {
                    let _ = check.set_checked(enabled);
                }
            }
        });
    });
}

pub fn handle_menu_event(app: &AppHandle, id: &str) {
    match id {
        "connect" => ui::open_connect_window(app),
        "copy_link" => ui::copy_connect_link(app),
        "permissions" => ui::permission_dialog(app),
        "restart" => {
            if let Some(backend) = app.try_state::<Backend>() {
                backend.restart();
            }
        }
        "restart_app" => {
            if let Some(backend) = app.try_state::<Backend>() {
                backend.stop();
            }
            app.restart();
        }
        "check_updates" => check_updates(app),
        "autostart" => toggle_autostart(app),
        "open_logs" => ui::open_logs(app),
        "reload" => {
            if let Some(window) = ui::main_window(app) {
                let _ = window.eval("location.reload()");
            }
        }
        "browser" => ui::open_in_browser(app),
        "quit" => crate::quit(app),
        _ => {}
    }
}

fn toggle_autostart(app: &AppHandle) {
    let autolaunch = app.autolaunch();
    let enabled = autolaunch.is_enabled().unwrap_or(false);
    let result = if enabled {
        autolaunch.disable()
    } else {
        autolaunch.enable()
    };
    match result {
        Ok(()) => {
            sync_autostart(app);
            ui::notify(
                app,
                "Termx",
                if enabled {
                    "Launch at login disabled"
                } else {
                    "Launch at login enabled"
                },
            );
        }
        Err(error) => ui::notify(app, "Termx", &format!("Could not update launch at login: {error}")),
    }
}

fn check_updates(app: &AppHandle) {
    run_update(app, "install");
}

/// Run an update step: `check` only reports, `install` downloads and restarts.
pub fn run_update(app: &AppHandle, action: &str) {
    let handle = app.clone();
    let install = action == "install";
    tauri::async_runtime::spawn(async move {
        let updater = match handle.updater() {
            Ok(updater) => updater,
            Err(_) => {
                ui::notify(
                    &handle,
                    "Updates unavailable",
                    "This build has no update endpoint configured.",
                );
                return;
            }
        };
        match updater.check().await {
            Ok(Some(update)) => {
                let version = update.version.clone();
                if !install {
                    ui::notify(
                        &handle,
                        "Update available",
                        &format!("Termx {version} is ready — open the menu to install it."),
                    );
                    return;
                }
                ui::notify(&handle, "Updating Termx", &format!("Downloading Termx {version}…"));
                match update.download_and_install(|_, _| {}, || {}).await {
                    Ok(()) => {
                        ui::notify(&handle, "Termx updated", &format!("Restarting on {version}"));
                        handle.restart();
                    }
                    Err(error) => ui::notify(&handle, "Update failed", &error.to_string()),
                }
            }
            Ok(None) => ui::notify(&handle, "Termx is up to date", ""),
            Err(error) => ui::notify(&handle, "Updates unavailable", &error.to_string()),
        }
    });
}
