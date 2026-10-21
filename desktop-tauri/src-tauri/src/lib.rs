//! Lycosa desktop (ADR-037): a Tauri shell around the React UI, plus the
//! Rust side that talks to controllers and supervises the standalone local
//! services.

pub mod commands;
pub mod controller;
pub mod discovery;
pub mod error;
pub mod netinfo;
pub mod oauth;
pub mod smoke;
pub mod supervisor;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use tauri::{Emitter, Manager, RunEvent};

use commands::AppState;
use controller::events::EventsHub;
use controller::http::{http_client, Streams};
use controller::profiles::{KeyringTokens, ProfileStore};
use oauth::Receivers;
use supervisor::{Phase, Supervisor};

/// A development override for the bundled runtime (debug builds only), so
/// `tauri dev` can use a runtime staged anywhere.
fn runtime_override() -> Option<std::path::PathBuf> {
    if cfg!(debug_assertions) {
        std::env::var_os("LYCOSA_RUNTIME_DIR").map(Into::into)
    } else {
        None
    }
}

pub fn run() {
    // WebKitGTK's DMA-BUF renderer draws a blank window where no usable GPU
    // path exists (virtual machines, some NVIDIA drivers, Xvfb). Turn it off
    // unless the user chose otherwise; set before any thread starts.
    #[cfg(target_os = "linux")]
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }
    // reqwest and tokio-tungstenite share one TLS crypto provider
    let _ = rustls::crypto::ring::default_provider().install_default();
    let context = tauri::generate_context!();
    let version = context.package_info().version.to_string();

    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--smoke-test") {
        let runtime = runtime_override().or_else(|| {
            tauri::utils::platform::resource_dir(context.package_info(), &tauri::Env::default())
                .ok()
                .map(|dir| dir.join("runtime"))
        });
        std::process::exit(smoke::run_cli(&args, runtime, &version));
    }

    let exiting = Arc::new(AtomicBool::new(false));
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .setup(move |app| {
            let paths = app.path();
            let runtime = match runtime_override() {
                Some(dir) => dir,
                None => paths.resource_dir()?.join("runtime"),
            };
            // kept short: Qdrant's files nest deep and Windows caps paths at 260
            let data = paths.app_local_data_dir()?;
            let config = paths.app_config_dir()?;
            let supervisor = Supervisor::new(runtime, data, &version);
            let profiles = ProfileStore::load(
                config.join("profiles.json"),
                Box::<KeyringTokens>::default(),
            );

            // the webview follows the supervisor; the local profile follows
            // the port the local API got
            let mut status = supervisor.subscribe();
            let handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let mut last_url = None;
                while status.changed().await.is_ok() {
                    let snapshot = status.borrow_and_update().clone();
                    if snapshot.api_url.is_some() && snapshot.api_url != last_url {
                        last_url = snapshot.api_url.clone();
                        let state = handle.state::<AppState>();
                        let has_local = state
                            .profiles
                            .views()
                            .0
                            .iter()
                            .any(|p| p.kind == controller::profiles::ProfileKind::Local);
                        if has_local {
                            let _ = state
                                .profiles
                                .upsert_local(last_url.as_deref().unwrap_or_default());
                        }
                    }
                    let _ = handle.emit(commands::STANDALONE_CHANNEL, snapshot);
                }
            });

            if supervisor.available() && supervisor.config().enabled {
                let starter = supervisor.clone();
                tauri::async_runtime::spawn(async move {
                    let _ = starter.start().await;
                });
            }

            app.manage(AppState {
                profiles,
                http: http_client(),
                events: EventsHub::default(),
                streams: Streams::default(),
                oauth: Receivers::default(),
                supervisor,
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::app_info,
            commands::session_state,
            commands::session_restore,
            commands::session_add_profile,
            commands::session_login,
            commands::session_logout,
            commands::session_switch,
            commands::session_remove_profile,
            commands::api_request,
            commands::api_stream,
            commands::api_stream_cancel,
            commands::knowledge_upload,
            commands::events_start,
            commands::events_stop,
            commands::lan_scan,
            commands::lan_ipv4,
            commands::oauth_loopback_start,
            commands::oauth_loopback_wait,
            commands::oauth_loopback_cancel,
            commands::open_sign_in_page,
            commands::standalone_status,
            commands::standalone_enable,
            commands::standalone_disable,
            commands::standalone_start,
            commands::standalone_stop,
            commands::standalone_restart,
            commands::standalone_logs,
            commands::standalone_set_network,
            commands::standalone_set_local_node,
            commands::standalone_sign_in,
            commands::standalone_open_data_dir,
            commands::standalone_backup_now,
        ])
        .build(context)
        .expect("failed to build the Lycosa desktop app");

    app.run(move |handle, event| match event {
        // closing the app stops the local services first: the API, Qdrant,
        // then PostgreSQL with a clean shutdown checkpoint
        RunEvent::ExitRequested { api, .. } => {
            let supervisor = handle.state::<AppState>().supervisor.clone();
            if matches!(supervisor.status().phase, Phase::Off | Phase::Unavailable) {
                return;
            }
            api.prevent_exit();
            if !exiting.swap(true, Ordering::SeqCst) {
                let handle = handle.clone();
                tauri::async_runtime::spawn(async move {
                    let _ = tokio::time::timeout(Duration::from_secs(90), supervisor.stop()).await;
                    handle.exit(0);
                });
            }
        }
        RunEvent::Exit => {
            let supervisor = handle.state::<AppState>().supervisor.clone();
            if !matches!(supervisor.status().phase, Phase::Off | Phase::Unavailable) {
                tauri::async_runtime::block_on(async {
                    let _ = tokio::time::timeout(Duration::from_secs(60), supervisor.stop()).await;
                });
            }
        }
        _ => {}
    });
}
