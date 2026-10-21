/// Every command the frontend may call. Declaring them in the app manifest
/// means none is reachable until a capability grants its `allow-*`
/// permission (capabilities/main.json), instead of all app commands being
/// open by default.
const COMMANDS: &[&str] = &[
    "app_info",
    "session_state",
    "session_restore",
    "session_add_profile",
    "session_login",
    "session_logout",
    "session_switch",
    "session_remove_profile",
    "api_request",
    "api_stream",
    "api_stream_cancel",
    "knowledge_upload",
    "events_start",
    "events_stop",
    "lan_scan",
    "lan_ipv4",
    "oauth_loopback_start",
    "oauth_loopback_wait",
    "oauth_loopback_cancel",
    "open_sign_in_page",
    "standalone_status",
    "standalone_enable",
    "standalone_disable",
    "standalone_start",
    "standalone_stop",
    "standalone_restart",
    "standalone_logs",
    "standalone_set_network",
    "standalone_set_local_node",
    "standalone_sign_in",
    "standalone_open_data_dir",
    "standalone_backup_now",
];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("failed to run the Tauri build script");
}
