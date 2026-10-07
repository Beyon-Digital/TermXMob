//! Trusted native workspace bridge. Refresh credentials never cross into JS.
use keyring::Entry;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::{
    AppHandle, Manager, PhysicalPosition, PhysicalSize, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder,
};

const SERVICE: &str = "com.jaexxxy.termx.workspace.session";
#[derive(Default)]
struct MemorySession {
    access: String,
    expires: u64,
    host_id: String,
}
#[derive(Default)]
pub struct NativeSession(Mutex<MemorySession>);
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
}
fn visible_position(saved: &WindowPlacement, monitors: &[tauri::Monitor]) -> (i32, i32) {
    let intersects = monitors.iter().any(|m| {
        let p = m.position();
        let size = m.size();
        saved.x.saturating_add(80) > p.x
            && saved.y.saturating_add(40) > p.y
            && saved.x < p.x.saturating_add(size.width as i32)
            && saved.y < p.y.saturating_add(size.height as i32)
    });
    if intersects {
        return (saved.x, saved.y);
    }
    monitors
        .first()
        .map(|m| (m.position().x + 40, m.position().y + 40))
        .unwrap_or((40, 40))
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
        || session_id.as_ref().is_some_and(|s| {
            s.len() > 128
                || !s
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        })
    {
        return Err("Invalid detached workspace request".into());
    }
    let label = format!("workspace-{}", rand::random::<u64>());
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
    let path = crate::config::data_dir(app).join("workspace-window.json");
    if let Ok(text) = std::fs::read_to_string(path) {
        if let Ok(saved) = serde_json::from_str::<WindowPlacement>(&text) {
            if let Ok(monitors) = window.available_monitors() {
                let (x, y) = visible_position(&saved, &monitors);
                let max = monitors
                    .iter()
                    .map(|m| m.size().width)
                    .max()
                    .unwrap_or(1600);
                let height = monitors
                    .iter()
                    .map(|m| m.size().height)
                    .max()
                    .unwrap_or(1000);
                let _ = window.set_size(PhysicalSize::new(
                    saved.width.clamp(760, max.max(760)),
                    saved.height.clamp(520, height.max(520)),
                ));
                let _ = window.set_position(PhysicalPosition::new(x, y));
            }
        }
    }
}
pub fn track_placement(app: &AppHandle, window: &WebviewWindow) {
    let handle = app.clone();
    let observed = window.clone();
    window.on_window_event(move |event| {
        if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
            if let (Ok(p), Ok(s)) = (observed.outer_position(), observed.inner_size()) {
                let saved = WindowPlacement {
                    x: p.x,
                    y: p.y,
                    width: s.width,
                    height: s.height,
                };
                if let Ok(text) = serde_json::to_string(&saved) {
                    let _ = std::fs::write(
                        crate::config::data_dir(&handle).join("workspace-window.json"),
                        text,
                    );
                }
            }
        }
    });
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn empty_monitor_recovery() {
        let p = WindowPlacement {
            x: -9000,
            y: 200,
            width: 1100,
            height: 780,
        };
        assert_eq!(visible_position(&p, &[]), (40, 40));
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
