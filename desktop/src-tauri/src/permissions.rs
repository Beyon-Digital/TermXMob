#[derive(Clone, Copy, Debug)]
pub struct PermissionStatus {
    pub screen_recording: bool,
    pub accessibility: bool,
}

impl PermissionStatus {
    pub fn screen_recording_label(&self) -> &'static str {
        if self.screen_recording {
            "granted"
        } else {
            "denied"
        }
    }

    pub fn accessibility_label(&self) -> &'static str {
        if self.accessibility {
            "granted"
        } else {
            "denied"
        }
    }
}

#[cfg(target_os = "macos")]
pub fn status() -> PermissionStatus {
    imp::status()
}

#[cfg(not(target_os = "macos"))]
pub fn status() -> PermissionStatus {
    PermissionStatus {
        screen_recording: true,
        accessibility: true,
    }
}

#[cfg(target_os = "macos")]
pub fn request_screen_recording() -> bool {
    imp::request_screen_recording()
}

#[cfg(not(target_os = "macos"))]
pub fn request_screen_recording() -> bool {
    true
}

#[cfg(target_os = "macos")]
pub fn request_accessibility() -> bool {
    imp::request_accessibility()
}

#[cfg(not(target_os = "macos"))]
pub fn request_accessibility() -> bool {
    true
}

#[cfg(target_os = "macos")]
mod imp {
    use core_foundation::base::TCFType;
    use core_foundation::boolean::CFBoolean;
    use core_foundation::dictionary::CFDictionary;
    use core_foundation::string::CFString;
    use std::ffi::c_void;

    #[link(name = "CoreGraphics", kind = "framework")]
    extern "C" {
        fn CGPreflightScreenCaptureAccess() -> bool;
        fn CGRequestScreenCaptureAccess() -> bool;
    }

    #[link(name = "ApplicationServices", kind = "framework")]
    extern "C" {
        fn AXIsProcessTrusted() -> bool;
        fn AXIsProcessTrustedWithOptions(options: *const c_void) -> bool;
    }

    pub fn status() -> super::PermissionStatus {
        super::PermissionStatus {
            screen_recording: unsafe { CGPreflightScreenCaptureAccess() },
            accessibility: unsafe { AXIsProcessTrusted() },
        }
    }

    pub fn request_screen_recording() -> bool {
        unsafe { CGRequestScreenCaptureAccess() }
    }

    pub fn request_accessibility() -> bool {
        let key = CFString::from_static_string("AXTrustedCheckOptionPrompt");
        let value = CFBoolean::true_value();
        let dictionary = CFDictionary::from_CFType_pairs(&[(key.as_CFType(), value.as_CFType())]);
        unsafe { AXIsProcessTrustedWithOptions(dictionary.as_concrete_TypeRef() as *const c_void) }
    }
}
