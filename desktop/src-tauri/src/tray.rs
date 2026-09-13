use serde_json::json;
use tauri::menu::{CheckMenuItemBuilder, MenuBuilder, MenuItemBuilder};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::AppHandle;
use tauri_plugin_autostart::ManagerExt;

use crate::backend::{api_get, api_post};
use crate::menu;
use crate::ui;

pub fn init(app: &AppHandle) -> tauri::Result<()> {
    let show = MenuItemBuilder::with_id("show", "Show Termx").build(app)?;
    let connect = MenuItemBuilder::with_id("connect", "Connection Info…").build(app)?;
    let copy_link = MenuItemBuilder::with_id("copy_link", "Copy Phone Link").build(app)?;
    let tunnel = MenuItemBuilder::with_id("tunnel", "Start / Stop Tunnel").build(app)?;
    let restart = MenuItemBuilder::with_id("restart", "Restart Backend").build(app)?;
    let autostart = CheckMenuItemBuilder::with_id("autostart", "Launch at Login")
        .checked(app.autolaunch().is_enabled().unwrap_or(false))
        .build(app)?;
    let permissions = MenuItemBuilder::with_id("permissions", "Permissions…").build(app)?;
    let quit = MenuItemBuilder::with_id("quit", "Quit Termx").build(app)?;

    let menu = MenuBuilder::new(app)
        .item(&show)
        .separator()
        .item(&connect)
        .item(&copy_link)
        .item(&tunnel)
        .separator()
        .item(&restart)
        .item(&permissions)
        .item(&autostart)
        .separator()
        .item(&quit)
        .build()?;

    let mut builder = TrayIconBuilder::with_id("termx")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .tooltip("Termx")
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                ui::toggle_main_window(tray.app_handle());
            }
        })
        .on_menu_event(|app, event| {
            let id = event.id().as_ref();
            if id == "show" {
                ui::show_main_window(app);
            } else if id == "tunnel" {
                toggle_tunnel(app);
            } else {
                menu::handle_menu_event(app, id);
            }
        });
    if let Some(icon) = app.default_window_icon().cloned() {
        builder = builder.icon(icon).icon_as_template(true);
    }
    builder.build(app)?;
    Ok(())
}

fn toggle_tunnel(app: &AppHandle) {
    let status = api_get(app, "/api/tunnels");
    let connected = status
        .as_ref()
        .and_then(|value| value.get("active"))
        .and_then(|value| value.get("state"))
        .and_then(|value| value.as_str())
        .map(|state| state == "connected")
        .unwrap_or(false);
    let result = if connected {
        api_post(app, "/api/tunnels/stop", json!({}))
    } else {
        api_post(app, "/api/tunnels/start", json!({}))
    };
    if result.is_none() {
        ui::notify(
            app,
            "Tunnel unavailable",
            "Check that cloudflared, ngrok, or tailscale is installed.",
        );
    }
}
