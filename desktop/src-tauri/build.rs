fn main() {
    #[cfg(target_os = "macos")]
    {
        cc::Build::new()
            .file("macos/capture_bridge.m")
            .file("macos/media_permission.m")
            .flag("-fobjc-arc")
            .flag("-mmacosx-version-min=13.0")
            .warnings(true)
            .compile("termx_capture_bridge");
        println!("cargo:rustc-link-lib=framework=Foundation");
        println!("cargo:rustc-link-lib=framework=WebKit");
        println!("cargo:rustc-link-lib=framework=CoreGraphics");
        println!("cargo:rustc-link-lib=framework=CoreMedia");
        println!("cargo:rustc-link-lib=framework=CoreVideo");
        println!("cargo:rustc-link-lib=framework=CoreImage");
        println!("cargo:rustc-link-lib=framework=ImageIO");
        println!("cargo:rustc-link-lib=framework=UniformTypeIdentifiers");
        println!("cargo:rustc-link-lib=framework=ApplicationServices");
        println!("cargo:rustc-link-lib=framework=ScreenCaptureKit");
        println!("cargo:rustc-link-lib=framework=QuartzCore");
        println!("cargo:rerun-if-changed=macos/capture_bridge.m");
        println!("cargo:rerun-if-changed=macos/capture_bridge.h");
        println!("cargo:rerun-if-changed=macos/media_permission.m");
    }
    // The served loopback workspace is a remote URL to Tauri. Its guarded
    // application commands therefore need explicit ACL entries as well as
    // the invoke handlers; core:default alone only grants core plugin APIs.
    let manifest = tauri_build::AppManifest::new().commands(&[
        "workspace_login",
        "workspace_resume_sso",
        "workspace_request",
        "workspace_logout",
        "workspace_lock_state",
        "workspace_unlock",
        "workspace_unlock_oidc",
        "workspace_detach",
        "workspace_redock",
        "workspace_redock_accept",
        "workspace_redock_commit",
        "workspace_redock_cancel",
        "workspace_binary",
    ]);
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(manifest))
        .expect("Failed to build the guarded workspace command ACL")
}
