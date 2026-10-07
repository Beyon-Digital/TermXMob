//! Native permission UI, independent of JavaScript recording-button checks.
use tauri::WebviewWindow;
#[cfg(any(target_os = "linux", windows))]
use tauri::{AppHandle, Manager};

#[cfg(any(target_os = "linux", windows))]
pub fn workspace_url(app: &AppHandle, url: &tauri::Url) -> bool {
    app.try_state::<crate::backend::Backend>()
        .and_then(|backend| backend.info())
        .map(|info| exact_workspace_origin(url, info.port))
        .unwrap_or(false)
}

#[cfg(any(target_os = "linux", windows, test))]
fn exact_workspace_origin(url: &tauri::Url, port: u16) -> bool {
    port > 0
        && url.scheme() == "http"
        && url.host_str() == Some("127.0.0.1")
        && url.port() == Some(port)
        && url.username().is_empty()
        && url.password().is_none()
}

#[cfg(target_os = "macos")]
pub fn install(window: &WebviewWindow, port: u16) -> tauri::Result<()> {
    extern "C" {
        fn termx_install_media_permission(view: *mut std::ffi::c_void, port: u16) -> bool;
    }
    let owner = window.clone();
    window.with_webview(move |webview| {
        // Tauri supplies this live WKWebView pointer on the UI thread. The
        // bridge retains only its proxy; no Rust reference escapes the call.
        let installed = unsafe { termx_install_media_permission(webview.inner(), port) };
        if !installed {
            log::error!("Could not install native media permission delegate");
            // Fail closed rather than retain Wry's unconditional Grant handler.
            let _ = owner.close();
        }
    })
}

#[cfg(target_os = "linux")]
pub fn install(window: &WebviewWindow, _port: u16) -> tauri::Result<()> {
    use gtk::prelude::*;
    use webkit2gtk::{
        PermissionRequestExt, SettingsExt, UserMediaPermissionRequestExt, WebViewExt,
    };
    let app = window.app_handle().clone();
    window.with_webview(move |webview| {
        let view = webview.inner();
        // UserMedia capture needs WebKit's media-stream feature as well as
        // the installed audio device/PipeWire or PulseAudio stack.
        if let Some(settings) = WebViewExt::settings(&view) { settings.set_enable_media_stream(true); }
        view.connect_permission_request(move |view, request| {
            let Some(media) = request.downcast_ref::<webkit2gtk::UserMediaPermissionRequest>() else { return false; };
            let current = view.uri().and_then(|uri| tauri::Url::parse(&uri).ok());
            if !media.is_for_audio_device() || media.is_for_video_device()
                || !current.as_ref().map(|url| workspace_url(&app, url)).unwrap_or(false) {
                request.deny(); return true;
            }
            let dialog = gtk::MessageDialog::builder()
                .modal(true).destroy_with_parent(true).message_type(gtk::MessageType::Question)
                .buttons(gtk::ButtonsType::YesNo)
                .text("Allow TermX to use your microphone?")
                .secondary_text("Record only when you choose Record. Review audio or its transcript before sending.")
                .build();
            if let Some(parent) = view.toplevel().and_then(|widget| widget.downcast::<gtk::Window>().ok()) {
                dialog.set_transient_for(Some(&parent));
            }
            dialog.set_default_response(gtk::ResponseType::No);
            let request = request.clone(); let view = view.clone(); let app = app.clone();
            dialog.connect_response(move |dialog, response| {
                let current = view.uri().and_then(|uri| tauri::Url::parse(&uri).ok());
                if response == gtk::ResponseType::Yes
                    && current.as_ref().map(|url| workspace_url(&app, url)).unwrap_or(false) {
                    request.allow();
                } else { request.deny(); }
                dialog.close();
            });
            dialog.show_all(); true
        });
    })
}

#[cfg(windows)]
pub fn install(window: &WebviewWindow, _port: u16) -> tauri::Result<()> {
    use webview2_com::{Microsoft::Web::WebView2::Win32::*, PermissionRequestedEventHandler};
    use windows::{core::PWSTR, Win32::System::Com::CoTaskMemFree};
    let app = window.app_handle().clone();
    let owner = window.clone();
    window.with_webview(move |webview| {
        // COM handles are supplied by Tauri on their apartment thread. The URI
        // allocation is copied then released using WebView2's documented allocator.
        let result = unsafe {
            (|| {
                let view = webview.controller().CoreWebView2()?;
                let mut token = 0;
                let permission_owner = owner.clone();
                view.add_PermissionRequested(
                    &PermissionRequestedEventHandler::create(Box::new(move |_, args| {
                        let Some(args) = args else {
                            let _ = permission_owner.close();
                            return Ok(());
                        };
                        let deny = || {
                            if let Err(error) = args.SetState(COREWEBVIEW2_PERMISSION_STATE_DENY) {
                                log::error!(
                                    "Native permission denial failed; closing workspace window"
                                );
                                let _ = permission_owner.close();
                                return Err(error);
                            }
                            Ok(())
                        };
                        let mut kind = COREWEBVIEW2_PERMISSION_KIND::default();
                        if args.PermissionKind(&mut kind).is_err() {
                            return deny();
                        }
                        if kind == COREWEBVIEW2_PERMISSION_KIND_CAMERA {
                            return deny();
                        }
                        if kind == COREWEBVIEW2_PERMISSION_KIND_MICROPHONE {
                            let mut uri = PWSTR::null();
                            if args.Uri(&mut uri).is_err() || uri.is_null() {
                                return deny();
                            }
                            let text = uri.to_string();
                            CoTaskMemFree(Some(uri.0.cast()));
                            let trusted = text
                                .ok()
                                .and_then(|text| tauri::Url::parse(&text).ok())
                                .map(|url| workspace_url(&app, &url))
                                .unwrap_or(false);
                            if !trusted {
                                return deny();
                            }
                            // Leave DEFAULT: WebView2's native consent prompt and Windows
                            // microphone privacy settings decide; never force ALLOW.
                        }
                        Ok(())
                    })),
                    &mut token,
                )
            })()
        };
        if result.is_err() {
            log::error!("Could not install native microphone permission policy");
            let _ = owner.close();
        }
    })
}

#[cfg(test)]
mod tests {
    use super::exact_workspace_origin;
    #[test]
    fn microphone_origin_is_exact_current_workspace() {
        for (url, expected) in [
            ("http://127.0.0.1:9911/?layout=chat", true),
            ("http://127.0.0.1:9912/", false),
            ("https://127.0.0.1:9911/", false),
            ("http://localhost:9911/", false),
            ("http://127.0.0.1.example.org:9911/", false),
            ("http://user@127.0.0.1:9911/", false),
        ] {
            assert_eq!(
                exact_workspace_origin(&url.parse().unwrap(), 9911),
                expected,
                "{url}"
            );
        }
        assert!(!exact_workspace_origin(
            &"http://127.0.0.1:9911/".parse().unwrap(),
            0
        ));
    }
}
