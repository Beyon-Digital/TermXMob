//! The shell controls its own user service through the packaged controller.
//! Service children and runtime files remain owned by that controller.
use serde_json::Value;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};
use tauri::AppHandle;

pub fn control(app: &AppHandle, action: &str, port: u16, passcode: &str) -> Result<Value, String> {
    if !["install", "start", "stop", "status", "uninstall"].contains(&action) {
        return Err("Unsupported background host action".into());
    }
    let mut command = Command::new(crate::backend::backend_binary(app));
    command
        .arg("service")
        .arg(action)
        .arg("--config-dir")
        .arg(crate::config::config_dir(app))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if action == "install" || action == "start" {
        command
            .arg("--port")
            .arg(port.to_string())
            .arg("--host")
            .arg("127.0.0.1")
            .env("TERMX_SERVICE_PASSCODE", passcode);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let mut child = command
        .spawn()
        .map_err(|_| "Background host controller could not start")?;
    let deadline = Instant::now() + Duration::from_secs(45);
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(100)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("Background host controller did not finish; inspect host status before retrying".into());
            }
        }
    }
    let output = child
        .wait_with_output()
        .map_err(|_| "Background host controller result unavailable")?;
    let result: Value = serde_json::from_slice(&output.stdout)
        .map_err(|_| "Background host controller returned an invalid result")?;
    if !output.status.success() {
        return Err(result["error"]
            .as_str()
            .or_else(|| result["last_error"].as_str())
            .unwrap_or("Background host operation failed")
            .to_string());
    }
    Ok(result)
}

pub fn verify(app: &AppHandle, status: &Value) -> Result<u16, String> {
    let port = status["port"]
        .as_u64()
        .and_then(|port| u16::try_from(port).ok())
        .filter(|port| *port > 0)
        .ok_or("Background host has no valid local port")?;
    let path = crate::config::config_dir(app).join("host-runtime.json");
    std::fs::symlink_metadata(&path).map_err(|_| "Background host private runtime unavailable")?;
    crate::private_storage::file(&path)
        .map_err(|_| "Background host private runtime is not owned by this user")?;
    let private: Value = serde_json::from_slice(
        &std::fs::read(path).map_err(|_| "Background host private runtime unavailable")?,
    )
    .map_err(|_| "Background host private runtime invalid")?;
    let proof = private["proof_token"]
        .as_str()
        .filter(|token| token.len() == 64 && token.bytes().all(|c| c.is_ascii_hexdigit()))
        .ok_or("Background host private identity proof unavailable")?;
    let runtime: Value = ureq::AgentBuilder::new()
        .try_proxy_from_env(false)
        .redirects(0)
        .timeout(Duration::from_secs(2))
        .build()
        .get(&format!("http://127.0.0.1:{port}/api/host/runtime"))
        .set("X-Termx-Host-Proof", proof)
        .call()
        .map_err(|_| "Background host identity unavailable")?
        .into_json()
        .map_err(|_| "Background host identity invalid")?;
    crate::service_identity::verified_port(status, &runtime)
        .ok_or_else(|| "Background host identity does not match this managed profile".into())
}
