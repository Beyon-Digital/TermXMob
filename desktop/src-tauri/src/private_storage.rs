//! Native bootstrap storage must be private before credentials are read/written.
use std::{fs, io, path::Path};

pub fn directory(path: &Path) -> io::Result<()> {
    fs::create_dir_all(path)?;
    protect(path, true)
}
pub fn file(path: &Path) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        directory(parent)?;
    }
    match fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
    {
        Ok(_) => (),
        Err(error) if error.kind() == io::ErrorKind::AlreadyExists => (),
        Err(error) => return Err(error),
    }
    protect(path, false)
}
fn protect(path: &Path, directory: bool) -> io::Result<()> {
    let metadata = fs::symlink_metadata(path)?;
    if metadata.file_type().is_symlink() {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "Native storage cannot be a symbolic link",
        ));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        // SAFETY: geteuid takes no arguments and has no pointer invariants.
        if metadata.uid() != unsafe { libc::geteuid() } {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Native storage has a different owner",
            ));
        }
        fs::set_permissions(
            path,
            fs::Permissions::from_mode(if directory { 0o700 } else { 0o600 }),
        )?;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "Native storage cannot be a reparse point",
            ));
        }
        windows::protect(path, directory)?;
    }
    Ok(())
}

#[cfg(windows)]
mod windows {
    use super::*;
    use std::{ffi::c_void, os::windows::ffi::OsStrExt, ptr};
    type Handle = *mut c_void;
    type Sid = *mut c_void;
    #[repr(C)]
    struct Acl {
        revision: u8,
        reserved: u8,
        size: u16,
        count: u16,
        reserved2: u16,
    }
    #[repr(C)]
    struct AceHeader {
        kind: u8,
        flags: u8,
        size: u16,
    }
    #[repr(C)]
    struct AllowAce {
        header: AceHeader,
        mask: u32,
        sid_start: u32,
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn GetCurrentProcess() -> Handle;
        fn CloseHandle(handle: Handle) -> i32;
        fn LocalFree(memory: *mut c_void) -> *mut c_void;
    }
    #[link(name = "advapi32")]
    extern "system" {
        fn OpenProcessToken(process: Handle, access: u32, token: *mut Handle) -> i32;
        fn GetTokenInformation(
            token: Handle,
            class: i32,
            data: *mut c_void,
            length: u32,
            returned: *mut u32,
        ) -> i32;
        fn ConvertSidToStringSidW(sid: Sid, text: *mut *mut u16) -> i32;
        fn ConvertStringSecurityDescriptorToSecurityDescriptorW(
            text: *const u16,
            revision: u32,
            descriptor: *mut *mut c_void,
            size: *mut u32,
        ) -> i32;
        fn GetNamedSecurityInfoW(
            path: *const u16,
            kind: i32,
            info: u32,
            owner: *mut Sid,
            group: *mut Sid,
            dacl: *mut *mut Acl,
            sacl: *mut *mut Acl,
            descriptor: *mut *mut c_void,
        ) -> u32;
        fn SetNamedSecurityInfoW(
            path: *const u16,
            kind: i32,
            info: u32,
            owner: Sid,
            group: Sid,
            dacl: *const Acl,
            sacl: *const Acl,
        ) -> u32;
        fn GetSecurityDescriptorDacl(
            descriptor: *mut c_void,
            present: *mut i32,
            acl: *mut *mut Acl,
            defaulted: *mut i32,
        ) -> i32;
        fn GetSecurityDescriptorControl(
            descriptor: *mut c_void,
            control: *mut u16,
            revision: *mut u32,
        ) -> i32;
        fn GetAce(acl: *const Acl, index: u32, ace: *mut *mut c_void) -> i32;
        fn IsValidSid(sid: Sid) -> i32;
        fn GetLengthSid(sid: Sid) -> u32;
    }
    struct Allocation(*mut c_void);
    impl Drop for Allocation {
        fn drop(&mut self) {
            // SAFETY: nonnull allocations here are returned by Win32 LocalAlloc APIs.
            if !self.0.is_null() {
                unsafe {
                    LocalFree(self.0);
                }
            }
        }
    }
    struct Token(Handle);
    impl Drop for Token {
        fn drop(&mut self) {
            // SAFETY: OpenProcessToken transferred this valid handle to this guard.
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
    fn denied() -> io::Error {
        io::Error::new(
            io::ErrorKind::PermissionDenied,
            "Native Windows storage permissions could not be verified",
        )
    }
    fn check(value: i32) -> io::Result<()> {
        if value == 0 {
            Err(denied())
        } else {
            Ok(())
        }
    }
    fn wide(path: &Path) -> Vec<u16> {
        path.as_os_str().encode_wide().chain(Some(0)).collect()
    }
    // SAFETY: callers supply validated Windows-owned SIDs inside live descriptors.
    unsafe fn sid_string(sid: Sid) -> io::Result<String> {
        if sid.is_null() {
            return Err(denied());
        }
        let mut text = ptr::null_mut();
        check(ConvertSidToStringSidW(sid, &mut text))?;
        let allocation = Allocation(text.cast());
        let mut length = 0;
        while length < 256 && *text.add(length) != 0 {
            length += 1;
        }
        if length == 256 {
            return Err(denied());
        }
        let result =
            String::from_utf16(std::slice::from_raw_parts(text, length)).map_err(|_| denied());
        drop(allocation);
        result
    }
    fn current_sid() -> io::Result<String> {
        // SAFETY: token buffer is aligned, sized by GetTokenInformation, and kept
        // alive until its embedded SID is converted; every handle is RAII-owned.
        unsafe {
            let mut handle = ptr::null_mut();
            check(OpenProcessToken(GetCurrentProcess(), 8, &mut handle))?;
            let token = Token(handle);
            let mut length = 0;
            GetTokenInformation(token.0, 1, ptr::null_mut(), 0, &mut length);
            if length < std::mem::size_of::<Sid>() as u32 {
                return Err(denied());
            }
            let mut buffer = vec![0u64; (length as usize + 7) / 8];
            check(GetTokenInformation(
                token.0,
                1,
                buffer.as_mut_ptr().cast(),
                length,
                &mut length,
            ))?;
            sid_string(*buffer.as_ptr().cast::<Sid>())
        }
    }
    pub(super) fn protect(path: &Path, directory: bool) -> io::Result<()> {
        let actor = current_sid()?;
        let path = wide(path);
        // SAFETY: buffers are NUL-terminated and live through each Win32 call;
        // descriptors and SID/ACL pointers remain valid until their RAII guards
        // free the OS allocations. No ownership is changed, only a verified DACL.
        unsafe {
            let mut owner = ptr::null_mut();
            let mut old = ptr::null_mut();
            if GetNamedSecurityInfoW(
                path.as_ptr(),
                1,
                1,
                &mut owner,
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null_mut(),
                &mut old,
            ) != 0
            {
                return Err(denied());
            }
            let old_guard = Allocation(old);
            let owner = sid_string(owner)?;
            if owner != actor && owner != "S-1-5-32-544" {
                return Err(denied());
            }
            drop(old_guard);
            let inherit = if directory { "OICI" } else { "" };
            let sddl =
                format!("D:P(A;{inherit};FA;;;{actor})(A;{inherit};FA;;;SY)(A;{inherit};FA;;;BA)");
            let text: Vec<u16> = sddl.encode_utf16().chain(Some(0)).collect();
            let mut descriptor = ptr::null_mut();
            check(ConvertStringSecurityDescriptorToSecurityDescriptorW(
                text.as_ptr(),
                1,
                &mut descriptor,
                ptr::null_mut(),
            ))?;
            let guard = Allocation(descriptor);
            let mut present = 0;
            let mut defaulted = 0;
            let mut acl = ptr::null_mut();
            check(GetSecurityDescriptorDacl(
                descriptor,
                &mut present,
                &mut acl,
                &mut defaulted,
            ))?;
            if present == 0 || acl.is_null() {
                return Err(denied());
            }
            if SetNamedSecurityInfoW(
                path.as_ptr(),
                1,
                0x80000004,
                ptr::null_mut(),
                ptr::null_mut(),
                acl,
                ptr::null_mut(),
            ) != 0
            {
                return Err(denied());
            }
            drop(guard);
        }
        verify_wide(&path, &actor)
    }
    fn verify_wide(path: &[u16], actor: &str) -> io::Result<()> {
        // SAFETY: the returned descriptor owns all queried ACL/SID pointers;
        // GetAce validates each index. Ordinary allow ACE layout is checked
        // before its trailing SID is read and converted by Windows itself.
        unsafe {
            let mut descriptor = ptr::null_mut();
            let mut acl = ptr::null_mut();
            let mut owner = ptr::null_mut();
            if GetNamedSecurityInfoW(
                path.as_ptr(),
                1,
                5,
                &mut owner,
                ptr::null_mut(),
                &mut acl,
                ptr::null_mut(),
                &mut descriptor,
            ) != 0
            {
                return Err(denied());
            }
            let _guard = Allocation(descriptor);
            let actual_owner = sid_string(owner)?;
            if actual_owner != actor && actual_owner != "S-1-5-32-544" {
                return Err(denied());
            }
            let mut control = 0;
            let mut revision = 0;
            check(GetSecurityDescriptorControl(
                descriptor,
                &mut control,
                &mut revision,
            ))?;
            if control & 0x1000 == 0 || acl.is_null() || (*acl).count != 3 {
                return Err(denied());
            }
            let mut seen = std::collections::HashSet::new();
            for index in 0..3 {
                let mut ace = ptr::null_mut();
                check(GetAce(acl, index, &mut ace))?;
                if ace.is_null() {
                    return Err(denied());
                }
                let header = &*ace.cast::<AceHeader>();
                if header.kind != 0 || header.flags & 0x10 != 0 || header.size < 16 {
                    return Err(denied());
                }
                let entry = &*ace.cast::<AllowAce>();
                if entry.mask & 0x001f01ff != 0x001f01ff {
                    return Err(denied());
                }
                let sid = ptr::addr_of!(entry.sid_start).cast_mut().cast();
                check(IsValidSid(sid))?;
                let length = GetLengthSid(sid);
                if length < 8 || length as usize + 8 > header.size as usize {
                    return Err(denied());
                }
                let value = sid_string(sid)?;
                if value != actor && value != "S-1-5-18" && value != "S-1-5-32-544" {
                    return Err(denied());
                }
                seen.insert(value);
            }
            if seen.len() != 3 {
                return Err(denied());
            }
        }
        Ok(())
    }
    #[cfg(test)]
    pub(super) fn verify(path: &Path) -> io::Result<()> {
        verify_wide(&wide(path), &current_sid()?)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fixture() -> std::path::PathBuf {
        std::env::temp_dir().join(format!(
            "termx-native-private-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ))
    }
    #[test]
    fn native_bootstrap_is_private_before_its_first_write() {
        let root = fixture();
        directory(&root).unwrap();
        let path = root.join("desktop.json");
        file(&path).unwrap();
        #[cfg(windows)]
        {
            windows::verify(&root).unwrap();
            windows::verify(&path).unwrap();
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(
                fs::metadata(&root).unwrap().permissions().mode() & 0o777,
                0o700
            );
            assert_eq!(
                fs::metadata(&path).unwrap().permissions().mode() & 0o777,
                0o600
            );
        }
        fs::write(&path, "synthetic-bootstrap-fixture").unwrap();
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn native_storage_refuses_directory_link_without_writing_target() {
        let root = fixture();
        directory(&root).unwrap();
        let target = root.join("target");
        directory(&target).unwrap();
        let alias = root.join("alias");
        #[cfg(unix)]
        std::os::unix::fs::symlink(&target, &alias).unwrap();
        #[cfg(windows)]
        {
            let linked = std::process::Command::new("cmd.exe")
                .args(["/d", "/c", "mklink", "/J"])
                .arg(&alias)
                .arg(&target)
                .output()
                .unwrap();
            assert!(
                linked.status.success(),
                "Windows qualification could not prepare a synthetic junction"
            );
        }
        assert_eq!(
            directory(&alias).unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        assert!(!target.join("desktop.json").exists());
        #[cfg(windows)]
        fs::remove_dir(&alias).unwrap();
        #[cfg(unix)]
        fs::remove_file(&alias).unwrap();
        fs::remove_dir_all(root).unwrap();
    }
    #[cfg(unix)]
    #[test]
    fn native_storage_refuses_file_link_before_read_or_write() {
        let root = fixture();
        directory(&root).unwrap();
        let target = root.join("original.json");
        file(&target).unwrap();
        fs::write(&target, "preserved synthetic data").unwrap();
        let alias = root.join("desktop.json");
        std::os::unix::fs::symlink(&target, &alias).unwrap();
        assert_eq!(
            file(&alias).unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        assert_eq!(
            fs::read_to_string(&target).unwrap(),
            "preserved synthetic data"
        );
        fs::remove_dir_all(root).unwrap();
    }
    #[cfg(windows)]
    #[test]
    fn native_storage_refuses_foreign_owner_without_changing_it() {
        let root = fixture();
        directory(&root).unwrap();
        let path = root.join("foreign.json");
        file(&path).unwrap();
        let changed = std::process::Command::new("icacls.exe")
            .arg(&path)
            .args(["/setowner", "*S-1-5-18"])
            .output()
            .unwrap();
        assert!(
            changed.status.success(),
            "Windows qualification could not prepare a foreign-owned synthetic fixture"
        );
        assert_eq!(
            file(&path).unwrap_err().kind(),
            io::ErrorKind::PermissionDenied
        );
        assert!(fs::read(&path).unwrap().is_empty());
        fs::remove_dir_all(root).unwrap();
    }
}
