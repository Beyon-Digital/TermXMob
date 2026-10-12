//! A single update operation survives closing/reopening its bundled window.
//! Only this local window receives the update command capability.
use serde::Serialize;
use std::sync::Mutex;
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_updater::{Update, UpdaterExt};

#[derive(Clone, Copy, Default, PartialEq, Debug, Serialize)]
#[serde(rename_all = "snake_case")]
enum Phase {
    #[default]
    Idle,
    Checking,
    Current,
    Available,
    Downloading,
    Verifying,
    Downloaded,
    Installing,
    Ready,
    Restarting,
    Error,
}

#[derive(Clone, Default, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Snapshot {
    phase: Phase,
    current_version: String,
    version: Option<String>,
    notes: Option<String>,
    downloaded: u64,
    total: Option<u64>,
    error: Option<String>,
    windows: bool,
}

#[derive(Default)]
struct Operation {
    snapshot: Snapshot,
    update: Option<Update>,
    bytes: Option<Vec<u8>>,
}

impl Operation {
    fn begin(&mut self, action: &str) -> Result<(), String> {
        let allowed = match action {
            "check" => matches!(
                self.snapshot.phase,
                Phase::Idle | Phase::Current | Phase::Available | Phase::Error
            ),
            "download" => self.snapshot.phase == Phase::Available && self.update.is_some(),
            "install" => self.snapshot.phase == Phase::Downloaded && self.bytes.is_some(),
            "restart" => self.snapshot.phase == Phase::Ready,
            _ => false,
        };
        if !allowed {
            return Err("This update action is not available right now.".into());
        }
        self.snapshot.error = None;
        self.snapshot.phase = match action {
            "check" => {
                self.update = None;
                self.bytes = None;
                self.snapshot.version = None;
                self.snapshot.notes = None;
                self.snapshot.downloaded = 0;
                self.snapshot.total = None;
                Phase::Checking
            }
            "download" => Phase::Downloading,
            "install" => Phase::Installing,
            _ => Phase::Restarting,
        };
        Ok(())
    }
}

#[derive(Default)]
pub struct Coordinator(Mutex<Operation>);

fn mutate(app: &AppHandle, f: impl FnOnce(&mut Operation)) {
    let state = app.state::<Coordinator>();
    let mut operation = state.0.lock().unwrap_or_else(|error| error.into_inner());
    f(&mut operation);
}

fn fail(app: &AppHandle, context: &str, error: impl std::fmt::Display) {
    mutate(app, |operation| {
        operation.snapshot.phase = Phase::Error;
        operation.snapshot.error = Some(format!("{context}: {error}"));
        operation.bytes = None;
    });
}

fn guard(window: &WebviewWindow) -> Result<(), String> {
    if window.label() != "updater" {
        return Err("Updates are only available in the desktop update window.".into());
    }
    let url = window.url().map_err(|error| error.to_string())?;
    let local = url.scheme() == "tauri"
        || (matches!(url.scheme(), "http" | "https") && url.host_str() == Some("tauri.localhost"));
    if !local || url.path() != "/updater.html" {
        return Err("Untrusted update window.".into());
    }
    Ok(())
}

#[tauri::command]
pub fn desktop_update_state(window: WebviewWindow, app: AppHandle) -> Result<Snapshot, String> {
    guard(&window)?;
    let state = app.state::<Coordinator>();
    let operation = state.0.lock().map_err(|error| error.to_string())?;
    let mut snapshot = operation.snapshot.clone();
    snapshot.current_version = app.package_info().version.to_string();
    snapshot.windows = cfg!(target_os = "windows");
    Ok(snapshot)
}

#[tauri::command]
pub fn desktop_update_action(
    window: WebviewWindow,
    app: AppHandle,
    action: String,
) -> Result<(), String> {
    guard(&window)?;
    start(&app, &action)
}

pub fn show(app: &AppHandle) {
    let result = if let Some(window) = app.get_webview_window("updater") {
        window
            .show()
            .and_then(|_| window.unminimize())
            .and_then(|_| window.set_focus())
    } else {
        WebviewWindowBuilder::new(app, "updater", WebviewUrl::App("updater.html".into()))
            .title("Termx — Software Update")
            .inner_size(560.0, 640.0)
            .min_inner_size(360.0, 420.0)
            .on_navigation(|url| {
                (url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost"))
                    && url.path() == "/updater.html"
            })
            .build()
            .map(|_| ())
    };
    if let Err(error) = result {
        crate::ui::notify(app, "Could not open Software Update", &error.to_string());
        return;
    }
    // Reopening an in-flight operation must not start another download or lose
    // a verified package. Current/error states can be checked again explicitly.
    let idle = app
        .state::<Coordinator>()
        .0
        .lock()
        .map(|o| o.snapshot.phase == Phase::Idle)
        .unwrap_or(false);
    if idle {
        let _ = start(app, "check");
    }
}

fn start(app: &AppHandle, action: &str) -> Result<(), String> {
    let state = app.state::<Coordinator>();
    let mut operation = state.0.lock().map_err(|error| error.to_string())?;
    operation.begin(action)?;
    let update = operation.update.clone();
    let bytes = if action == "install" {
        operation.bytes.take()
    } else {
        None
    };
    drop(operation);
    let handle = app.clone();
    match action {
        "check" => {
            tauri::async_runtime::spawn(async move {
                let exit_handle = handle.clone();
                let updater = handle
                    .updater_builder()
                    .timeout(std::time::Duration::from_secs(60))
                    .on_before_exit(move || {
                        if let Some(backend) = exit_handle.try_state::<crate::backend::Backend>() {
                            backend.stop();
                        }
                    })
                    .build();
                match updater {
                    Ok(updater) => match updater.check().await {
                        Ok(Some(mut update)) => mutate(&handle, |operation| {
                            update.timeout = Some(std::time::Duration::from_secs(30 * 60));
                            operation.snapshot.phase = Phase::Available;
                            operation.snapshot.version = Some(update.version.clone());
                            operation.snapshot.notes = update.body.clone();
                            operation.update = Some(update);
                        }),
                        Ok(None) => mutate(&handle, |o| o.snapshot.phase = Phase::Current),
                        Err(error) => fail(&handle, "Could not check for updates", error),
                    },
                    Err(error) => fail(&handle, "Updates are unavailable for this build", error),
                }
            });
        }
        "download" => {
            tauri::async_runtime::spawn(async move {
                let update = update.expect("available phase has a checked update");
                match update
                    .download(
                        |chunk, total| {
                            mutate(&handle, |operation| {
                                operation.snapshot.downloaded =
                                    operation.snapshot.downloaded.saturating_add(chunk as u64);
                                operation.snapshot.total = total.filter(|size| *size > 0);
                            })
                        },
                        || mutate(&handle, |o| o.snapshot.phase = Phase::Verifying),
                    )
                    .await
                {
                    Ok(bytes) => mutate(&handle, |operation| {
                        operation.bytes = Some(bytes);
                        operation.snapshot.phase = Phase::Downloaded;
                    }),
                    Err(error) => fail(&handle, "Download or signature verification failed", error),
                }
            });
        }
        "install" => {
            tauri::async_runtime::spawn_blocking(move || {
                let update = update.expect("downloaded phase has a checked update");
                match update.install(bytes.expect("downloaded phase has verified bytes")) {
                    Ok(()) => mutate(&handle, |o| o.snapshot.phase = Phase::Ready),
                    Err(error) => fail(&handle, "Could not install the update", error),
                }
            });
        }
        "restart" => {
            tauri::async_runtime::spawn_blocking(move || {
                if let Some(backend) = handle.try_state::<crate::backend::Backend>() {
                    backend.stop();
                }
                handle.restart();
            });
        }
        _ => unreachable!(),
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_install_without_verified_bytes_and_duplicate_operations() {
        let mut operation = Operation::default();
        assert!(operation.begin("install").is_err());
        assert!(operation.begin("download").is_err());
        assert!(operation.begin("restart").is_err());
        assert!(operation.begin("check").is_ok());
        assert!(operation.begin("check").is_err());
        for phase in [
            Phase::Downloading,
            Phase::Verifying,
            Phase::Installing,
            Phase::Restarting,
            Phase::Downloaded,
            Phase::Ready,
        ] {
            operation.snapshot.phase = phase;
            assert!(operation.begin("check").is_err());
        }
        operation.snapshot.phase = Phase::Downloaded;
        assert!(operation.begin("install").is_err());
        operation.bytes = Some(vec![1]);
        assert!(operation.begin("install").is_ok());
        assert!(operation.begin("install").is_err());
        operation.snapshot.phase = Phase::Ready;
        assert!(operation.begin("restart").is_ok());
    }

    #[test]
    fn retry_resets_stale_metadata_and_download_progress() {
        let mut operation = Operation::default();
        operation.snapshot.phase = Phase::Error;
        operation.snapshot.downloaded = 99;
        operation.snapshot.total = Some(100);
        operation.snapshot.error = Some("network failure".into());
        operation.snapshot.version = Some("old".into());
        assert!(operation.begin("check").is_ok());
        assert_eq!(operation.snapshot.downloaded, 0);
        assert!(operation.snapshot.total.is_none());
        assert!(operation.snapshot.version.is_none());
        assert!(operation.snapshot.error.is_none());
    }
}
