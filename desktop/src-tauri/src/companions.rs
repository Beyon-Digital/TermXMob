//! Native companion controller. HTTP polling, image decoding and animation
//! timing are Rust owned; platform adapters only render bounded RGBA frames.
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};
use tauri::{AppHandle, Emitter, Listener, Manager, WebviewWindow};
#[path = "companion_motion.rs"]
mod motion;
use motion::{Body, Habitat, Rect};

#[repr(C)]
#[derive(Default, Clone, Copy)]
struct Geometry {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    scale: f64,
    kind: u32,
}
#[repr(C)]
#[derive(Default)]
struct NativeEvent {
    slot: u32,
    kind: u32,
    x: f64,
    y: f64,
}
extern "C" {
    fn termx_companion_available() -> bool;
    fn termx_companion_awake() -> bool;
    fn termx_companion_reduced_motion() -> bool;
    fn termx_companion_geometry(out: *mut Geometry, capacity: usize) -> usize;
    fn termx_companion_render(
        slot: u32,
        x: f64,
        y: f64,
        width: f64,
        height: f64,
        anchor_x: f64,
        anchor_y: f64,
        hit_x: f64,
        hit_y: f64,
        hit_width: f64,
        hit_height: f64,
        rgba: *const u8,
        pixel_width: u32,
        pixel_height: u32,
        flip: bool,
    ) -> bool;
    fn termx_companion_hide(slot: u32);
    fn termx_companion_event(out: *mut NativeEvent) -> bool;
    fn termx_companion_shutdown();
}
#[derive(Clone, Default, Deserialize)]
struct Preferences {
    #[serde(default)]
    enabled: bool,
    #[serde(default = "default_size")]
    size: f64,
    #[serde(default)]
    activity: String,
    #[serde(default)]
    reduced_motion: bool,
    #[serde(default)]
    hidden: Vec<String>,
}
fn default_size() -> f64 {
    96.0
}
#[derive(Clone, Default, Deserialize)]
struct Snapshot {
    #[serde(default)]
    enabled: bool,
    #[serde(default)]
    preferences: Preferences,
    #[serde(default)]
    pets: Vec<Pet>,
}
#[derive(Clone, Deserialize)]
struct Pet {
    id: String,
    session_id: String,
    #[serde(default)]
    state: String,
    pack_id: String,
    pack_revision: u32,
    #[serde(default)]
    roaming: bool,
    action: Option<Action>,
}
#[derive(Clone, Deserialize)]
struct Action {
    id: String,
    name: String,
    #[serde(default)]
    target: Option<String>,
    #[serde(default)]
    expires_at: serde_json::Value,
}
#[derive(Deserialize)]
struct Size {
    width: u32,
    height: u32,
}
#[derive(Deserialize)]
struct Point {
    x: f64,
    y: f64,
}
#[derive(Deserialize)]
struct Bounds {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}
#[derive(Deserialize)]
struct AtlasInfo {
    file: String,
    width: u32,
    height: u32,
}
#[derive(Clone, Deserialize)]
struct Frame {
    atlas: usize,
    x: u32,
    y: u32,
    width: u32,
    height: u32,
    duration_ms: u64,
}
#[derive(Deserialize)]
struct Clip {
    frames: Vec<Frame>,
    #[serde(rename = "loop", default)]
    repeat: bool,
}
#[derive(Deserialize)]
struct Manifest {
    version: u32,
    id: String,
    #[serde(deserialize_with = "revision_number")]
    revision: u32,
    logical_size: Size,
    foot_anchor: Point,
    interaction_bounds: Bounds,
    atlases: Vec<AtlasInfo>,
    clips: BTreeMap<String, Clip>,
    states: BTreeMap<String, String>,
}
fn revision_number<'de, D: serde::Deserializer<'de>>(deserializer: D) -> Result<u32, D::Error> {
    let value = serde_json::Value::deserialize(deserializer)?;
    match value {
        serde_json::Value::Number(n) => n.as_u64().and_then(|n| u32::try_from(n).ok()),
        serde_json::Value::String(s) => s.parse().ok(),
        _ => None,
    }
    .ok_or_else(|| serde::de::Error::custom("Invalid character revision"))
}
struct Atlas {
    bytes: Vec<u8>,
    width: u32,
}
struct Pack {
    manifest: Manifest,
    atlases: Vec<Atlas>,
}
impl Pack {
    fn load(app: &AppHandle, id: &str, revision: u32) -> Result<Self, String> {
        if !identifier(id) {
            return Err("Invalid character identifier".into());
        }
        let raw = crate::workspace::companion_read(
            app,
            &format!("/api/characters/packs/{id}/manifest?revision={revision}"),
        )?;
        let manifest: Manifest =
            serde_json::from_slice(&raw).map_err(|_| "Invalid character manifest")?;
        validate(&manifest, id, revision)?;
        let mut atlases = Vec::new();
        for info in &manifest.atlases {
            let raw = crate::workspace::companion_read(
                app,
                &format!(
                    "/api/characters/packs/{id}/assets/{}?revision={revision}",
                    info.file
                ),
            )?;
            // PNG IHDR dimensions are checked before decoding to prevent a
            // compressed image from allocating beyond the native pack budget.
            if raw.len() < 24
                || &raw[..8] != b"\x89PNG\r\n\x1a\n"
                || &raw[12..16] != b"IHDR"
                || u32::from_be_bytes(raw[16..20].try_into().unwrap()) != info.width
                || u32::from_be_bytes(raw[20..24].try_into().unwrap()) != info.height
            {
                return Err("Atlas dimensions differ from manifest".into());
            }
            let image = tauri::image::Image::from_bytes(&raw)
                .map_err(|_| "Atlas PNG could not be decoded")?;
            if image.width() != info.width || image.height() != info.height {
                return Err("Invalid decoded atlas".into());
            }
            atlases.push(Atlas {
                bytes: image.rgba().to_vec(),
                width: image.width(),
            });
        }
        Ok(Self { manifest, atlases })
    }
    fn frame(&self, clip: &str, elapsed: Duration, reduced: bool) -> Option<&Frame> {
        let clip = self
            .manifest
            .clips
            .get(clip)
            .or_else(|| self.manifest.clips.get("idle"))?;
        let total: u64 = clip.frames.iter().map(|f| f.duration_ms).sum();
        let mut time = if reduced {
            0
        } else if clip.repeat {
            (elapsed.as_millis() as u64) % total
        } else {
            (elapsed.as_millis() as u64).min(total - 1)
        };
        for frame in &clip.frames {
            if time < frame.duration_ms {
                return Some(frame);
            }
            time -= frame.duration_ms;
        }
        clip.frames.last()
    }
    fn pixels(&self, frame: &Frame) -> Vec<u8> {
        let atlas = &self.atlases[frame.atlas];
        let mut bytes = Vec::with_capacity((frame.width * frame.height * 4) as usize);
        for row in frame.y..frame.y + frame.height {
            let start = ((row * atlas.width + frame.x) * 4) as usize;
            bytes.extend_from_slice(&atlas.bytes[start..start + (frame.width * 4) as usize]);
        }
        bytes
    }
}
fn identifier(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}
fn validate(m: &Manifest, id: &str, revision: u32) -> Result<(), String> {
    let valid = m.version == 1
        && m.id == id
        && m.revision == revision
        && (1..=512).contains(&m.logical_size.width)
        && (1..=512).contains(&m.logical_size.height)
        && m.foot_anchor.x.is_finite()
        && m.foot_anchor.y.is_finite()
        && m.foot_anchor.x >= 0.0
        && m.foot_anchor.x <= f64::from(m.logical_size.width)
        && m.foot_anchor.y >= 0.0
        && m.foot_anchor.y <= f64::from(m.logical_size.height)
        && !m.atlases.is_empty()
        && m.atlases.len() <= 32
        && m.clips.contains_key("idle")
        && m.clips.len() <= 64;
    if !valid {
        return Err("Unsupported character manifest".into());
    }
    let b = &m.interaction_bounds;
    if ![b.x, b.y, b.width, b.height].iter().all(|v| v.is_finite())
        || b.x < 0.0
        || b.y < 0.0
        || b.width < 1.0
        || b.height < 1.0
        || b.x + b.width > f64::from(m.logical_size.width)
        || b.y + b.height > f64::from(m.logical_size.height)
    {
        return Err("Invalid interaction bounds".into());
    }
    let mut pixels = 0u64;
    for atlas in &m.atlases {
        if atlas.file.len() > 128
            || !atlas.file.ends_with(".png")
            || atlas
                .file
                .bytes()
                .any(|b| !b.is_ascii_alphanumeric() && b != b'-' && b != b'_' && b != b'.')
            || atlas.file.contains("..")
            || atlas.width == 0
            || atlas.height == 0
            || atlas.width > 4096
            || atlas.height > 4096
        {
            return Err("Invalid atlas".into());
        }
        pixels += u64::from(atlas.width) * u64::from(atlas.height);
    }
    if pixels > 16 * 1024 * 1024 {
        return Err("Character atlases exceed 64 MiB decoded budget".into());
    }
    let mut total_frames = 0;
    for clip in m.clips.values() {
        total_frames += clip.frames.len();
        if total_frames > 4096 {
            return Err("Character exceeds total frame budget".into());
        }
        if clip.frames.is_empty() || clip.frames.len() > 256 {
            return Err("Invalid animation length".into());
        }
        for frame in &clip.frames {
            let atlas = m
                .atlases
                .get(frame.atlas)
                .ok_or("Missing animation atlas")?;
            if frame.width != m.logical_size.width
                || frame.height != m.logical_size.height
                || frame.width == 0
                || frame.height == 0
                || frame.width > 512
                || frame.height > 512
                || frame
                    .x
                    .checked_add(frame.width)
                    .map_or(true, |v| v > atlas.width)
                || frame
                    .y
                    .checked_add(frame.height)
                    .map_or(true, |v| v > atlas.height)
                || !(16..=10000).contains(&frame.duration_ms)
            {
                return Err("Invalid animation frame".into());
            }
        }
    }
    if m.states.values().any(|clip| !m.clips.contains_key(clip)) {
        return Err("Missing state animation".into());
    }
    Ok(())
}
struct Instance {
    pet: Pet,
    slot: u32,
    body: Body,
    clip: String,
    started: Instant,
    action: String,
    action_until: Instant,
    pixels: Vec<u8>,
    frame_key: Option<(usize, u32, u32, u32, u32)>,
}
#[derive(Default)]
struct Shared {
    snapshot: Snapshot,
    packs: BTreeMap<(String, u32), Arc<Pack>>,
    updated: Option<Instant>,
}
struct Runtime {
    shared: Arc<Mutex<Shared>>,
    instances: BTreeMap<String, Instance>,
    habitat: Habitat,
    geometry_at: Instant,
    last: Instant,
    available: bool,
    visible: bool,
}
#[derive(Clone)]
pub struct Coordinator {
    shared: Arc<Mutex<Shared>>,
    home: Arc<AtomicBool>,
    available: Arc<AtomicBool>,
    epoch: Arc<AtomicU64>,
    suspended: Arc<AtomicBool>,
}
impl Coordinator {
    pub fn suspend(&self) {
        self.suspended.store(true, Ordering::SeqCst);
        self.epoch.fetch_add(1, Ordering::SeqCst);
        if let Ok(mut shared) = self.shared.lock() {
            shared.updated = None;
            shared.snapshot = Snapshot::default();
            shared.packs.clear();
        }
    }
}
#[derive(Clone, Serialize)]
pub struct Capabilities {
    desktop: bool,
    backend: &'static str,
    reason: &'static str,
}
#[tauri::command]
pub fn companion_capabilities(
    app: AppHandle,
    window: WebviewWindow,
) -> Result<Capabilities, String> {
    crate::workspace::trusted(&app, &window)?;
    let desktop = app.state::<Coordinator>().available.load(Ordering::Relaxed);
    let backend = if !desktop {
        "in-app"
    } else if cfg!(target_os = "macos") {
        "appkit"
    } else if cfg!(windows) {
        "win32-layered"
    } else if desktop {
        "gtk-x11"
    } else {
        "in-app"
    };
    Ok(Capabilities {
        desktop,
        backend,
        reason: if desktop {
            ""
        } else {
            "The native desktop habitat is unavailable; use the in-app habitat."
        },
    })
}
#[tauri::command]
pub fn companion_home(app: AppHandle, window: WebviewWindow) -> Result<(), String> {
    crate::workspace::trusted(&app, &window)?;
    app.state::<Coordinator>()
        .home
        .store(true, Ordering::Relaxed);
    Ok(())
}
fn action_duration(action: &Action) -> Option<Duration> {
    use std::time::{SystemTime, UNIX_EPOCH};
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64();
    // Backend emits epoch seconds. Missing or malformed expiry never persists.
    action
        .expires_at
        .as_f64()
        .filter(|expiry| expiry.is_finite() && *expiry > now && *expiry <= now + 16.0)
        .map(|expiry| Duration::from_secs_f64((expiry - now).min(15.0)))
}
pub fn start(app: &AppHandle) {
    // SAFETY: setup and all rendering callbacks run on the Tauri main thread;
    // bridge globals never cross threads. No pointers are retained by Rust.
    let available = unsafe { termx_companion_available() };
    let coordinator = Coordinator {
        shared: Arc::new(Mutex::new(Shared::default())),
        home: Arc::new(AtomicBool::new(false)),
        available: Arc::new(AtomicBool::new(available)),
        epoch: Arc::new(AtomicU64::new(0)),
        suspended: Arc::new(AtomicBool::new(false)),
    };
    app.manage(coordinator.clone());
    let locked = coordinator.clone();
    app.listen("termx-session-locked", move |_| locked.suspend());
    if !available {
        return;
    }
    let reader_app = app.clone();
    let data = coordinator.shared.clone();
    let reader_epoch = coordinator.epoch.clone();
    let reader_suspended = coordinator.suspended.clone();
    let reader_available = coordinator.available.clone();
    std::thread::spawn(move || {
        while !crate::QUITTING.load(Ordering::Relaxed) {
            if !reader_available.load(Ordering::Relaxed) {
                break;
            }
            if reader_suspended.load(Ordering::SeqCst) {
                std::thread::sleep(Duration::from_millis(500));
                continue;
            }
            let epoch = reader_epoch.load(Ordering::SeqCst);
            let result = crate::workspace::companion_read(&reader_app, "/api/companions/runtime")
                .and_then(|bytes| {
                    serde_json::from_slice::<Snapshot>(&bytes)
                        .map_err(|_| "Invalid companion snapshot".into())
                });
            match result {
                Ok(mut snapshot) => {
                    snapshot.pets.truncate(128);
                    let keys: BTreeSet<_> = snapshot
                        .pets
                        .iter()
                        .filter(|p| p.roaming)
                        .take(4)
                        .map(|p| (p.pack_id.clone(), p.pack_revision))
                        .collect();
                    for (id, revision) in &keys {
                        let exists = data
                            .lock()
                            .map(|s| s.packs.contains_key(&(id.clone(), *revision)))
                            .unwrap_or(false);
                        if !exists {
                            if let Ok(pack) = Pack::load(&reader_app, id, *revision) {
                                if let Ok(mut s) = data.lock() {
                                    if epoch == reader_epoch.load(Ordering::SeqCst) {
                                        s.packs.insert((id.clone(), *revision), Arc::new(pack));
                                    }
                                }
                            }
                        }
                    }
                    if let Ok(mut s) = data.lock() {
                        if epoch != reader_epoch.load(Ordering::SeqCst) {
                            continue;
                        }
                        s.packs.retain(|key, _| keys.contains(key));
                        s.snapshot = snapshot;
                        s.updated = Some(Instant::now());
                    }
                }
                Err(_) => {
                    if let Ok(mut s) = data.lock() {
                        s.updated = None;
                    }
                }
            }
            std::thread::sleep(Duration::from_millis(500));
        }
    });
    let runtime = Arc::new(Mutex::new(Runtime {
        shared: coordinator.shared.clone(),
        instances: BTreeMap::new(),
        habitat: Habitat::default(),
        geometry_at: Instant::now() - Duration::from_secs(1),
        last: Instant::now(),
        available,
        visible: false,
    }));
    let timer_app = app.clone();
    let scheduled = Arc::new(AtomicBool::new(false));
    let rendering = Arc::new(AtomicBool::new(false));
    std::thread::spawn(move || {
        while !crate::QUITTING.load(Ordering::Relaxed) {
            // At most one UI callback can be pending, even while the app is
            // asleep, displaying a modal, or a system menu blocks the loop.
            if !scheduled.swap(true, Ordering::Relaxed) {
                let runtime = runtime.clone();
                let home = coordinator.home.clone();
                let done = scheduled.clone();
                let active = rendering.clone();
                let app = timer_app.clone();
                if timer_app
                    .run_on_main_thread(move || {
                        if let Ok(mut r) = runtime.lock() {
                            r.tick(&app, home.swap(false, Ordering::Relaxed));
                            active.store(r.visible, Ordering::Relaxed);
                        }
                        done.store(false, Ordering::Relaxed);
                    })
                    .is_err()
                {
                    break;
                }
            }
            std::thread::sleep(Duration::from_millis(
                if rendering.load(Ordering::Relaxed) {
                    16
                } else {
                    500
                },
            ));
        }
    });
}
impl Runtime {
    fn tick(&mut self, app: &AppHandle, home: bool) {
        let now = Instant::now();
        let dt = now.duration_since(self.last).as_secs_f64();
        self.last = now;
        // SAFETY: this method is only called by run_on_main_thread. All buffers
        // are initialized, bounded and live until each synchronous FFI returns.
        unsafe {
            let state = self.shared.lock().ok();
            let show = self.available
                && termx_companion_awake()
                && state.as_ref().is_some_and(|s| {
                    s.updated
                        .is_some_and(|t| now.duration_since(t) < Duration::from_secs(3))
                        && s.snapshot.enabled
                        && s.snapshot.preferences.enabled
                        && s.snapshot.pets.iter().any(|p| {
                            p.roaming
                                && !s.snapshot.preferences.hidden.contains(&p.id)
                                && s.packs.contains_key(&(p.pack_id.clone(), p.pack_revision))
                        })
                });
            if !show {
                if self.visible {
                    for slot in 0..4 {
                        termx_companion_hide(slot);
                    }
                    self.visible = false;
                }
                self.instances.clear();
                return;
            }
            self.visible = true;
            let state = state.unwrap();
            let mut preferences = state.snapshot.preferences.clone();
            preferences.reduced_motion |= termx_companion_reduced_motion();
            let pets = state.snapshot.pets.clone();
            let packs = state.packs.clone();
            drop(state);
            if now.duration_since(self.geometry_at) > Duration::from_millis(500) {
                let mut geometry = [Geometry::default(); 256];
                let count = termx_companion_geometry(geometry.as_mut_ptr(), geometry.len())
                    .min(geometry.len());
                let rect = |g: &Geometry| Rect {
                    x: g.x,
                    y: g.y,
                    width: g.width,
                    height: g.height,
                };
                self.habitat = Habitat::new(
                    geometry[..count]
                        .iter()
                        .filter(|g| g.kind == 0)
                        .map(|g| (rect(g), g.scale))
                        .collect(),
                    &geometry[..count]
                        .iter()
                        .filter(|g| g.kind == 1)
                        .map(rect)
                        .collect::<Vec<_>>(),
                );
                self.geometry_at = now;
            }
            let chosen: Vec<_> = pets
                .into_iter()
                .filter(|p| p.roaming && !preferences.hidden.contains(&p.id))
                .take(4)
                .collect();
            self.instances.retain(|id, instance| {
                let keep = chosen.iter().any(|p| &p.id == id);
                if !keep {
                    termx_companion_hide(instance.slot);
                }
                keep
            });
            for pet in chosen {
                if let Some(instance) = self.instances.get_mut(&pet.id) {
                    if instance.pet.pack_id != pet.pack_id
                        || instance.pet.pack_revision != pet.pack_revision
                    {
                        instance.frame_key = None;
                    }
                    instance.pet = pet;
                } else {
                    let slot = (0..4)
                        .find(|slot| !self.instances.values().any(|i| i.slot == *slot))
                        .unwrap_or(0);
                    self.instances.insert(
                        pet.id.clone(),
                        Instance {
                            pet,
                            slot,
                            body: Body::new(slot as usize),
                            clip: String::new(),
                            started: now,
                            action: String::new(),
                            action_until: now,
                            pixels: Vec::new(),
                            frame_key: None,
                        },
                    );
                }
            }
            let mut event = NativeEvent::default();
            for _ in 0..32 {
                if !termx_companion_event(&mut event) {
                    break;
                }
                if let Some(instance) = self.instances.values_mut().find(|i| i.slot == event.slot) {
                    match event.kind {
                        1 => {
                            let _ = app.emit(
                                "termx-companion-open-session",
                                json!({"session_id":instance.pet.session_id}),
                            );
                            if let Some(window) = app.get_webview_window("main") {
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                        }
                        2 => {
                            instance.body.stop_drag();
                            instance.body.dragging = true;
                        }
                        3 | 4 => {
                            instance.body.x = event.x;
                            instance.body.y = event.y;
                            instance.body.dragging = event.kind == 3;
                        }
                        5 => instance.body.paused = !instance.body.paused,
                        6 => instance.body.home(
                            &self.habitat,
                            preferences.size.clamp(48.0, 192.0)
                                * self.habitat.scale_at(f64::NAN, f64::NAN),
                        ),
                        7 => {
                            instance.body.hidden = true;
                            let handle = app.clone();
                            let id = instance.pet.id.clone();
                            std::thread::spawn(move || {
                                if crate::workspace::companion_hide_preference(&handle, &id)
                                    .is_err()
                                {
                                    crate::logging::desktop(
                                        &handle,
                                        "Could not persist companion visibility",
                                    );
                                }
                            });
                        }
                        _ => {}
                    }
                }
            }
            for slot in 0..4 {
                if !self
                    .instances
                    .values()
                    .any(|i| i.slot == slot && !i.body.hidden)
                {
                    termx_companion_hide(slot);
                }
            }
            let mut failed = false;
            for instance in self.instances.values_mut() {
                if instance.body.hidden {
                    continue;
                }
                let Some(pack) =
                    packs.get(&(instance.pet.pack_id.clone(), instance.pet.pack_revision))
                else {
                    termx_companion_hide(instance.slot);
                    continue;
                };
                let size = if preferences.size.is_finite() {
                    preferences.size.clamp(48.0, 192.0)
                } else {
                    96.0
                } * if home {
                    self.habitat.scale_at(f64::NAN, f64::NAN)
                } else {
                    self.habitat.scale_at(instance.body.x, instance.body.y)
                };
                if home {
                    instance.body.home(&self.habitat, size);
                }
                let blocked = ["waiting", "error", "paused"].contains(&instance.pet.state.as_str());
                if let Some(action) = &instance.pet.action {
                    if !blocked && instance.action != action.id {
                        if let Some(duration) = action_duration(action) {
                            instance.action = action.id.clone();
                            instance.action_until = now + duration;
                            instance.clip.clear();
                            if !preferences.reduced_motion
                                && ["approach", "hop"].contains(&action.name.as_str())
                                && action
                                    .target
                                    .as_deref()
                                    .map_or(true, |target| target == "nearest")
                            {
                                instance.body.approach(&self.habitat, size);
                                if action.name == "hop" && !instance.body.hopping {
                                    instance.body.hop_in_place(&self.habitat, size);
                                }
                            }
                            if action.name == "home" {
                                instance.body.home(
                                    &self.habitat,
                                    preferences.size.clamp(48.0, 192.0)
                                        * self.habitat.scale_at(f64::NAN, f64::NAN),
                                );
                            }
                        }
                    }
                }
                if blocked && instance.body.hopping {
                    instance.body.stop_drag();
                }
                let active = instance.pet.state != "idle" || instance.action_until > now;
                instance.body.tick(
                    &self.habitat,
                    dt,
                    size,
                    active,
                    preferences.reduced_motion,
                    preferences.activity == "playful",
                );
                let desired =
                    if ["waiting", "error", "paused"].contains(&instance.pet.state.as_str()) {
                        pack.manifest
                            .states
                            .get(&instance.pet.state)
                            .map(String::as_str)
                            .unwrap_or("idle")
                    } else if instance.body.dragging {
                        "drag"
                    } else if instance.body.hopping {
                        "hop"
                    } else if instance.body.falling {
                        "fall"
                    } else if instance.body.moving {
                        "walk"
                    } else if instance.action_until > now {
                        instance
                            .pet
                            .action
                            .as_ref()
                            .map(|a| {
                                if a.name == "sleep" {
                                    "sleeping"
                                } else {
                                    a.name.as_str()
                                }
                            })
                            .unwrap_or("idle")
                    } else if instance.pet.state == "idle" {
                        instance.body.idle_pose()
                    } else {
                        pack.manifest
                            .states
                            .get(&instance.pet.state)
                            .map(String::as_str)
                            .unwrap_or("idle")
                    };
                let desired = if pack.manifest.clips.contains_key(desired) {
                    desired
                } else {
                    "idle"
                };
                if instance.clip != desired {
                    let restored_completion =
                        instance.clip.is_empty() && instance.pet.state == "success";
                    instance.clip = desired.into();
                    instance.started = if restored_completion {
                        now - Duration::from_secs(5)
                    } else {
                        now
                    };
                }
                let Some(frame) = pack.frame(
                    &instance.clip,
                    now.duration_since(instance.started),
                    preferences.reduced_motion || instance.body.paused,
                ) else {
                    continue;
                };
                let key = (frame.atlas, frame.x, frame.y, frame.width, frame.height);
                if instance.frame_key != Some(key) {
                    instance.pixels = pack.pixels(frame);
                    instance.frame_key = Some(key);
                }
                let ratio = size
                    / f64::from(
                        pack.manifest
                            .logical_size
                            .width
                            .max(pack.manifest.logical_size.height),
                    );
                let width = f64::from(frame.width) * ratio;
                let height = f64::from(frame.height) * ratio;
                let anchor_x = if instance.body.direction < 0.0 {
                    f64::from(pack.manifest.logical_size.width) - pack.manifest.foot_anchor.x
                } else {
                    pack.manifest.foot_anchor.x
                };
                if !termx_companion_render(
                    instance.slot,
                    instance.body.x - anchor_x * ratio,
                    instance.body.y - pack.manifest.foot_anchor.y * ratio,
                    width,
                    height,
                    anchor_x * ratio,
                    pack.manifest.foot_anchor.y * ratio,
                    if instance.body.direction < 0.0 {
                        f64::from(pack.manifest.logical_size.width)
                            - pack.manifest.interaction_bounds.x
                            - pack.manifest.interaction_bounds.width
                    } else {
                        pack.manifest.interaction_bounds.x
                    } * ratio,
                    pack.manifest.interaction_bounds.y * ratio,
                    pack.manifest.interaction_bounds.width * ratio,
                    pack.manifest.interaction_bounds.height * ratio,
                    instance.pixels.as_ptr(),
                    frame.width,
                    frame.height,
                    instance.body.direction < 0.0,
                ) {
                    failed = true;
                    break;
                }
            }
            if failed {
                for slot in 0..4 {
                    termx_companion_hide(slot);
                }
                self.available = false;
                self.visible = false;
                let coordinator = app.state::<Coordinator>();
                coordinator.available.store(false, Ordering::Relaxed);
                coordinator.suspend();
                termx_companion_shutdown();
                self.instances.clear();
                let _ = app.emit(
                    "termx-companion-capabilities",
                    Capabilities {
                        desktop: false,
                        backend: "in-app",
                        reason: "Native companion surface unavailable; using the in-app habitat.",
                    },
                );
            }
        }
    }
}
/// Called only after the trusted workspace bridge installs fresh credentials.
pub(crate) fn authenticated(app: &AppHandle) {
    if let Some(state) = app.try_state::<Coordinator>() {
        state.suspended.store(false, Ordering::SeqCst);
    }
}

pub fn shutdown() {
    // SAFETY: RunEvent::Exit invokes this on the native main thread.
    unsafe {
        termx_companion_shutdown();
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn manifest() -> Manifest {
        serde_json::from_value(json!({"version":1,"id":"robot","revision":1,"logical_size":{"width":96,"height":96},"foot_anchor":{"x":48,"y":96},"interaction_bounds":{"x":0,"y":0,"width":96,"height":96},"atlases":[{"file":"atlas.png","width":96,"height":96}],"clips":{"idle":{"frames":[{"atlas":0,"x":0,"y":0,"width":96,"height":96,"duration_ms":120}],"loop":true}},"states":{"idle":"idle"}})).unwrap()
    }
    #[test]
    fn validates_bounded_packs_and_rejects_paths_and_frame_overflow() {
        let mut m = manifest();
        assert!(validate(&m, "robot", 1).is_ok());
        m.atlases[0].file = "../atlas.png".into();
        assert!(validate(&m, "robot", 1).is_err());
        m.atlases[0].file = "atlas.png".into();
        m.clips.get_mut("idle").unwrap().frames[0].x = u32::MAX;
        assert!(validate(&m, "robot", 1).is_err());
    }
    #[test]
    fn missing_state_clip_and_zero_duration_are_rejected() {
        let mut m = manifest();
        m.states.insert("error".into(), "missing".into());
        assert!(validate(&m, "robot", 1).is_err());
        m.states.clear();
        m.clips.get_mut("idle").unwrap().frames[0].duration_ms = 0;
        assert!(validate(&m, "robot", 1).is_err());
    }
    #[test]
    fn non_looping_clip_holds_last_frame_and_reduced_motion_holds_first() {
        let mut m = manifest();
        let clip = m.clips.get_mut("idle").unwrap();
        clip.repeat = false;
        let mut second = clip.frames[0].clone();
        second.x = 1;
        clip.frames.push(second);
        let pack = Pack {
            manifest: m,
            atlases: Vec::new(),
        };
        assert_eq!(
            pack.frame("idle", Duration::from_secs(100), false)
                .unwrap()
                .x,
            1
        );
        assert_eq!(
            pack.frame("idle", Duration::from_secs(100), true)
                .unwrap()
                .x,
            0
        );
    }
}
