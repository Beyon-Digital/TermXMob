use serde_json::{json, Value};
use std::fs;
use std::io::{BufRead, BufReader};
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Manager};

use crate::config::{self, DesktopConfig};
use crate::{logging, ui};

#[derive(Clone, Debug, Default)]
pub struct ReadyInfo {
    pub port: u16,
    pub passcode: String,
    pub urls: Vec<String>,
    pub tunnel: Option<String>,
}

impl ReadyInfo {
    pub fn window_url(&self) -> String {
        format!("http://127.0.0.1:{}/?k={}", self.port, self.passcode)
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum State {
    Idle,
    Starting,
    Running,
    Stopping,
    Failed,
}

struct Inner {
    app: AppHandle,
    config: Mutex<DesktopConfig>,
    child: Mutex<Option<Child>>,
    info: Mutex<Option<ReadyInfo>>,
    state: Mutex<State>,
    ready_at: Mutex<Option<Instant>>,
    stop_requested: AtomicBool,
    quitting: AtomicBool,
    adopted: AtomicBool,
    restarts: AtomicU32,
}

#[derive(Clone)]
pub struct Backend(Arc<Inner>);

impl Backend {
    pub fn new(app: AppHandle) -> Self {
        let config = config::load(&app);
        let manager = Self(Arc::new(Inner {
            app,
            config: Mutex::new(config),
            child: Mutex::new(None),
            info: Mutex::new(None),
            state: Mutex::new(State::Idle),
            ready_at: Mutex::new(None),
            stop_requested: AtomicBool::new(false),
            quitting: AtomicBool::new(false),
            adopted: AtomicBool::new(false),
            restarts: AtomicU32::new(0),
        }));
        manager
    }

    pub fn info(&self) -> Option<ReadyInfo> {
        self.0.info.lock().ok().and_then(|value| value.clone())
    }

    pub fn passcode(&self) -> String {
        self.0
            .config
            .lock()
            .map(|config| config.passcode.clone())
            .unwrap_or_default()
    }

    pub fn port(&self) -> u16 {
        self.0.config.lock().map(|config| config.port).unwrap_or(config::DEFAULT_PORT)
    }

    pub fn start(&self) {
        let manager = self.clone();
        thread::spawn(move || manager.run_session());
    }

    pub fn restart(&self) {
        let manager = self.clone();
        self.0.stop_requested.store(true, Ordering::SeqCst);
        thread::spawn(move || {
            manager.stop();
            manager.0.restarts.store(0, Ordering::SeqCst);
            manager.0.stop_requested.store(false, Ordering::SeqCst);
            manager.0.state.lock().map(|mut state| *state = State::Starting).ok();
            manager.run_session();
        });
    }

    pub fn stop(&self) {
        self.0.stop_requested.store(true, Ordering::SeqCst);
        if let Some(mut state) = self.0.state.lock().ok() {
            if *state != State::Idle {
                *state = State::Stopping;
            }
        }
        let info = self.info();
        let adopted = self.0.adopted.load(Ordering::SeqCst);
        if let Some(info) = info {
            if !adopted {
                let _ = api_json(
                    info.port,
                    "POST",
                    "/api/shutdown",
                    &info.passcode,
                    Some(json!({})),
                );
            }
        }
        let deadline = Instant::now() + Duration::from_secs(6);
        loop {
            let exited = {
                let mut guard = self.0.child.lock().unwrap();
                match guard.as_mut() {
                    Some(child) => child.try_wait().ok().flatten().is_some(),
                    None => true,
                }
            };
            if exited || Instant::now() >= deadline {
                break;
            }
            thread::sleep(Duration::from_millis(100));
        }
        if let Some(mut child) = self.0.child.lock().unwrap().take() {
            kill_tree(&mut child);
            let _ = child.wait();
            logging::desktop(&self.0.app, "backend process tree terminated");
        }
        let _ = fs::remove_file(config::pid_path(&self.0.app));
        if let Ok(mut state) = self.0.state.lock() {
            *state = State::Idle;
        }
        if let Ok(mut info) = self.0.info.lock() {
            *info = None;
        }
        if let Ok(mut ready) = self.0.ready_at.lock() {
            *ready = None;
        }
    }

    fn run_session(&self) {
        if self.0.quitting.load(Ordering::SeqCst) || self.0.stop_requested.load(Ordering::SeqCst) {
            return;
        }
        self.cleanup_stale();
        let config = self.0.config.lock().unwrap().clone();
        let (port, adopted) = self.pick_port(&config);
        self.0.adopted.store(adopted, Ordering::SeqCst);
        if let Ok(mut state) = self.0.state.lock() {
            *state = State::Starting;
        }
        if adopted {
            let info = ReadyInfo {
                port,
                passcode: config.passcode.clone(),
                urls: vec![format!("http://127.0.0.1:{port}")],
                tunnel: None,
            };
            logging::desktop(&self.0.app, &format!("adopted existing backend on port {port}"));
            self.handle_ready(info);
            return;
        }
        match self.spawn_backend(port, &config) {
            Ok(child) => {
                *self.0.child.lock().unwrap() = Some(child);
                self.write_pid_file(port, &config);
                self.spawn_monitor();
            }
            Err(error) => {
                logging::desktop(&self.0.app, &format!("failed to spawn backend: {error}"));
                self.fail(&format!("Termx backend could not start: {error}"));
            }
        }
    }

    fn spawn_backend(&self, port: u16, config: &DesktopConfig) -> std::io::Result<Child> {
        let binary = backend_binary(&self.0.app);
        logging::desktop(
            &self.0.app,
            &format!("spawning {} --desktop --port {port}", binary.display()),
        );
        let mut command = Command::new(&binary);
        command
            .arg("--desktop")
            .arg("--host")
            .arg("0.0.0.0")
            .arg("--port")
            .arg(port.to_string())
            .arg("--passcode")
            .arg(&config.passcode)
            .arg("--parent-pid")
            .arg(std::process::id().to_string())
            .env("TERMX_DESKTOP", "1")
            .env("TERMX_CONFIG_DIR", config::config_dir(&self.0.app))
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some(socket) = crate::broker::socket_path() {
            command.arg("--broker").arg(socket);
        }
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
            command.creation_flags(CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP);
        }
        let mut child = command.spawn()?;
        if let Some(stdout) = child.stdout.take() {
            let manager = self.clone();
            thread::spawn(move || {
                let reader = BufReader::new(stdout);
                for line in reader.lines().map_while(Result::ok) {
                    manager.handle_stdout(&line);
                }
            });
        }
        if let Some(stderr) = child.stderr.take() {
            let app = self.0.app.clone();
            thread::spawn(move || {
                let reader = BufReader::new(stderr);
                for line in reader.lines().map_while(Result::ok) {
                    logging::backend(&app, &line);
                }
            });
        }
        Ok(child)
    }

    fn spawn_monitor(&self) {
        let manager = self.clone();
        thread::spawn(move || {
            loop {
                thread::sleep(Duration::from_millis(250));
                if manager.0.quitting.load(Ordering::SeqCst) {
                    return;
                }
                let status = {
                    let mut guard = manager.0.child.lock().unwrap();
                    match guard.as_mut() {
                        Some(child) => child.try_wait(),
                        None => return,
                    }
                };
                match status {
                    Ok(None) => continue,
                    Ok(Some(status)) => {
                        let expected = manager.0.stop_requested.load(Ordering::SeqCst);
                        manager.on_exit(status.code(), expected);
                        return;
                    }
                    Err(_) => return,
                }
            }
        });
    }

    fn on_exit(&self, code: Option<i32>, expected: bool) {
        *self.0.child.lock().unwrap() = None;
        let _ = fs::remove_file(config::pid_path(&self.0.app));
        if let Ok(mut info) = self.0.info.lock() {
            *info = None;
        }
        if expected || self.0.quitting.load(Ordering::SeqCst) || self.0.stop_requested.load(Ordering::SeqCst) {
            if let Ok(mut state) = self.0.state.lock() {
                *state = State::Idle;
            }
            return;
        }
        let was_stable = self
            .0
            .ready_at
            .lock()
            .ok()
            .and_then(|value| *value)
            .map(|at| at.elapsed() > Duration::from_secs(60))
            .unwrap_or(false);
        if was_stable {
            self.0.restarts.store(0, Ordering::SeqCst);
        }
        let attempt = self.0.restarts.fetch_add(1, Ordering::SeqCst) + 1;
        logging::desktop(
            &self.0.app,
            &format!("backend exited unexpectedly (code {code:?}), restart attempt {attempt}"),
        );
        if attempt > 3 {
            self.fail("Termx backend stopped unexpectedly. Use the Termx menu to restart it.");
            return;
        }
        let delay = Duration::from_secs(1 << (attempt - 1));
        ui::notify(
            &self.0.app,
            "Termx backend stopped",
            "Restarting the local service…",
        );
        thread::sleep(delay);
        if self.0.stop_requested.load(Ordering::SeqCst) || self.0.quitting.load(Ordering::SeqCst) {
            return;
        }
        if let Ok(mut state) = self.0.state.lock() {
            *state = State::Starting;
        }
        self.run_session();
    }

    fn fail(&self, message: &str) {
        if let Ok(mut state) = self.0.state.lock() {
            *state = State::Failed;
        }
        logging::desktop(&self.0.app, message);
        ui::notify(&self.0.app, "Termx backend failed", message);
        ui::on_backend_failed(&self.0.app, message);
    }

    fn handle_stdout(&self, line: &str) {
        logging::backend(&self.0.app, line);
        let trimmed = line.trim();
        if !trimmed.starts_with('{') {
            return;
        }
        let Ok(value) = serde_json::from_str::<Value>(trimmed) else {
            return;
        };
        match value.get("termx").and_then(Value::as_str) {
            Some("ready") => {
                let port = value
                    .get("port")
                    .and_then(Value::as_u64)
                    .map(|value| value as u16)
                    .unwrap_or_else(|| self.port());
                let urls = value
                    .get("urls")
                    .and_then(Value::as_array)
                    .map(|items| {
                        items
                            .iter()
                            .filter_map(|item| item.as_str().map(str::to_string))
                            .collect()
                    })
                    .unwrap_or_default();
                let tunnel = value
                    .get("tunnel")
                    .and_then(Value::as_str)
                    .map(str::to_string);
                let info = ReadyInfo {
                    port,
                    passcode: self.passcode(),
                    urls,
                    tunnel,
                };
                self.handle_ready(info);
            }
            Some("permission") => {
                let which = value
                    .get("which")
                    .and_then(Value::as_str)
                    .unwrap_or("screen_recording");
                ui::permission_event(&self.0.app, which);
            }
            Some("notify") => {
                let title = value
                    .get("title")
                    .and_then(Value::as_str)
                    .unwrap_or("Termx");
                let body = value.get("body").and_then(Value::as_str).unwrap_or("");
                let kind = value.get("kind").and_then(Value::as_str).unwrap_or("info");
                if kind == "ready" && ui::main_window_visible(&self.0.app) {
                    return;
                }
                ui::notify(&self.0.app, title, body);
            }
            _ => {}
        }
    }

    fn handle_ready(&self, info: ReadyInfo) {
        *self.0.info.lock().unwrap() = Some(info.clone());
        *self.0.ready_at.lock().unwrap() = Some(Instant::now());
        if let Ok(mut state) = self.0.state.lock() {
            *state = State::Running;
        }
        logging::desktop(
            &self.0.app,
            &format!(
                "backend ready on port {} (urls: {:?}, tunnel: {:?})",
                info.port, info.urls, info.tunnel
            ),
        );
        ui::on_backend_ready(&self.0.app, &info);
    }

    fn write_pid_file(&self, port: u16, config: &DesktopConfig) {
        let path = config::pid_path(&self.0.app);
        if let Some(parent) = path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        let pid = self
            .0
            .child
            .lock()
            .ok()
            .and_then(|guard| guard.as_ref().map(|child| child.id()));
        if let Some(pid) = pid {
            let payload = json!({"pid": pid, "port": port, "passcode": config.passcode});
            let _ = fs::write(path, payload.to_string());
        }
    }

    fn cleanup_stale(&self) {
        let path = config::pid_path(&self.0.app);
        let Ok(text) = fs::read_to_string(&path) else {
            return;
        };
        let _ = fs::remove_file(&path);
        let Ok(value) = serde_json::from_str::<Value>(&text) else {
            return;
        };
        let Some(pid) = value.get("pid").and_then(Value::as_u64) else {
            return;
        };
        let pid = pid as u32;
        if process_is_backend(pid) {
            logging::desktop(&self.0.app, &format!("cleaning up stale backend pid {pid}"));
            kill_pid_tree(pid);
        }
    }

    fn pick_port(&self, config: &DesktopConfig) -> (u16, bool) {
        let preferred = config.port;
        if self.adoptable(preferred, &config.passcode) {
            return (preferred, true);
        }
        if port_free(preferred) {
            return (preferred, false);
        }
        for port in preferred.saturating_add(1)..=preferred.saturating_add(70) {
            if self.adoptable(port, &config.passcode) {
                return (port, true);
            }
            if port_free(port) {
                return (port, false);
            }
        }
        (ephemeral_port(), false)
    }

    fn adoptable(&self, port: u16, passcode: &str) -> bool {
        if !is_termx(port) {
            return false;
        }
        api_json(port, "GET", "/api/machine", passcode, None).is_some()
    }
}

pub fn backend_binary(app: &AppHandle) -> PathBuf {
    let name = if cfg!(windows) {
        "termx-backend.exe"
    } else {
        "termx-backend"
    };
    if let Ok(resource_dir) = app.path().resource_dir() {
        let candidate = resource_dir.join("backend").join(name);
        if candidate.is_file() {
            return candidate;
        }
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("resources")
        .join("backend")
        .join(name);
    dev
}

pub fn api_json(port: u16, method: &str, path: &str, passcode: &str, body: Option<Value>) -> Option<Value> {
    let url = format!("http://127.0.0.1:{port}{path}");
    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_millis(2000))
        .build();
    let request = match method {
        "POST" => agent.post(&url),
        _ => agent.get(&url),
    }
    .set("X-Termx-Passcode", passcode);
    let result = match body {
        Some(value) => request.send_json(value),
        None => request.call(),
    };
    match result {
        Ok(response) if (200..300).contains(&response.status()) => response.into_json().ok(),
        _ => None,
    }
}

pub fn api_get(app: &AppHandle, path: &str) -> Option<Value> {
    let backend = app.try_state::<Backend>()?;
    let info = backend.info()?;
    api_json(info.port, "GET", path, &info.passcode, None)
}

pub fn api_post(app: &AppHandle, path: &str, body: Value) -> Option<Value> {
    let backend = app.try_state::<Backend>()?;
    let info = backend.info()?;
    api_json(info.port, "POST", path, &info.passcode, Some(body))
}

fn is_termx(port: u16) -> bool {
    let url = format!("http://127.0.0.1:{port}/api/health");
    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_millis(800))
        .build();
    match agent.get(&url).call() {
        Ok(response) if (200..300).contains(&response.status()) => {
            let value: Option<Value> = response.into_json().ok();
            value
                .and_then(|body| body.get("app").and_then(Value::as_str).map(str::to_string))
                .map(|app| app == "termx")
                .unwrap_or(false)
        }
        _ => false,
    }
}

fn port_free(port: u16) -> bool {
    if TcpListener::bind(("127.0.0.1", port)).is_err() {
        return false;
    }
    // A wildcard listener may still allow the loopback bind above on macOS
    // (SO_REUSEADDR semantics), so an accepted connection also means occupied.
    let address = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    std::net::TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_err()
}

fn ephemeral_port() -> u16 {
    TcpListener::bind(("127.0.0.1", 0))
        .and_then(|listener| listener.local_addr())
        .map(|address| address.port())
        .unwrap_or(config::DEFAULT_PORT)
}

#[cfg(unix)]
pub fn kill_tree(child: &mut Child) {
    let pid = child.id() as i32;
    unsafe {
        libc::kill(-pid, libc::SIGKILL);
    }
    let _ = child.kill();
}

#[cfg(windows)]
pub fn kill_tree(child: &mut Child) {
    let pid = child.id();
    let _ = Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    let _ = child.kill();
}

#[cfg(unix)]
fn kill_pid_tree(pid: u32) {
    unsafe {
        libc::kill(-(pid as i32), libc::SIGKILL);
        libc::kill(pid as i32, libc::SIGKILL);
    }
}

#[cfg(windows)]
fn kill_pid_tree(pid: u32) {
    let _ = Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}

#[cfg(unix)]
fn process_is_backend(pid: u32) -> bool {
    let output = Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "command="])
        .output();
    match output {
        Ok(output) => String::from_utf8_lossy(&output.stdout).contains("termx-backend"),
        Err(_) => false,
    }
}

#[cfg(windows)]
fn process_is_backend(pid: u32) -> bool {
    let output = Command::new("tasklist")
        .args(["/FI", &format!("PID eq {pid}"), "/NH", "/FO", "CSV"])
        .output();
    match output {
        Ok(output) => String::from_utf8_lossy(&output.stdout).contains("termx-backend"),
        Err(_) => false,
    }
}
