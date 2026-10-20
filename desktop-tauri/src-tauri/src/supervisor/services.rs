//! Launch recipes for the long-running services: Qdrant, the Lycosa API and
//! the optional local agent. Each returns a ready-to-spawn command; the
//! supervisor owns spawning, readiness and restarts.

use std::path::Path;
use std::time::Duration;

use super::config::Secrets;
use super::paths::{DataLayout, RuntimeLayout};
use super::postgres::{DB_NAME, DB_USER};
use super::process::{self, Env};

pub fn qdrant_command(
    runtime: &RuntimeLayout,
    data: &DataLayout,
    secrets: &Secrets,
    http_port: u16,
    grpc_port: u16,
) -> tokio::process::Command {
    let env = process::with(
        &process::base_env(),
        &[
            ("QDRANT__SERVICE__HOST", "127.0.0.1".into()),
            ("QDRANT__SERVICE__HTTP_PORT", http_port.to_string()),
            ("QDRANT__SERVICE__GRPC_PORT", grpc_port.to_string()),
            ("QDRANT__SERVICE__API_KEY", secrets.qdrant_api_key.clone()),
            (
                "QDRANT__STORAGE__STORAGE_PATH",
                data.qdrant_storage().to_string_lossy().into(),
            ),
            (
                "QDRANT__STORAGE__SNAPSHOTS_PATH",
                data.qdrant_snapshots().to_string_lossy().into(),
            ),
            ("QDRANT__TELEMETRY_DISABLED", "true".into()),
            ("QDRANT__LOG_LEVEL", "INFO".into()),
        ],
    );
    // qdrant writes a marker file into its working directory
    process::command(&runtime.qdrant(), &[], &data.root, &env)
}

/// Where the API listens: loopback by default; every interface in
/// network-controller mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Bind {
    Loopback,
    AllInterfaces,
}

impl Bind {
    pub fn host(self) -> &'static str {
        match self {
            Bind::Loopback => "127.0.0.1",
            Bind::AllInterfaces => "0.0.0.0",
        }
    }
}

pub struct BackendEnv {
    pub postgres_port: u16,
    pub qdrant_http_port: u16,
}

/// The backend's environment: everything explicit, production checks on
/// (ADR-022 fails fast on weak secrets; ours are generated), one worker so
/// no Redis is needed (ADR-028), and the source tree on PYTHONPATH because
/// the backend resolves its config files relative to its source (Docker
/// does the same through uvicorn's app dir).
pub fn backend_env(
    runtime: &RuntimeLayout,
    data: &DataLayout,
    secrets: &Secrets,
    ports: &BackendEnv,
) -> Env {
    process::with(
        &process::base_env(),
        &[
            (
                "DATABASE_URL",
                format!(
                    "postgresql+asyncpg://{DB_USER}:{}@127.0.0.1:{}/{DB_NAME}",
                    secrets.postgres_password, ports.postgres_port
                ),
            ),
            (
                "QDRANT_URL",
                format!("http://127.0.0.1:{}", ports.qdrant_http_port),
            ),
            ("QDRANT_API_KEY", secrets.qdrant_api_key.clone()),
            ("DATA_DIR", data.backend_data().to_string_lossy().into()),
            ("JWT_SECRET", secrets.jwt_secret.clone()),
            ("DEFAULT_ADMIN_EMAIL", secrets.admin_email.clone()),
            ("DEFAULT_ADMIN_PASSWORD", secrets.admin_password.clone()),
            ("ENVIRONMENT", "production".into()),
            ("WORKERS", "1".into()),
            ("LOG_LEVEL", "info".into()),
            ("PYTHONPATH", runtime.backend_dir().to_string_lossy().into()),
            ("PYTHONNOUSERSITE", "1".into()),
            ("PYTHONDONTWRITEBYTECODE", "1".into()),
            ("PYTHONUTF8", "1".into()),
            ("PYTHONIOENCODING", "utf-8".into()),
            ("PYTHONUNBUFFERED", "1".into()),
        ],
    )
}

pub fn python(runtime: &RuntimeLayout, args: &[&str], env: &Env) -> tokio::process::Command {
    process::command(&runtime.python(), args, &runtime.backend_dir(), env)
}

pub fn migrate_command(runtime: &RuntimeLayout, env: &Env) -> tokio::process::Command {
    python(runtime, &["-m", "alembic", "upgrade", "head"], env)
}

pub fn seed_command(runtime: &RuntimeLayout, env: &Env) -> tokio::process::Command {
    python(runtime, &["scripts/seed_admin.py"], env)
}

/// The backend's logging setup re-attaches uvicorn's access logger, so
/// request lines (including the event stream's `?token=`, Backlog O1) still
/// arrive; the log book redacts them before anything is stored or shown.
pub fn serve_command(
    runtime: &RuntimeLayout,
    env: &Env,
    bind: Bind,
    port: u16,
) -> tokio::process::Command {
    let port = port.to_string();
    python(
        runtime,
        &[
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            bind.host(),
            "--port",
            &port,
            "--workers",
            "1",
            "--no-access-log",
            "--no-server-header",
        ],
        env,
    )
}

/// The local agent: a Lycosa node on this computer (ADR-009/011). It talks
/// to the local API over loopback; in network-controller mode it also
/// listens on the LAN so the controller can be reached the same way remote
/// nodes are.
pub fn agent_command(
    runtime: &RuntimeLayout,
    data: &DataLayout,
    controller_port: u16,
    agent_port: u16,
    api_key: &str,
    node_name: &str,
) -> tokio::process::Command {
    let env = process::with(
        &process::base_env(),
        &[
            (
                "LYCOSA_CONTROLLER_URL",
                format!("http://127.0.0.1:{controller_port}"),
            ),
            ("LYCOSA_API_KEY", api_key.to_string()),
            ("LYCOSA_NODE_NAME", node_name.to_string()),
            ("LYCOSA_EXEC_HOST", "127.0.0.1".into()),
            ("LYCOSA_EXEC_PORT", agent_port.to_string()),
            (
                "LYCOSA_ADVERTISE_URL",
                format!("http://127.0.0.1:{agent_port}"),
            ),
            ("LYCOSA_DISCOVERY_ENABLED", "false".into()),
            ("LYCOSA_AUTO_PULL_MODEL", "false".into()),
            ("PYTHONPATH", runtime.agent_dir().to_string_lossy().into()),
            ("PYTHONNOUSERSITE", "1".into()),
            ("PYTHONDONTWRITEBYTECODE", "1".into()),
            ("PYTHONUTF8", "1".into()),
            ("PYTHONUNBUFFERED", "1".into()),
        ],
    );
    let dir = data.root.join("agent");
    let _ = std::fs::create_dir_all(&dir);
    process::command(
        &runtime.python(),
        &["-m", "lycosa_agent.main", "run"],
        &dir,
        &env,
    )
}

/// Poll an HTTP endpoint on loopback until it answers 2xx.
pub async fn wait_http_ok(
    url: &str,
    timeout: Duration,
    alive: impl Fn() -> bool,
) -> Result<(), String> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .no_proxy()
        .build()
        .map_err(|e| e.to_string())?;
    let deadline = tokio::time::Instant::now() + timeout;
    loop {
        if let Ok(response) = client.get(url).send().await {
            if response.status().is_success() {
                return Ok(());
            }
        }
        if !alive() {
            return Err("the process exited during startup".into());
        }
        if tokio::time::Instant::now() >= deadline {
            return Err(format!(
                "no healthy answer from {url} within {}s",
                timeout.as_secs()
            ));
        }
        tokio::time::sleep(Duration::from_millis(400)).await;
    }
}

pub fn node_name() -> String {
    let host = std::env::var("COMPUTERNAME")
        .or_else(|_| std::env::var("HOSTNAME"))
        .ok()
        .filter(|h| !h.trim().is_empty())
        .unwrap_or_else(|| "this-computer".into());
    host.trim().to_lowercase()
}

pub fn exists(path: &Path) -> bool {
    path.is_file()
}
