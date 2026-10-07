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
    tauri_build::build()
}
