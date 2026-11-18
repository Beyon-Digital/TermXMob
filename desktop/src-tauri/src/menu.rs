use tauri::menu::{
    AboutMetadataBuilder, CheckMenuItemBuilder, MenuBuilder, MenuItemBuilder, SubmenuBuilder,
    HELP_SUBMENU_ID, WINDOW_SUBMENU_ID,
};
use tauri::{AppHandle, Manager};
use tauri_plugin_autostart::ManagerExt;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};

use crate::backend::Backend;
use crate::menu_commands::{self, command_item};
use crate::ui;

pub fn app_menu(app: &AppHandle) -> tauri::Result<tauri::menu::Menu<tauri::Wry>> {
    let connect = MenuItemBuilder::with_id("connect", "Machine Status & Pairing…")
        .accelerator("CmdOrCtrl+Shift+C")
        .build(app)?;
    let copy_link = MenuItemBuilder::with_id("copy_link", "Copy Connection Link").build(app)?;
    let permissions = MenuItemBuilder::with_id("permissions", "Permissions…").build(app)?;
    let restart = MenuItemBuilder::with_id("restart", "Restart Backend").build(app)?;
    let restart_app = MenuItemBuilder::with_id("restart_app", "Restart TermX").build(app)?;
    let check_updates =
        MenuItemBuilder::with_id("check_updates", "Check for Updates…").build(app)?;
    let autostart = CheckMenuItemBuilder::with_id("autostart", "Launch at Login")
        .checked(false)
        .build(app)?;
    let open_logs = MenuItemBuilder::with_id("open_logs", "Open Logs Folder").build(app)?;
    let quit = MenuItemBuilder::with_id("quit", "Quit TermX")
        .accelerator("CmdOrCtrl+Q")
        .build(app)?;

    // Low-level service operations live under Advanced — the top level stays
    // machine status, pairing, permissions, and launch-at-login.
    let background =
        CheckMenuItemBuilder::with_id("background_host", "Keep Host Running After Quit")
            .checked(
                crate::config::load(app)
                    .map(|config| config.backend_mode == crate::config::BackendMode::Service)
                    .unwrap_or(false),
            )
            .build(app)?;
    let stop_background =
        MenuItemBuilder::with_id("stop_background_host", "Stop Background Host").build(app)?;
    let advanced = SubmenuBuilder::new(app, "Advanced")
        .item(&background)
        .item(&stop_background)
        .separator()
        .item(&restart)
        .item(&restart_app)
        .item(&check_updates)
        .separator()
        .item(&open_logs)
        .build()?;

    // Items made with `command_item` carry a command id of the workspace registry and run nothing here: they emit an event
    // and the workspace shell executes the command (see menu_commands.rs). Their order is pinned to native-menu.json.
    let settings = command_item(app, "settings", "Settings…")?;
    let lock = command_item(app, "lock", "Lock")?;
    let app_menu = SubmenuBuilder::new(app, "TermX")
        .about(Some(
            AboutMetadataBuilder::new()
                .name(Some("TermX"))
                .version(Some(env!("CARGO_PKG_VERSION")))
                .build(),
        ))
        .separator()
        .item(&settings)
        .item(&lock)
        .separator()
        .item(&connect)
        .item(&copy_link)
        .separator()
        .item(&permissions)
        .item(&autostart)
        .separator()
        .item(&advanced)
        .separator()
        .item(&quit)
        .build()?;

    let new_chat = command_item(app, "newConversation", "New Chat")?;
    let open_project = command_item(app, "open-project", "Open Project…")?;
    let save = command_item(app, "saveFile", "Save")?;
    let close_tab = command_item(app, "closeTab", "Close Tab")?;
    let file = SubmenuBuilder::new(app, "File")
        .item(&new_chat)
        .item(&open_project)
        .separator()
        .item(&save)
        .separator()
        .item(&close_tab)
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

    let palette = command_item(app, "palette", "Command Palette…")?;
    let side_bar = command_item(app, "left", "Side Bar")?;
    let panel = command_item(app, "bottom", "Panel")?;
    let agents = command_item(app, "view-agents", "Agents")?;
    let explorer = command_item(app, "explorer", "Explorer")?;
    let search = command_item(app, "search", "Search")?;
    let source_control = command_item(app, "sourceControl", "Source Control")?;
    let full_screen = command_item(app, "fullscreen", "Full Screen")?;
    let reload = MenuItemBuilder::with_id("reload", "Reload UI")
        .accelerator("CmdOrCtrl+R")
        .build(app)?;
    let browser = MenuItemBuilder::with_id("browser", "Open in Browser").build(app)?;
    let view = SubmenuBuilder::new(app, "View")
        .item(&palette)
        .separator()
        .item(&side_bar)
        .item(&panel)
        .separator()
        .item(&agents)
        .item(&explorer)
        .item(&search)
        .item(&source_control)
        .separator()
        .item(&full_screen)
        .separator()
        .item(&reload)
        .item(&browser)
        .build()?;

    let go_to_file = command_item(app, "quickFile", "Go to File…")?;
    let next_tab = command_item(app, "nextTab", "Next Tab")?;
    let previous_tab = command_item(app, "previousTab", "Previous Tab")?;
    let first_group = command_item(app, "focusGroup1", "First Group")?;
    let second_group = command_item(app, "focusGroup2", "Second Group")?;
    let third_group = command_item(app, "focusGroup3", "Third Group")?;
    let focus_group = SubmenuBuilder::new(app, "Focus Group")
        .item(&first_group)
        .item(&second_group)
        .item(&third_group)
        .build()?;
    let go = SubmenuBuilder::new(app, "Go")
        .item(&go_to_file)
        .separator()
        .item(&next_tab)
        .item(&previous_tab)
        .separator()
        .item(&focus_group)
        .build()?;

    let new_terminal = command_item(app, "newTerminal", "New Terminal")?;
    let toggle_terminal = command_item(app, "focusTerminal", "Toggle Terminal")?;
    let terminal = SubmenuBuilder::new(app, "Terminal")
        .item(&new_terminal)
        .item(&toggle_terminal)
        .build()?;

    let detach = command_item(app, "detach-workspace", "Detach Workspace")?;
    let return_to_main = command_item(app, "return-to-main", "Return to Main Workspace")?;
    let window = SubmenuBuilder::with_id(app, WINDOW_SUBMENU_ID, "Window")
        .minimize()
        .maximize()
        .separator()
        .item(&detach)
        .item(&return_to_main)
        .build()?;

    let help_logs = MenuItemBuilder::with_id("open_logs", "Open Logs Folder").build(app)?;
    let help_browser = MenuItemBuilder::with_id("browser", "Open in Browser").build(app)?;
    let help = SubmenuBuilder::with_id(app, HELP_SUBMENU_ID, "Help")
        .item(&help_browser)
        .item(&help_logs)
        .build()?;

    MenuBuilder::new(app)
        .items(&[&app_menu, &file, &edit, &view, &go, &terminal, &window, &help])
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
                if let Some(check) = menu
                    .get("autostart")
                    .and_then(|item| item.as_check_menuitem().cloned())
                {
                    let _ = check.set_checked(enabled);
                }
            }
            if let Some(check) = handle_for_menu.try_state::<crate::tray::AutostartItem>() {
                let _ = check.0.set_checked(enabled);
            }
        });
    });
}

pub fn sync_background_mode(app: &AppHandle, enabled: bool) {
    let handle = app.clone();
    let _ = app.run_on_main_thread(move || {
        if let Some(menu) = handle.menu() {
            if let Some(check) = menu
                .get("background_host")
                .and_then(|item| item.as_check_menuitem().cloned())
            {
                let _ = check.set_checked(enabled);
            }
        }
        for window in handle.webview_windows().values() {
            if let Some(menu) = window.menu() {
                if let Some(check) = menu
                    .get("background_host")
                    .and_then(|item| item.as_check_menuitem().cloned())
                {
                    let _ = check.set_checked(enabled);
                }
            }
        }
        if let Some(item) = handle.try_state::<crate::tray::BackgroundItem>() {
            let _ = item.0.set_checked(enabled);
        }
    });
}

fn toggle_background_mode(app: &AppHandle) {
    let Some(backend) = app.try_state::<Backend>() else {
        return;
    };
    let enable = !backend.background_mode();
    sync_background_mode(app, !enable);
    let message = if enable {
        "Keep agents and schedules running after you quit TermX?\n\nThis explicitly enables a user-owned background host for this profile. It continues until you stop it remotely or choose Stop Background Host. Independent service mode does not inherit the desktop shell’s macOS permission bridge. Verify computer-control permissions on the host before relying on unattended desktop control."
    } else {
        "Stop the independent background host and return to an app-managed host?\n\nActive agent work will stop. Afterwards, quitting TermX also stops its host."
    };
    let handle = app.clone();
    app.dialog()
        .message(message)
        .title("TermX — Host Lifetime")
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Switch host mode".into(),
            "Cancel".into(),
        ))
        .show(move |accepted| {
            if accepted {
                if let Some(backend) = handle.try_state::<Backend>() {
                    backend.set_background_mode(enable);
                }
            }
        });
}

pub fn handle_menu_event(app: &AppHandle, id: &str) {
    // A workspace command: the window runs it, never this process.
    if let Some(command) = menu_commands::command_id(id) {
        menu_commands::emit(app, command);
        return;
    }
    match id {
        "background_host" => toggle_background_mode(app),
        "stop_background_host" => {
            if let Some(backend) = app.try_state::<Backend>() {
                backend.stop_background_host();
            }
        }
        "connect" => ui::open_connect_window(app),
        "copy_link" => {
            let handle = app.clone();
            std::thread::spawn(move || ui::copy_connect_link(&handle));
        }
        "permissions" => ui::permission_dialog(app),
        "restart" => {
            if let Some(backend) = app.try_state::<Backend>() {
                backend.restart();
            }
        }
        "restart_app" => {
            let handle = app.clone();
            std::thread::spawn(move || {
                if let Some(backend) = handle.try_state::<Backend>() {
                    backend.stop();
                }
                handle.restart();
            });
        }
        "check_updates" => check_updates(app),
        "autostart" => {
            let handle = app.clone();
            std::thread::spawn(move || toggle_autostart(&handle));
        }
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
                "TermX",
                if enabled {
                    "Launch at login disabled"
                } else {
                    "Launch at login enabled"
                },
            );
        }
        Err(error) => ui::notify(
            app,
            "TermX",
            &format!("Could not update launch at login: {error}"),
        ),
    }
}

fn check_updates(app: &AppHandle) {
    crate::updater::show(app);
}

/// Backend update requests open the same confirmation/progress window.
/// Installation and restart require a user action in that local window.
pub fn run_update(app: &AppHandle, _action: &str) {
    crate::updater::show(app);
}
