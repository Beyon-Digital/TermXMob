use rand::distributions::Alphanumeric;
use rand::Rng;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;
use tauri::{AppHandle, Manager};

pub const DEFAULT_PORT: u16 = 8787;

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct DesktopConfig {
    pub passcode: String,
    #[serde(default = "default_port")]
    pub port: u16,
    #[serde(default)]
    pub onboarded: bool,
}

fn default_port() -> u16 {
    DEFAULT_PORT
}

impl Default for DesktopConfig {
    fn default() -> Self {
        Self {
            passcode: random_passcode(),
            port: DEFAULT_PORT,
            onboarded: false,
        }
    }
}

pub fn random_passcode() -> String {
    rand::thread_rng()
        .sample_iter(&Alphanumeric)
        .take(32)
        .map(char::from)
        .collect()
}

pub fn data_dir(app: &AppHandle) -> PathBuf {
    if let Some(directory) = desktop_directory_override(std::env::var_os("TERMX_DESKTOP_DATA_DIR"))
    {
        return directory;
    }
    app.path()
        .app_data_dir()
        .unwrap_or_else(|_| std::env::temp_dir().join("termx-desktop"))
}

fn desktop_directory_override(value: Option<std::ffi::OsString>) -> Option<PathBuf> {
    value
        .map(PathBuf::from)
        .filter(|path| path.is_absolute() && path.parent().is_some())
}

pub fn config_dir(app: &AppHandle) -> PathBuf {
    data_dir(app).join("config")
}

pub fn log_dir(app: &AppHandle) -> PathBuf {
    if desktop_directory_override(std::env::var_os("TERMX_DESKTOP_DATA_DIR")).is_some() {
        return data_dir(app).join("logs");
    }
    app.path()
        .app_log_dir()
        .unwrap_or_else(|_| data_dir(app).join("logs"))
}

pub fn desktop_config_path(app: &AppHandle) -> PathBuf {
    data_dir(app).join("desktop.json")
}

pub fn pid_path(app: &AppHandle) -> PathBuf {
    data_dir(app).join("backend.pid")
}

pub fn load(app: &AppHandle) -> std::io::Result<DesktopConfig> {
    let path = desktop_config_path(app);
    crate::private_storage::file(&path)?;
    if let Ok(text) = fs::read_to_string(&path) {
        if let Ok(config) = serde_json::from_str::<DesktopConfig>(&text) {
            return Ok(config);
        }
    }
    let config = DesktopConfig::default();
    save(app, &config)?;
    Ok(config)
}

pub fn save(app: &AppHandle, config: &DesktopConfig) -> std::io::Result<()> {
    let path = desktop_config_path(app);
    crate::private_storage::file(&path)?;
    let text = serde_json::to_string_pretty(config).map_err(std::io::Error::other)?;
    fs::write(path, text)
}

pub fn migrate_legacy(app: &AppHandle) {
    // An explicitly selected portable directory starts independently. Never
    // copy another profile's tokens/configuration into it implicitly.
    if desktop_directory_override(std::env::var_os("TERMX_DESKTOP_DATA_DIR")).is_some() {
        return;
    }
    let destination = config_dir(app);
    if destination.exists() {
        return;
    }
    let mut legacy = None;
    if let Some(home) = std::env::var_os("HOME") {
        let candidate = PathBuf::from(home).join(".config").join("termx");
        if candidate.is_dir() {
            legacy = Some(candidate);
        }
    }
    if let Some(legacy) = legacy {
        if fs::create_dir_all(&destination).is_ok() {
            for name in ["config.json", "tokens.json", "audit.jsonl"] {
                let source = legacy.join(name);
                if source.is_file() {
                    let _ = fs::copy(&source, destination.join(name));
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn desktop_data_override_requires_an_absolute_directory() {
        let absolute = std::env::temp_dir().join("termx-isolated-native-fixture");
        assert_eq!(
            desktop_directory_override(Some(absolute.clone().into_os_string())),
            Some(absolute)
        );
        for invalid in ["", "relative", "../other-profile"] {
            assert!(desktop_directory_override(Some(invalid.into())).is_none());
        }
        assert!(desktop_directory_override(None).is_none());
    }
}

pub fn prepare_dirs(app: &AppHandle) -> std::io::Result<()> {
    for directory in [data_dir(app), config_dir(app), log_dir(app)] {
        crate::private_storage::directory(&directory)?;
    }
    migrate_legacy(app);
    Ok(())
}

pub fn set_onboarded(app: &AppHandle) -> std::io::Result<()> {
    let mut config = load(app)?;
    if !config.onboarded {
        config.onboarded = true;
        save(app, &config)?;
    }
    Ok(())
}
