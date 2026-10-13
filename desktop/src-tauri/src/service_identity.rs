//! A service attachment must match both the controller's private profile record
//! and the live loopback process. A generic health response is never enough.
use serde_json::Value;

pub fn verified_port(status: &Value, runtime: &Value) -> Option<u16> {
    if status["installed"] != true
        || status["running"] != true
        || status["ready"] != true
        || runtime["managed"] != true
        || runtime["ready"] != true
    {
        return None;
    }
    let profile = status["profile_id"].as_str()?;
    let instance = status["instance_id"].as_str()?;
    if profile.len() != 64
        || !profile.bytes().all(|c| c.is_ascii_hexdigit())
        || !(instance.len() == 32 || instance.len() == 36)
        || !instance.bytes().all(|c| c.is_ascii_hexdigit() || c == b'-')
        || runtime["profile_id"] != profile
        || runtime["instance_id"] != instance
        || runtime["pid"] != status["pid"]
        || status["pid"].as_u64()? == 0
    {
        return None;
    }
    let port = u16::try_from(status["port"].as_u64()?)
        .ok()
        .filter(|port| *port > 0)?;
    if runtime["port"] != port {
        return None;
    }
    Some(port)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn records() -> (Value, Value) {
        let status = json!({"installed":true,"running":true,"ready":true,"pid":123,
            "port":8787,"profile_id":"a".repeat(64),"instance_id":"12345678-1234-1234-1234-123456789abc"});
        let runtime = json!({"managed":true,"ready":true,"pid":123,"port":8787,
            "profile_id":status["profile_id"],"instance_id":status["instance_id"]});
        (status, runtime)
    }
    #[test]
    fn attaches_only_to_same_ready_profile_and_process() {
        let (status, runtime) = records();
        assert_eq!(verified_port(&status, &runtime), Some(8787));
        for (field, changed) in [
            ("profile_id", json!("b".repeat(64))),
            ("instance_id", json!("87654321-1234-1234-1234-123456789abc")),
            ("pid", json!(124)),
            ("port", json!(8788)),
            ("managed", json!(false)),
            ("ready", json!(false)),
        ] {
            let mut altered = runtime.clone();
            altered[field] = changed;
            assert_eq!(verified_port(&status, &altered), None, "{field}");
        }
    }
    #[test]
    fn rejects_invalid_ports_or_unowned_records() {
        let (status, runtime) = records();
        for (field, changed) in [
            ("port", json!(0)),
            ("port", json!(65536)),
            ("pid", json!(0)),
            ("running", json!(false)),
            ("installed", json!(false)),
            ("profile_id", json!("")),
        ] {
            let mut altered = status.clone();
            altered[field] = changed;
            assert_eq!(verified_port(&altered, &runtime), None, "{field}");
        }
    }
}
