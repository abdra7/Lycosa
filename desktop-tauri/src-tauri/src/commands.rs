//! The typed IPC surface. Every command here is listed in build.rs and
//! granted in capabilities/main.json; nothing else is callable from the
//! webview. Tokens, admin passwords and file paths stay on this side.

use std::sync::Arc;
use std::time::Duration;

use serde::Serialize;
use serde_json::Value;
use tauri::ipc::Channel;
use tauri::{AppHandle, Emitter, State};
use tauri_plugin_dialog::DialogExt;
use tauri_plugin_opener::OpenerExt;

use crate::controller::events::{EventsHub, Outgoing, Sink};
use crate::controller::http::{self, ApiCall, SseEvent, Streams};
use crate::controller::profiles::{normalize_base_url, ProfileKind, ProfileStore, ProfileView};
use crate::discovery::{self, DiscoveredAgent};
use crate::error::{AppError, AppResult};
use crate::oauth::{self, Receivers};
use crate::supervisor::logs::LogLine;
use crate::supervisor::{ServiceName, Status, Supervisor};

pub const EVENTS_CHANNEL: &str = "lycosa://events";
pub const STANDALONE_CHANNEL: &str = "lycosa://standalone";
/// Uploads above this are refused before reading the file into memory; the
/// controller's own limit (20 MB) still applies below it.
const MAX_UPLOAD_BYTES: u64 = 64 * 1024 * 1024;

pub struct AppState {
    pub profiles: ProfileStore,
    pub http: reqwest::Client,
    pub events: EventsHub,
    pub streams: Streams,
    pub oauth: Receivers,
    pub supervisor: Supervisor,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppInfo {
    version: &'static str,
    os: &'static str,
    standalone_available: bool,
}

#[tauri::command]
pub fn app_info(state: State<'_, AppState>) -> AppInfo {
    AppInfo {
        version: env!("CARGO_PKG_VERSION"),
        os: std::env::consts::OS,
        standalone_available: state.supervisor.available(),
    }
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionState {
    profiles: Vec<ProfileView>,
    active_id: Option<String>,
    /// False when tokens are only remembered until the app closes.
    keychain_persistent: bool,
}

fn session_snapshot(profiles: &ProfileStore) -> SessionState {
    let (views, active_id) = profiles.views();
    SessionState {
        profiles: views,
        active_id,
        keychain_persistent: profiles.keychain_persistent(),
    }
}

#[tauri::command]
pub fn session_state(state: State<'_, AppState>) -> SessionState {
    session_snapshot(&state.profiles)
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Restored {
    session: SessionState,
    principal: Option<Value>,
    /// The controller did not answer; the token was kept for a retry.
    unreachable: bool,
}

/// Validate the active profile's token against `/me`: a revoked or expired
/// token is dropped (back to sign-in); an unreachable controller keeps it.
#[tauri::command]
pub async fn session_restore(state: State<'_, AppState>) -> AppResult<Restored> {
    let mut principal = None;
    let mut unreachable = false;
    if let Some(profile) = state.profiles.active() {
        if let Some(token) = state.profiles.token(&profile.id) {
            match http::send(
                &state.http,
                &profile.base_url,
                Some(&token),
                &get("/api/v1/me"),
            )
            .await
            {
                Ok(me) => principal = Some(me),
                Err(AppError::Api { status: 401, .. }) => state.profiles.clear_token(&profile.id),
                Err(AppError::Unreachable { .. }) => unreachable = true,
                Err(e) => return Err(e),
            }
        }
    }
    Ok(Restored {
        session: session_snapshot(&state.profiles),
        principal,
        unreachable,
    })
}

fn get(path: &str) -> ApiCall {
    ApiCall {
        method: "GET".into(),
        path: path.into(),
        query: vec![],
        body: None,
        timeout_ms: None,
        secret: false,
    }
}

async fn login(
    client: &reqwest::Client,
    base_url: &str,
    email: &str,
    password: &str,
) -> AppResult<(String, Value)> {
    let call = ApiCall {
        method: "POST".into(),
        path: "/api/v1/auth/login".into(),
        query: vec![],
        body: Some(serde_json::json!({"email": email, "password": password})),
        timeout_ms: None,
        secret: false,
    };
    let response = http::send(client, base_url, None, &call).await?;
    let token = response
        .get("access_token")
        .and_then(Value::as_str)
        .ok_or_else(|| AppError::internal("the controller returned no access token"))?
        .to_string();
    let me = http::send(client, base_url, Some(&token), &get("/api/v1/me")).await?;
    Ok((token, me))
}

/// Connection setup: probe the controller, sign in, then save the profile.
#[tauri::command]
pub async fn session_add_profile(
    state: State<'_, AppState>,
    name: String,
    base_url: String,
    email: String,
    password: String,
) -> AppResult<Value> {
    let base_url = normalize_base_url(&base_url)?;
    http::send(&state.http, &base_url, None, &get("/healthz")).await?;
    let (token, me) = login(&state.http, &base_url, email.trim(), &password).await?;
    let name = if name.trim().is_empty() {
        url::Url::parse(&base_url)
            .ok()
            .and_then(|u| u.host_str().map(str::to_string))
            .unwrap_or_else(|| base_url.clone())
    } else {
        name.trim().chars().take(80).collect()
    };
    let profile = state.profiles.add(&name, &base_url, ProfileKind::Remote)?;
    state.profiles.set_token(&profile.id, &token)?;
    Ok(me)
}

#[tauri::command]
pub async fn session_login(
    state: State<'_, AppState>,
    email: String,
    password: String,
) -> AppResult<Value> {
    let profile = state.profiles.active().ok_or(AppError::NotSignedIn)?;
    let (token, me) = login(&state.http, &profile.base_url, email.trim(), &password).await?;
    state.profiles.set_token(&profile.id, &token)?;
    Ok(me)
}

/// Revoke the session on the controller, then forget the token locally
/// whatever the controller said.
#[tauri::command]
pub async fn session_logout(app: AppHandle, state: State<'_, AppState>) -> AppResult<()> {
    if let Some(profile) = state.profiles.active() {
        if let Some(token) = state.profiles.token(&profile.id) {
            let call = ApiCall {
                method: "POST".into(),
                path: "/api/v1/auth/logout".into(),
                query: vec![],
                body: None,
                timeout_ms: None,
                secret: false,
            };
            let _ = http::send(&state.http, &profile.base_url, Some(&token), &call).await;
            state.profiles.clear_token(&profile.id);
        }
    }
    state.events.stop(&events_sink(&app));
    Ok(())
}

#[tauri::command]
pub fn session_switch(app: AppHandle, state: State<'_, AppState>, id: String) -> AppResult<()> {
    state.events.stop(&events_sink(&app));
    state.profiles.set_active(&id)
}

#[tauri::command]
pub fn session_remove_profile(
    app: AppHandle,
    state: State<'_, AppState>,
    id: String,
) -> AppResult<SessionState> {
    if state.profiles.active().is_some_and(|p| p.id == id) {
        state.events.stop(&events_sink(&app));
    }
    state.profiles.remove(&id)?;
    Ok(session_snapshot(&state.profiles))
}

fn active_session(state: &AppState) -> AppResult<(String, String)> {
    let profile = state.profiles.active().ok_or(AppError::NotSignedIn)?;
    let token = state
        .profiles
        .token(&profile.id)
        .ok_or(AppError::NotSignedIn)?;
    Ok((profile.base_url, token))
}

#[tauri::command]
pub async fn api_request(state: State<'_, AppState>, call: ApiCall) -> AppResult<Value> {
    let (base_url, token) = active_session(&state)?;
    http::send(&state.http, &base_url, Some(&token), &call).await
}

#[tauri::command]
pub async fn api_stream(
    state: State<'_, AppState>,
    call: ApiCall,
    stream_id: String,
    on_event: Channel<SseEvent>,
) -> AppResult<()> {
    let (base_url, token) = active_session(&state)?;
    let cancel = state.streams.register(&stream_id);
    let result = http::stream(&state.http, &base_url, &token, &call, cancel, |event| {
        let _ = on_event.send(event);
    })
    .await;
    state.streams.finish(&stream_id);
    result
}

#[tauri::command]
pub fn api_stream_cancel(state: State<'_, AppState>, stream_id: String) {
    state.streams.cancel(&stream_id);
}

/// Pick a file with the OS dialog and upload it. The webview never learns
/// the path and cannot name one: it can only start the picker.
#[tauri::command]
pub async fn knowledge_upload(
    app: AppHandle,
    state: State<'_, AppState>,
    collection_id: String,
) -> AppResult<Option<Value>> {
    let (base_url, token) = active_session(&state)?;
    let picked =
        tauri::async_runtime::spawn_blocking(move || app.dialog().file().blocking_pick_file())
            .await
            .map_err(|e| AppError::internal(e.to_string()))?;
    let Some(picked) = picked else {
        return Ok(None);
    };
    let path = picked
        .into_path()
        .map_err(|_| AppError::invalid("that location cannot be read as a file"))?;
    let size = std::fs::metadata(&path)?.len();
    if size > MAX_UPLOAD_BYTES {
        return Err(AppError::invalid(format!(
            "the file is {} MB; uploads are limited to {} MB",
            size / (1024 * 1024),
            MAX_UPLOAD_BYTES / (1024 * 1024)
        )));
    }
    let filename = path
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "document".into());
    let bytes = tokio::fs::read(&path).await?;
    http::upload(
        &state.http,
        &base_url,
        &token,
        &collection_id,
        &filename,
        bytes,
    )
    .await
    .map(Some)
}

pub fn events_sink(app: &AppHandle) -> Sink {
    let app = app.clone();
    Arc::new(move |out: Outgoing| {
        let _ = app.emit(EVENTS_CHANNEL, out);
    })
}

#[tauri::command]
pub fn events_start(app: AppHandle, state: State<'_, AppState>) -> AppResult<()> {
    let profile = state.profiles.active().ok_or(AppError::NotSignedIn)?;
    let token = state
        .profiles
        .token(&profile.id)
        .ok_or(AppError::NotSignedIn)?;
    state
        .events
        .start(&profile.id, &profile.base_url, &token, events_sink(&app));
    Ok(())
}

#[tauri::command]
pub fn events_stop(app: AppHandle, state: State<'_, AppState>) {
    state.events.stop(&events_sink(&app));
}

#[tauri::command]
pub async fn lan_scan() -> AppResult<Vec<DiscoveredAgent>> {
    discovery::scan(Duration::from_secs(3))
        .await
        .map_err(|e| AppError::internal(format!("LAN scan failed: {e}")))
}

#[tauri::command]
pub fn lan_ipv4() -> Option<String> {
    crate::netinfo::detect_lan_ipv4()
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct OAuthStart {
    id: String,
    callback_url: String,
}

#[tauri::command]
pub async fn oauth_loopback_start(state: State<'_, AppState>) -> AppResult<OAuthStart> {
    let (id, callback_url) = state.oauth.start().await?;
    Ok(OAuthStart { id, callback_url })
}

#[tauri::command]
pub async fn oauth_loopback_wait(
    state: State<'_, AppState>,
    id: String,
) -> AppResult<Option<String>> {
    state.oauth.wait(&id, Duration::from_secs(300)).await
}

#[tauri::command]
pub fn oauth_loopback_cancel(state: State<'_, AppState>, id: String) {
    state.oauth.cancel(&id);
}

#[tauri::command]
pub fn open_sign_in_page(app: AppHandle, url: String) -> AppResult<bool> {
    if !oauth::is_openrouter_sign_in(&url) {
        return Err(AppError::invalid(
            "only OpenRouter's sign-in page can be opened",
        ));
    }
    Ok(app.opener().open_url(url, None::<&str>).is_ok())
}

#[tauri::command]
pub fn standalone_status(state: State<'_, AppState>) -> Status {
    state.supervisor.status()
}

#[tauri::command]
pub async fn standalone_enable(state: State<'_, AppState>) -> AppResult<Status> {
    state
        .supervisor
        .set_enabled(true)
        .map_err(AppError::supervisor)?;
    state.supervisor.start().await.map_err(AppError::supervisor)
}

/// Stop the local controller and stop starting it with the app. Its data
/// stays where it is.
#[tauri::command]
pub async fn standalone_disable(state: State<'_, AppState>) -> AppResult<Status> {
    state.supervisor.stop().await;
    state
        .supervisor
        .set_enabled(false)
        .map_err(AppError::supervisor)?;
    Ok(state.supervisor.status())
}

#[tauri::command]
pub async fn standalone_start(state: State<'_, AppState>) -> AppResult<Status> {
    state.supervisor.start().await.map_err(AppError::supervisor)
}

#[tauri::command]
pub async fn standalone_stop(state: State<'_, AppState>) -> AppResult<Status> {
    state.supervisor.stop().await;
    Ok(state.supervisor.status())
}

#[tauri::command]
pub async fn standalone_restart(state: State<'_, AppState>) -> AppResult<Status> {
    state
        .supervisor
        .restart()
        .await
        .map_err(AppError::supervisor)
}

#[tauri::command]
pub fn standalone_logs(
    state: State<'_, AppState>,
    service: String,
    limit: Option<usize>,
) -> AppResult<Vec<LogLine>> {
    let known = ServiceName::ALL.iter().any(|s| s.key() == service)
        || matches!(service.as_str(), "supervisor" | "postgres-setup");
    if !known {
        return Err(AppError::invalid("unknown service"));
    }
    Ok(state.supervisor.logs(&service, limit.unwrap_or(400)))
}

#[tauri::command]
pub async fn standalone_set_network(
    state: State<'_, AppState>,
    expose_on_lan: bool,
    lan_port: u16,
) -> AppResult<Status> {
    state
        .supervisor
        .set_network(expose_on_lan, lan_port)
        .await
        .map_err(AppError::supervisor)
}

#[tauri::command]
pub async fn standalone_set_local_node(
    state: State<'_, AppState>,
    enabled: bool,
) -> AppResult<Status> {
    state
        .supervisor
        .set_local_node(enabled)
        .await
        .map_err(AppError::supervisor)
}

/// One-click sign-in to this computer's controller with its generated admin
/// account. The password goes from the supervisor straight to the login
/// request; the webview gets only the resulting principal.
#[tauri::command]
pub async fn standalone_sign_in(state: State<'_, AppState>) -> AppResult<Value> {
    let api_url = state
        .supervisor
        .status()
        .api_url
        .ok_or_else(|| AppError::supervisor("the local controller is not running"))?;
    let (email, password) = state
        .supervisor
        .admin_credentials()
        .await
        .ok_or_else(|| AppError::supervisor("the local controller has no admin account yet"))?;
    let profile = state.profiles.upsert_local(&api_url)?;
    let (token, me) = login(&state.http, &api_url, &email, &password).await?;
    state.profiles.set_token(&profile.id, &token)?;
    state.profiles.set_active(&profile.id)?;
    Ok(me)
}

#[tauri::command]
pub fn standalone_open_data_dir(app: AppHandle, state: State<'_, AppState>) -> AppResult<()> {
    let dir = state.supervisor.data_dir();
    std::fs::create_dir_all(&dir)?;
    app.opener()
        .open_path(dir.to_string_lossy(), None::<&str>)
        .map_err(|e| AppError::internal(e.to_string()))
}

#[tauri::command]
pub async fn standalone_backup_now(state: State<'_, AppState>) -> AppResult<String> {
    state
        .supervisor
        .backup_now()
        .await
        .map(|p| p.to_string_lossy().into_owned())
        .map_err(AppError::supervisor)
}
