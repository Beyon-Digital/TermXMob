//! Trusted native workspace bridge. Refresh credentials never cross into JS.
use keyring::Entry;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tauri::{
    AppHandle, Emitter, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder,
};

const SERVICE: &str = "com.jaexxxy.termx.workspace.session";
#[path = "window_geometry.rs"]
mod window_geometry;
#[derive(Default)]
struct MemorySession {
    access: String,
    expires: u64,
    host_id: String,
}
#[derive(Default)]
pub struct NativeSession(Mutex<MemorySession>);
#[derive(Default)]
pub struct RedockCoordinator(Mutex<BTreeMap<String, RedockPending>>);
struct RedockPending {
    source: String,
    owner: String,
    session_id: Option<String>,
    created: Instant,
    accepted: bool,
}
#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct RedockEvent {
    id: String,
    source: String,
    owner_id: String,
    session_id: Option<String>,
    panel: String,
    payload: Value,
}
#[derive(Serialize)]
pub struct NativeResponse {
    status: u16,
    body: Value,
}
fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
fn workspace_label(label: &str) -> bool {
    label == "main" || label == "connect" || label.starts_with("workspace-")
}
pub fn legacy_allowed(port: u16) -> bool {
    // A native denial must never retry with the administrator bootstrap.
    call(port, "/auth/methods", "GET", None, None, None)
        .map(|response| response.status == 200 && response.body["configured"] == false)
        .unwrap_or(false)
}
fn trusted(app: &AppHandle, window: &WebviewWindow) -> Result<u16, String> {
    let backend = app
        .try_state::<crate::backend::Backend>()
        .ok_or("Host unavailable")?;
    let port = backend.port();
    let url = window.url().map_err(|_| "Window unavailable")?;
    if !workspace_label(window.label())
        || url.scheme() != "http"
        || url.host_str() != Some("127.0.0.1")
        || url.port_or_known_default() != Some(port)
        || url.path() != "/"
    {
        return Err("Native actions require the trusted local workspace".into());
    }
    Ok(port)
}
fn safe_host_path(path: &str) -> bool {
    // API route segments are ASCII identifiers. Encoded dots/slashes can be
    // normalized by the URL parser into an otherwise forbidden auth route.
    path.starts_with('/')
        && !path.starts_with("//")
        && !path.contains('\\')
        && !path.contains("..")
        && !path.contains('#')
        && !path.split('?').next().unwrap_or("").contains('%')
        && !path.chars().any(char::is_control)
}
fn call(
    port: u16,
    path: &str,
    method: &str,
    data: Option<Value>,
    access: Option<&str>,
    bootstrap: Option<&str>,
) -> Result<NativeResponse, String> {
    if !safe_host_path(path) {
        return Err("Invalid host path".into());
    }
    if !["GET", "POST", "PUT", "PATCH", "DELETE"].contains(&method) {
        return Err("Invalid method".into());
    }
    let url = format!("http://127.0.0.1:{port}{path}");
    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_secs(30))
        .redirects(0)
        .build();
    let mut req = agent.request(method, &url);
    if let Some(token) = access {
        req = req.set("Authorization", &format!("Bearer {token}"));
    }
    if let Some(passcode) = bootstrap {
        req = req
            .set("X-Termx-Passcode", passcode)
            .set("Origin", &format!("http://127.0.0.1:{port}"));
    }
    let result = match data {
        Some(value) => req.send_json(value),
        None => req.call(),
    };
    let response = match result {
        Ok(value) | Err(ureq::Error::Status(_, value)) => value,
        Err(_) => return Err("Host connection unavailable".into()),
    };
    let status = response.status();
    let body = response
        .into_json::<Value>()
        .map_err(|_| "Host response is not JSON")?;
    Ok(NativeResponse { status, body })
}
fn entry(host: &str) -> Result<Entry, String> {
    if host.len() != 32 || !host.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err("Invalid host identity".into());
    }
    Entry::new(SERVICE, host).map_err(|_| "OS secure credential storage unavailable".into())
}
fn save(session: &mut MemorySession, response: &Value) -> Result<(), String> {
    let access = response["access_token"]
        .as_str()
        .ok_or("Missing access credential")?;
    let refresh = response["refresh_token"]
        .as_str()
        .ok_or("Missing refresh credential")?;
    entry(&session.host_id)?
        .set_password(refresh)
        .map_err(|_| "Could not persist session in OS secure storage")?;
    session.access = access.to_string();
    session.expires = now() + response["expires_in"].as_u64().unwrap_or(300);
    Ok(())
}
fn host_identity(port: u16) -> Result<String, String> {
    let response = call(port, "/auth/methods", "GET", None, None, None)?;
    response.body["host_id"]
        .as_str()
        .map(str::to_string)
        .ok_or_else(|| "Host identity unavailable".into())
}
fn refresh(app: &AppHandle, port: u16, session: &mut MemorySession) -> Result<(), String> {
    if session.host_id.is_empty() {
        session.host_id = host_identity(port)?;
    }
    let secret = entry(&session.host_id)?
        .get_password()
        .map_err(|_| "Sign in required")?;
    let result = call(
        port,
        "/auth/refresh",
        "POST",
        Some(json!({"refresh_token": secret})),
        None,
        None,
    )?;
    if result.status != 200 {
        let _ = entry(&session.host_id)?.delete_credential();
        session.access.clear();
        session.expires = 0;
        return Err("Session expired or revoked; sign in again".into());
    }
    if let Err(error) = save(session, &result.body) {
        // A rotated refresh token that failed secure persistence is never
        // allowed to continue as a silently nonpersistent native session.
        if let Some(token) = result.body["access_token"].as_str() {
            let _ = call(
                port,
                "/auth/logout",
                "POST",
                Some(json!({})),
                Some(token),
                None,
            );
        }
        session.access.clear();
        return Err(error);
    }
    install_access_cookie(app, &result.body)?;
    Ok(())
}
fn install_access_cookie(app: &AppHandle, credentials: &Value) -> Result<(), String> {
    use tauri::webview::cookie::{Cookie, SameSite};
    let access = credentials["access_token"]
        .as_str()
        .ok_or("Missing access credential")?;
    let csrf = credentials["csrf_token"]
        .as_str()
        .ok_or("Missing CSRF token")?;
    for (label, window) in app.webview_windows() {
        if workspace_label(&label) {
            window
                .set_cookie(
                    Cookie::build(("termx_access", access.to_string()))
                        .domain("127.0.0.1")
                        .path("/")
                        .http_only(true)
                        .same_site(SameSite::Strict)
                        .build(),
                )
                .map_err(|_| "Could not authenticate native control transports")?;
            window
                .set_cookie(
                    Cookie::build(("termx_csrf", csrf.to_string()))
                        .domain("127.0.0.1")
                        .path("/")
                        .same_site(SameSite::Strict)
                        .build(),
                )
                .map_err(|_| "Could not protect native control transports")?;
        }
    }
    Ok(())
}
fn clear_access_cookies(app: &AppHandle) {
    use tauri::webview::cookie::Cookie;
    for (label, window) in app.webview_windows() {
        if workspace_label(&label) {
            for name in ["termx_access", "termx_csrf"] {
                let _ = window.delete_cookie(
                    Cookie::build((name, ""))
                        .domain("127.0.0.1")
                        .path("/")
                        .build(),
                );
            }
        }
    }
}
pub fn gql_native(
    app: &AppHandle,
    port: u16,
    query: &str,
    variables: Option<Value>,
) -> Option<Value> {
    let state = app.try_state::<NativeSession>()?;
    let mut session = state.0.lock().ok()?;
    if session.expires <= now() + 20 {
        refresh(app, port, &mut session).ok()?;
    }
    let response = call(
        port,
        "/graphql",
        "POST",
        Some(json!({"query":query,"variables":variables})),
        Some(&session.access),
        None,
    )
    .ok()?;
    if response.status != 200 || response.body.get("errors").is_some() {
        return None;
    }
    response.body.get("data").cloned()
}
#[tauri::command]
pub async fn workspace_login(
    app: AppHandle,
    window: WebviewWindow,
    username: String,
    password: String,
    setup: bool,
) -> Result<Value, String> {
    let port = trusted(&app, &window)?;
    tauri::async_runtime::spawn_blocking(move || {
        if username.len() > 128 || password.len() > 1024 { return Err("Invalid sign-in input".into()); }
        let state = app.state::<NativeSession>();
        let mut session = state.0.lock().map_err(|_| "Session coordinator unavailable")?;
        session.host_id = host_identity(port)?;
        let bootstrap = app.state::<crate::backend::Backend>().passcode();
        let result = call(port, if setup { "/auth/setup" } else { "/auth/login" }, "POST", Some(json!({"username":username,"password":password,"transport":"bearer","device_name":"TermX desktop"})), None, if setup { Some(&bootstrap) } else { None })?;
        if result.status != 200 { return Err(result.body["detail"].as_str().unwrap_or("Sign-in failed").into()); }
        if let Err(error) = save(&mut session, &result.body) {
            if let Some(token) = result.body["access_token"].as_str() { let _ = call(port,"/auth/logout","POST",Some(json!({})),Some(token),None); }
            return Err(error);
        }
        install_access_cookie(&app, &result.body)?;
        Ok(call(port,"/auth/me","GET",None,Some(&session.access),None)?.body)
    }).await.map_err(|_| "Sign-in worker unavailable")?
}
#[tauri::command]
pub async fn workspace_resume_sso(app: AppHandle, window: WebviewWindow) -> Result<bool, String> {
    let port = trusted(&app, &window)?;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<NativeSession>();
        let mut session = state
            .0
            .lock()
            .map_err(|_| "Session coordinator unavailable")?;
        let url = tauri::Url::parse(&format!("http://127.0.0.1:{port}/auth/refresh"))
            .map_err(|_| "Invalid host URL")?;
        let secret = window
            .cookies_for_url(url)
            .map_err(|_| "Could not read native SSO callback credential")?
            .into_iter()
            .find(|cookie| cookie.name() == "termx_refresh")
            .map(|cookie| cookie.value().to_string());
        let Some(secret) = secret else {
            return Ok(false);
        };
        session.host_id = host_identity(port)?;
        let result = call(
            port,
            "/auth/refresh",
            "POST",
            Some(json!({"refresh_token":secret})),
            None,
            None,
        )?;
        // Callback refresh credentials never remain in the webview after adoption.
        for (label, view) in app.webview_windows() {
            if workspace_label(&label) {
                let _ = view.delete_cookie(
                    tauri::webview::cookie::Cookie::build(("termx_refresh", ""))
                        .domain("127.0.0.1")
                        .path("/auth")
                        .build(),
                );
            }
        }
        if result.status != 200 {
            session.access.clear();
            session.expires = 0;
            return Err("SSO session expired or revoked; sign in again".into());
        }
        if let Err(error) = save(&mut session, &result.body) {
            if let Some(token) = result.body["access_token"].as_str() {
                let _ = call(
                    port,
                    "/auth/logout",
                    "POST",
                    Some(json!({})),
                    Some(token),
                    None,
                );
            }
            session.access.clear();
            session.expires = 0;
            return Err(error);
        }
        install_access_cookie(&app, &result.body)?;
        Ok(true)
    })
    .await
    .map_err(|_| "SSO adoption worker unavailable")?
}
#[tauri::command]
pub async fn workspace_request(
    app: AppHandle,
    window: WebviewWindow,
    path: String,
    method: String,
    data: Option<Value>,
) -> Result<NativeResponse, String> {
    let port = trusted(&app, &window)?;
    if !(path.starts_with("/api/")
        || path == "/graphql"
        || path.starts_with("/auth/admin/")
        || [
            "/auth/me",
            "/auth/methods",
            "/auth/access",
            "/auth/sessions",
        ]
        .contains(&path.as_str())
        || path.starts_with("/auth/sessions/"))
    {
        return Err("Native request path is not permitted".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<NativeSession>();
        // All windows share this critical section, including HTTP + secure
        // persistence: an old refresh value is never used concurrently.
        let mut session = state
            .0
            .lock()
            .map_err(|_| "Session coordinator unavailable")?;
        if path == "/auth/methods" {
            return call(port, &path, &method, data, None, None);
        }
        if session.expires <= now() + 20 {
            refresh(&app, port, &mut session)?;
        }
        let mut response = call(
            port,
            &path,
            &method,
            data.clone(),
            Some(&session.access),
            None,
        )?;
        if response.status == 401 {
            refresh(&app, port, &mut session)?;
            response = call(port, &path, &method, data, Some(&session.access), None)?;
        }
        Ok(response)
    })
    .await
    .map_err(|_| "Native request worker unavailable")?
}
#[tauri::command]
pub async fn workspace_logout(app: AppHandle, window: WebviewWindow) -> Result<(), String> {
    let port = trusted(&app, &window)?;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<NativeSession>();
        let mut session = state
            .0
            .lock()
            .map_err(|_| "Session coordinator unavailable")?;
        if session.expires <= now() {
            let _ = refresh(&app, port, &mut session);
        }
        let revoked = if !session.access.is_empty() {
            call(
                port,
                "/auth/logout",
                "POST",
                Some(json!({})),
                Some(&session.access),
                None,
            )
            .map(|r| r.status == 200 || r.status == 401)
            .unwrap_or(false)
        } else {
            true
        };
        let removed = if !session.host_id.is_empty() {
            entry(&session.host_id)?.delete_credential()
        } else {
            Ok(())
        };
        session.access.clear();
        session.expires = 0;
        clear_access_cookies(&app);
        match removed {
            Ok(()) | Err(keyring::Error::NoEntry) => {}
            Err(_) => return Err("Could not remove OS session credential".into()),
        }
        if revoked {
            Ok(())
        } else {
            Err("Signed out locally; host revocation could not be confirmed".into())
        }
    })
    .await
    .map_err(|_| "Sign-out worker unavailable")?
}
#[derive(Serialize)]
pub struct NativeBinaryResponse {
    status: u16,
    body_base64: String,
    content_type: String,
}
fn binary_call(
    port: u16,
    path: &str,
    method: &str,
    data: Option<&[u8]>,
    mime: &str,
    access: &str,
) -> Result<NativeBinaryResponse, String> {
    use base64::{engine::general_purpose::STANDARD, Engine as _};
    use std::io::Read;
    if !path.starts_with("/api/")
        || !safe_host_path(path)
        || mime.contains(['\r', '\n'])
        || !["GET", "POST", "PUT", "PATCH", "DELETE"].contains(&method)
    {
        return Err("Invalid binary host request".into());
    }
    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_secs(120))
        .redirects(0)
        .build();
    let req = agent
        .request(method, &format!("http://127.0.0.1:{port}{path}"))
        .set("Authorization", &format!("Bearer {access}"))
        .set("Content-Type", mime);
    let result = match data {
        Some(bytes) => req.send_bytes(bytes),
        None => req.call(),
    };
    let response = match result {
        Ok(value) | Err(ureq::Error::Status(_, value)) => value,
        Err(_) => return Err("Binary host connection unavailable".into()),
    };
    let status = response.status();
    let content_type = response
        .header("Content-Type")
        .unwrap_or("application/octet-stream")
        .to_string();
    let mut bytes = Vec::new();
    response
        .into_reader()
        .take(64 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "Binary response interrupted")?;
    if bytes.len() > 64 * 1024 * 1024 {
        return Err("Transfer exceeds 64 MiB".into());
    }
    Ok(NativeBinaryResponse {
        status,
        body_base64: STANDARD.encode(bytes),
        content_type,
    })
}
#[tauri::command]
pub async fn workspace_binary(
    app: AppHandle,
    window: WebviewWindow,
    path: String,
    method: String,
    body_base64: Option<String>,
    content_type: String,
) -> Result<NativeBinaryResponse, String> {
    let port = trusted(&app, &window)?;
    if body_base64
        .as_ref()
        .is_some_and(|s| s.len() > 90 * 1024 * 1024)
    {
        return Err("Transfer exceeds 64 MiB".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        use base64::{engine::general_purpose::STANDARD, Engine as _};
        let bytes = body_base64
            .map(|s| STANDARD.decode(s).map_err(|_| "Invalid transfer encoding"))
            .transpose()?;
        if bytes.as_ref().is_some_and(|b| b.len() > 64 * 1024 * 1024) {
            return Err("Transfer exceeds 64 MiB".into());
        }
        let state = app.state::<NativeSession>();
        let mut session = state
            .0
            .lock()
            .map_err(|_| "Session coordinator unavailable")?;
        if session.expires <= now() + 20 {
            refresh(&app, port, &mut session)?;
        }
        let mut result = binary_call(
            port,
            &path,
            &method,
            bytes.as_deref(),
            &content_type,
            &session.access,
        )?;
        if result.status == 401 {
            refresh(&app, port, &mut session)?;
            result = binary_call(
                port,
                &path,
                &method,
                bytes.as_deref(),
                &content_type,
                &session.access,
            )?;
        }
        Ok(result)
    })
    .await
    .map_err(|_| "Binary transfer worker unavailable")?
}
#[derive(Serialize, Deserialize)]
struct WindowPlacement {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
    #[serde(default = "default_scale")]
    scale: f64,
    #[serde(default)]
    frame_width: u32,
    #[serde(default)]
    frame_height: u32,
    #[serde(default)]
    monitor: Option<SavedMonitor>,
}
#[derive(Serialize, Deserialize)]
struct SavedMonitor {
    name: Option<String>,
    x: i32,
    y: i32,
    width: u32,
    height: u32,
    scale: f64,
}
fn default_scale() -> f64 {
    1.0
}
fn monitor_area(monitor: &tauri::Monitor) -> window_geometry::WorkArea {
    let area = monitor.work_area();
    window_geometry::WorkArea {
        x: area.position.x,
        y: area.position.y,
        width: area.size.width,
        height: area.size.height,
        scale: monitor.scale_factor(),
    }
}
fn placement_filename(label: &str) -> Option<String> {
    if !workspace_label(label)
        || label.len() > 200
        || !label
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
    {
        return None;
    }
    Some(format!("workspace-window-{label}.json"))
}
fn detached_label(panel: &str, session: Option<&str>, slot: usize) -> String {
    let context = session
        .map(|id| format!("session-{id}"))
        .unwrap_or_else(|| "unassigned".into());
    format!("workspace-{panel}-{context}-{slot}")
}
fn valid_session_id(session_id: &Option<String>) -> bool {
    session_id.as_ref().map_or(true, |id| {
        !id.is_empty()
            && id.len() <= 128
            && id.chars().all(|character| {
                character.is_ascii_alphanumeric() || character == '-' || character == '_'
            })
    })
}
fn valid_handoff(payload: &Value, session_id: &Option<String>) -> bool {
    payload["version"] == 1
        && session_id
            .as_deref()
            .is_some_and(|id| payload["sessionId"] == id)
        && payload["buffers"]
            .as_array()
            .is_some_and(|rows| rows.len() <= 100)
        && (payload["draft"].is_null() || payload["draft"].is_object())
        && serde_json::to_vec(payload).is_ok_and(|bytes| bytes.len() <= 16 * 1024 * 1024)
}
fn pending_live(item: &RedockPending, source: &str, owner: &str, accepted: bool) -> bool {
    item.source == source
        && item.owner == owner
        && (!accepted || item.accepted)
        && item.created.elapsed() < Duration::from_secs(60)
}
fn redock_authority(
    app: &AppHandle,
    port: u16,
    session_id: &Option<String>,
) -> Result<String, String> {
    let state = app.state::<NativeSession>();
    let mut session = state
        .0
        .lock()
        .map_err(|_| "Session coordinator unavailable")?;
    if session.expires <= now() + 20 {
        refresh(app, port, &mut session)?;
    }
    let current = call(port, "/auth/me", "GET", None, Some(&session.access), None)?;
    if current.status != 200 {
        return Err("Sign in again before returning this workspace".into());
    }
    if let Some(identifier) = session_id {
        let resource = call(
            port,
            &format!("/api/workspace/sessions/{identifier}?turns=false"),
            "GET",
            None,
            Some(&session.access),
            None,
        )?;
        if resource.status != 200 {
            return Err("Conversation authority changed; the detached window remains open".into());
        }
    }
    current.body["principal"]["id"]
        .as_str()
        .filter(|id| !id.is_empty())
        .map(str::to_string)
        .ok_or_else(|| "Current workspace owner unavailable".into())
}
#[tauri::command]
pub async fn workspace_redock(
    app: AppHandle,
    window: WebviewWindow,
    session_id: Option<String>,
    panel: String,
    payload: Value,
) -> Result<String, String> {
    let port = trusted(&app, &window)?;
    if !window.label().starts_with("workspace-")
        || !valid_session_id(&session_id)
        || !["chat", "workbench", "browser", "computer"].contains(&panel.as_str())
        || !valid_handoff(&payload, &session_id)
    {
        return Err("Invalid or oversized detached workspace handoff".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let main = app
            .get_webview_window("main")
            .ok_or("Main workspace unavailable; detached window remains open")?;
        trusted(&app, &main)?;
        let owner = redock_authority(&app, port, &session_id)?;
        let id = format!(
            "{:016x}{:016x}",
            rand::random::<u64>(),
            rand::random::<u64>()
        );
        let state = app.state::<RedockCoordinator>();
        {
            let mut pending = state
                .0
                .lock()
                .map_err(|_| "Handoff coordinator unavailable")?;
            pending.retain(|_, item| item.created.elapsed() < Duration::from_secs(60));
            if pending.len() >= 16 || pending.values().any(|item| item.source == window.label()) {
                return Err("A workspace handoff is already pending".into());
            }
            pending.insert(
                id.clone(),
                RedockPending {
                    source: window.label().into(),
                    owner: owner.clone(),
                    session_id: session_id.clone(),
                    created: Instant::now(),
                    accepted: false,
                },
            );
        }
        let event = RedockEvent {
            id: id.clone(),
            source: window.label().into(),
            owner_id: owner,
            session_id,
            panel,
            payload,
        };
        restore_placement(&app, &main);
        let deliver = main
            .show()
            .and_then(|_| main.unminimize())
            .and_then(|_| main.set_focus())
            .and_then(|_| app.emit_to("main", "termx-native-redock", event));
        if deliver.is_err() {
            if let Ok(mut pending) = state.0.lock() {
                pending.remove(&id);
            }
            return Err(
                "Main workspace could not accept the handoff; detached window remains open".into(),
            );
        }
        Ok(id)
    })
    .await
    .map_err(|_| "Workspace handoff worker unavailable")?
}
#[tauri::command]
pub async fn workspace_redock_accept(
    app: AppHandle,
    window: WebviewWindow,
    id: String,
) -> Result<(), String> {
    let port = trusted(&app, &window)?;
    if window.label() != "main" {
        return Err("Only the main workspace can accept a handoff".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<RedockCoordinator>();
        let (owner, session_id) = {
            let pending = state
                .0
                .lock()
                .map_err(|_| "Handoff coordinator unavailable")?;
            let item = pending
                .get(&id)
                .filter(|item| item.created.elapsed() < Duration::from_secs(60))
                .ok_or("Handoff expired; detached window remains open")?;
            (item.owner.clone(), item.session_id.clone())
        };
        if redock_authority(&app, port, &session_id)? != owner {
            return Err("Handoff owner changed".into());
        }
        let mut pending = state
            .0
            .lock()
            .map_err(|_| "Handoff coordinator unavailable")?;
        let item = pending.get_mut(&id).ok_or("Handoff cancelled")?;
        if item.owner != owner || item.created.elapsed() >= Duration::from_secs(60) {
            return Err("Handoff expired or changed; detached window remains open".into());
        }
        item.accepted = true;
        if app
            .emit_to(
                &item.source,
                "termx-native-redock-accepted",
                json!({"id":id}),
            )
            .is_err()
        {
            item.accepted = false;
            return Err("Detached workspace unavailable; its state has not been closed".into());
        }
        Ok(())
    })
    .await
    .map_err(|_| "Workspace acceptance worker unavailable")?
}
#[tauri::command]
pub async fn workspace_redock_commit(
    app: AppHandle,
    window: WebviewWindow,
    id: String,
) -> Result<(), String> {
    let port = trusted(&app, &window)?;
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<RedockCoordinator>();
        let (owner, session_id) = {
            let pending = state
                .0
                .lock()
                .map_err(|_| "Handoff coordinator unavailable")?;
            let item = pending
                .get(&id)
                .filter(|item| {
                    item.source == window.label()
                        && item.accepted
                        && item.created.elapsed() < Duration::from_secs(60)
                })
                .ok_or("Handoff has not been acknowledged; window remains open")?;
            (item.owner.clone(), item.session_id.clone())
        };
        if redock_authority(&app, port, &session_id)? != owner {
            return Err("Handoff owner changed; window remains open".into());
        }
        // Cancellation or timeout may occur while live host authority is checked.
        // Hold the coordinator through close so neither can race the effect.
        let mut pending = state
            .0
            .lock()
            .map_err(|_| "Handoff coordinator unavailable")?;
        if !pending
            .get(&id)
            .is_some_and(|item| pending_live(item, window.label(), &owner, true))
        {
            return Err("Handoff cancelled or expired; window remains open".into());
        }
        window
            .close()
            .map_err(|_| "Could not close detached workspace; its state remains available")?;
        pending.remove(&id);
        Ok(())
    })
    .await
    .map_err(|_| "Workspace return worker unavailable")?
}
#[tauri::command]
pub fn workspace_redock_cancel(
    app: AppHandle,
    window: WebviewWindow,
    id: String,
) -> Result<(), String> {
    trusted(&app, &window)?;
    let state = app.state::<RedockCoordinator>();
    let mut pending = state
        .0
        .lock()
        .map_err(|_| "Handoff coordinator unavailable")?;
    if pending
        .get(&id)
        .is_some_and(|item| item.source == window.label() || window.label() == "main")
    {
        if let Some(item) = pending.remove(&id) {
            let _ = app.emit_to(
                &item.source,
                "termx-native-redock-cancelled",
                json!({"id":id}),
            );
        }
    }
    Ok(())
}
#[tauri::command]
pub fn workspace_detach(
    app: AppHandle,
    window: WebviewWindow,
    session_id: Option<String>,
    panel: String,
) -> Result<String, String> {
    let port = trusted(&app, &window)?;
    if !["chat", "workbench", "browser", "computer"].contains(&panel.as_str())
        || !valid_session_id(&session_id)
    {
        return Err("Invalid detached workspace request".into());
    }
    // Reuse stable placement slots for the same panel/session across launches.
    // Simultaneously open windows each have their own independent slot/file.
    let label = (0..1024)
        .map(|slot| detached_label(&panel, session_id.as_deref(), slot))
        .find(|label| app.get_webview_window(label).is_none())
        .ok_or("Too many detached workspace windows")?;
    let mut url =
        tauri::Url::parse(&format!("http://127.0.0.1:{port}/")).map_err(|_| "Invalid host URL")?;
    url.query_pairs_mut().append_pair("layout", &panel);
    if let Some(id) = session_id {
        url.query_pairs_mut().append_pair("session", &id);
    }
    let detached = WebviewWindowBuilder::new(&app, &label, WebviewUrl::External(url))
        .title("TermX workspace")
        .inner_size(1100.0, 780.0)
        .min_inner_size(760.0, 520.0)
        .build()
        .map_err(|_| "Could not open workspace window")?;
    restore_placement(&app, &detached);
    track_placement(&app, &detached);
    Ok(label)
}
pub fn restore_placement(app: &AppHandle, window: &WebviewWindow) {
    let Some(filename) = placement_filename(window.label()) else {
        return;
    };
    let directory = crate::config::data_dir(app);
    // Migrate the former shared file only into the main window's placement.
    let text = std::fs::read_to_string(directory.join(filename)).or_else(|error| {
        if window.label() == "main" && error.kind() == std::io::ErrorKind::NotFound {
            std::fs::read_to_string(directory.join("workspace-window.json"))
        } else {
            Err(error)
        }
    });
    // Fresh and malformed placements use the same monitor recovery as saved
    // windows, so even the initial window fits a small work area.
    let saved = text
        .ok()
        .and_then(|text| serde_json::from_str::<WindowPlacement>(&text).ok())
        .or_else(|| capture_placement(window));
    if let Some(saved) = saved {
        if let Ok(monitors) = window.available_monitors() {
            let areas: Vec<_> = monitors.iter().map(monitor_area).collect();
            let preferred = saved.monitor.as_ref().and_then(|previous| {
                // Prefer an exact identity when duplicate display names exist.
                monitors
                    .iter()
                    .position(|monitor| {
                        monitor.name().cloned() == previous.name
                            && monitor.work_area().position.x == previous.x
                            && monitor.work_area().position.y == previous.y
                    })
                    .or_else(|| {
                        previous.name.as_ref().and_then(|name| {
                            let matches: Vec<_> = monitors
                                .iter()
                                .enumerate()
                                .filter(|(_, monitor)| monitor.name() == Some(name))
                                .collect();
                            if matches.len() == 1 {
                                Some(matches[0].0)
                            } else {
                                None
                            }
                        })
                    })
            });
            let primary = window.primary_monitor().ok().flatten().and_then(|primary| {
                monitors.iter().position(|monitor| {
                    monitor.position() == primary.position() && monitor.size() == primary.size()
                })
            });
            let previous_area = saved
                .monitor
                .as_ref()
                .map(|previous| window_geometry::WorkArea {
                    x: previous.x,
                    y: previous.y,
                    width: previous.width,
                    height: previous.height,
                    scale: previous.scale,
                });
            // Measure current decorations, including changes to OS chrome.
            // Express them in the saved scale before recovery scales them
            // into the selected monitor's physical work area.
            let frame = window.outer_size().ok().zip(window.inner_size().ok());
            let current_scale = window.scale_factor().unwrap_or(1.0);
            let valid_scale = if saved.scale.is_finite() && (0.25..=8.0).contains(&saved.scale) {
                saved.scale
            } else {
                1.0
            };
            let saved_frame = |actual: u32| {
                (f64::from(actual) * valid_scale / current_scale.max(0.25)).round() as u32
            };
            let (frame_width, frame_height) = frame
                .map(|(outer, inner)| {
                    (
                        saved_frame(outer.width.saturating_sub(inner.width)),
                        saved_frame(outer.height.saturating_sub(inner.height)),
                    )
                })
                .unwrap_or((saved.frame_width, saved.frame_height));
            let restored = window_geometry::recover(
                window_geometry::SavedWindow {
                    x: saved.x,
                    y: saved.y,
                    width: saved.width,
                    height: saved.height,
                    scale: saved.scale,
                    frame_width,
                    frame_height,
                    previous_area,
                },
                &areas,
                preferred,
                primary,
            );
            let _ = window.set_min_size(Some(PhysicalSize::new(
                restored.minimum_width,
                restored.minimum_height,
            )));
            let _ = window.set_size(PhysicalSize::new(restored.width, restored.height));
            let _ = window.set_position(PhysicalPosition::new(restored.x, restored.y));
        }
    }
}
fn capture_placement(window: &WebviewWindow) -> Option<WindowPlacement> {
    let position = window.outer_position().ok()?;
    let size = window.inner_size().ok()?;
    let outer = window.outer_size().unwrap_or(size);
    Some(WindowPlacement {
        x: position.x,
        y: position.y,
        width: size.width,
        height: size.height,
        scale: window.scale_factor().unwrap_or(1.0),
        frame_width: outer.width.saturating_sub(size.width),
        frame_height: outer.height.saturating_sub(size.height),
        monitor: window.current_monitor().ok().flatten().map(|monitor| {
            let area = monitor_area(&monitor);
            SavedMonitor {
                name: monitor.name().cloned(),
                x: area.x,
                y: area.y,
                width: area.width,
                height: area.height,
                scale: area.scale,
            }
        }),
    })
}
pub fn track_placement(app: &AppHandle, window: &WebviewWindow) {
    let Some(filename) = placement_filename(window.label()) else {
        return;
    };
    let handle = app.clone();
    let observed = window.clone();
    window.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
            if let Some(saved) = capture_placement(&observed) {
                if let Ok(text) = serde_json::to_string(&saved) {
                    let _ = std::fs::write(crate::config::data_dir(&handle).join(&filename), text);
                }
            }
        }
    });
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn handoff_state_is_bounded_and_bound_to_the_exact_conversation() {
        let id = Some("session-1".into());
        let mut state = json!({"version":1,"sessionId":"session-1","buffers":[],"draft":null});
        assert!(valid_handoff(&state, &id));
        assert!(!valid_handoff(&state, &Some("session-2".into())));
        assert!(!valid_handoff(&state, &None));
        state["buffers"] = json!(vec![json!({}); 101]);
        assert!(!valid_handoff(&state, &id));
        state["buffers"] = json!([]);
        state["extra"] = json!("x".repeat(16 * 1024 * 1024));
        assert!(!valid_handoff(&state, &id));
        assert!(!valid_session_id(&Some("../other".into())));
        assert!(!valid_session_id(&Some("".into())));
    }
    #[test]
    fn handoff_close_requires_live_acknowledgement_source_owner_and_deadline() {
        let mut item = RedockPending {
            source: "workspace-chat-session-1-0".into(),
            owner: "owner-1".into(),
            session_id: Some("session-1".into()),
            created: Instant::now(),
            accepted: false,
        };
        assert!(!pending_live(&item, &item.source, &item.owner, true));
        item.accepted = true;
        assert!(pending_live(&item, &item.source, &item.owner, true));
        assert!(!pending_live(&item, "main", &item.owner, true));
        assert!(!pending_live(&item, &item.source, "owner-2", true));
        item.created = Instant::now() - Duration::from_secs(61);
        assert!(!pending_live(&item, &item.source, &item.owner, true));
    }
    #[test]
    fn detached_placement_keys_are_stable_and_do_not_overwrite_main_or_each_other() {
        let first = detached_label("chat", Some("session-1"), 0);
        assert_eq!(first, detached_label("chat", Some("session-1"), 0));
        for other in [
            "main".into(),
            detached_label("chat", Some("session-1"), 1),
            detached_label("workbench", Some("session-1"), 0),
            detached_label("chat", Some("session-2"), 0),
        ] {
            assert_ne!(placement_filename(&first), placement_filename(&other));
        }
        assert!(placement_filename("workspace-../../other").is_none());
    }
    #[test]
    fn legacy_window_placement_remains_readable() {
        let legacy: WindowPlacement =
            serde_json::from_str(r#"{"x":40,"y":40,"width":1100,"height":780}"#).unwrap();
        assert_eq!(legacy.scale, 1.0);
        assert!(legacy.monitor.is_none());
    }
}

#[cfg(test)]
mod bridge_tests {
    use super::{safe_host_path, workspace_label};

    #[test]
    fn bridge_paths_cannot_normalize_into_a_privileged_auth_endpoint() {
        for path in [
            "//evil.example/api",
            "/api/%2e%2e/auth/login",
            "/api/%252e%252e/auth/login",
            "/api/../auth/refresh",
            "/api/x\\auth",
            "/api/x\r\nHost: evil.example",
            "/api/x#fragment",
        ] {
            assert!(!safe_host_path(path), "{path:?}");
        }
        assert!(safe_host_path(
            "/api/files/download?path=%2Ftmp%2Ffixture.py"
        ));
        assert!(safe_host_path("/auth/me"));
    }

    #[test]
    fn workspace_bridge_labels_include_connection_window_but_not_remote_surfaces() {
        for label in ["main", "connect", "workspace-123"] {
            assert!(workspace_label(label));
        }
        for label in ["browser", "remote-tab", "preview", "main-untrusted"] {
            assert!(!workspace_label(label));
        }
    }
}

#[cfg(test)]
mod platform_credentials_tests {
    #[test]
    #[ignore = "Requires an isolated, unlocked OS credential service"]
    fn os_keyring_synthetic_roundtrip() {
        assert_eq!(std::env::var("TERMX_TEST_OS_KEYRING").as_deref(), Ok("1"));
        // Unique fixture namespace; this never reads a user's TermX entry.
        let service = format!(
            "termx-ci-fixture-{}-{}",
            std::process::id(),
            rand::random::<u64>()
        );
        let entry =
            keyring::Entry::new(&service, "synthetic-account").expect("OS credential entry");
        struct Cleanup(keyring::Entry);
        impl Drop for Cleanup {
            fn drop(&mut self) {
                let _ = self.0.delete_credential();
            }
        }
        let fixture = Cleanup(entry);
        fixture
            .0
            .set_password("synthetic-noncredential-test-value")
            .expect("OS credential write");
        assert_eq!(
            fixture.0.get_password().expect("OS credential read"),
            "synthetic-noncredential-test-value"
        );
        fixture.0.delete_credential().expect("OS credential delete");
        assert!(matches!(
            fixture.0.get_password(),
            Err(keyring::Error::NoEntry)
        ));
    }
}
