//! The standalone-mode supervisor (ADR-037): runs PostgreSQL, Qdrant, the
//! Lycosa API and optionally a local agent from the bundled runtime, in
//! dependency order, watches them, restarts a crashed one a bounded number
//! of times, and shuts everything down cleanly. It has no Tauri dependency,
//! so the integration tests and the `--smoke-test` mode drive it directly.

pub mod config;
pub mod logs;
pub mod paths;
pub mod ports;
pub mod postgres;
pub mod process;
pub mod services;

use std::collections::HashMap;
use std::fs::File;
use std::net::Ipv4Addr;
use std::path::PathBuf;
use std::sync::{Arc, Mutex as StdMutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use tokio::sync::{watch, Mutex};
use tokio::task::JoinHandle;

use config::{Secrets, StandaloneConfig};
use logs::{LogBook, LogLine};
use paths::{DataLayout, RuntimeInfo, RuntimeLayout};
use postgres::Postgres;
use process::ServiceProcess;
use services::{BackendEnv, Bind};

const RESTART_LIMIT: usize = 3;
const RESTART_WINDOW: Duration = Duration::from_secs(600);
const KEEP_BACKUPS: usize = 5;

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum ServiceName {
    Postgres,
    Qdrant,
    Backend,
    Agent,
}

impl ServiceName {
    pub const ALL: [ServiceName; 4] = [Self::Postgres, Self::Qdrant, Self::Backend, Self::Agent];

    pub fn key(self) -> &'static str {
        match self {
            Self::Postgres => "postgres",
            Self::Qdrant => "qdrant",
            Self::Backend => "backend",
            Self::Agent => "agent",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Self::Postgres => "PostgreSQL",
            Self::Qdrant => "Qdrant",
            Self::Backend => "Lycosa API",
            Self::Agent => "Local node",
        }
    }
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ServiceState {
    Stopped,
    Starting,
    Running,
    Restarting,
    Failed,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ServiceStatus {
    pub name: ServiceName,
    pub label: &'static str,
    pub state: ServiceState,
    pub pid: Option<u32>,
    pub port: Option<u16>,
    pub restarts: u32,
    pub detail: Option<String>,
}

#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    /// No runtime bundled (a development build): remote mode only.
    Unavailable,
    Off,
    Starting,
    Running,
    Stopping,
    Error,
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Status {
    pub phase: Phase,
    /// What startup is doing right now.
    pub stage: Option<String>,
    /// Why the last start failed or what went wrong while running.
    pub message: Option<String>,
    pub services: Vec<ServiceStatus>,
    /// Loopback URL of the local API, for this app's own profile.
    pub api_url: Option<String>,
    /// Port other machines use in network-controller mode.
    pub lan_port: u16,
    pub enabled: bool,
    pub expose_on_lan: bool,
    pub local_node: bool,
    pub data_dir: String,
    pub runtime: Option<RuntimeInfo>,
    pub admin_email: String,
}

#[derive(Debug, Default, Clone, Copy)]
struct RunPorts {
    postgres: u16,
    qdrant_http: u16,
    qdrant_grpc: u16,
    api: u16,
    agent: u16,
}

#[derive(Default)]
struct Live {
    secrets: Option<Secrets>,
    ports: RunPorts,
    bind: Option<Bind>,
    procs: HashMap<ServiceName, ServiceProcess>,
    restarts: HashMap<ServiceName, Vec<Instant>>,
    postgres_up: bool,
}

struct Inner {
    runtime: RuntimeLayout,
    data: DataLayout,
    app_version: String,
    runtime_info: Result<RuntimeInfo, String>,
    logs: Arc<LogBook>,
    ops: Mutex<()>,
    live: Mutex<Live>,
    status: watch::Sender<Status>,
    lock: StdMutex<Option<File>>,
    monitor: StdMutex<Option<JoinHandle<()>>>,
}

#[derive(Clone)]
pub struct Supervisor {
    inner: Arc<Inner>,
}

impl Supervisor {
    pub fn new(runtime_root: PathBuf, data_root: PathBuf, app_version: &str) -> Self {
        let runtime = RuntimeLayout::new(runtime_root);
        let data = DataLayout::new(data_root);
        let runtime_info = runtime.check();
        let config = StandaloneConfig::load(&data.config_file());
        let logs = Arc::new(LogBook::new(data.logs(), Vec::new()));
        let status = Status {
            phase: if runtime_info.is_ok() {
                Phase::Off
            } else {
                Phase::Unavailable
            },
            stage: None,
            message: runtime_info.as_ref().err().cloned(),
            services: ServiceName::ALL
                .iter()
                .map(|&name| ServiceStatus {
                    name,
                    label: name.label(),
                    state: ServiceState::Stopped,
                    pid: None,
                    port: None,
                    restarts: 0,
                    detail: None,
                })
                .collect(),
            api_url: None,
            lan_port: config.lan_port,
            enabled: config.enabled,
            expose_on_lan: config.expose_on_lan,
            local_node: config.local_node,
            data_dir: data.root.to_string_lossy().into_owned(),
            runtime: runtime_info.as_ref().ok().cloned(),
            admin_email: config::ADMIN_EMAIL.to_string(),
        };
        let (status, _) = watch::channel(status);
        Self {
            inner: Arc::new(Inner {
                runtime,
                data,
                app_version: app_version.to_string(),
                runtime_info,
                logs,
                ops: Mutex::new(()),
                live: Mutex::new(Live::default()),
                status,
                lock: StdMutex::new(None),
                monitor: StdMutex::new(None),
            }),
        }
    }

    pub fn status(&self) -> Status {
        self.inner.status.borrow().clone()
    }

    pub fn subscribe(&self) -> watch::Receiver<Status> {
        self.inner.status.subscribe()
    }

    pub fn available(&self) -> bool {
        self.inner.runtime_info.is_ok()
    }

    pub fn config(&self) -> StandaloneConfig {
        StandaloneConfig::load(&self.inner.data.config_file())
    }

    pub fn data_dir(&self) -> PathBuf {
        self.inner.data.root.clone()
    }

    pub fn logs(&self, service: &str, limit: usize) -> Vec<LogLine> {
        self.inner.logs.tail(service, limit.min(2000))
    }

    /// The generated admin account, for this app's one-click sign-in. Never
    /// sent to the webview.
    pub async fn admin_credentials(&self) -> Option<(String, String)> {
        let live = self.inner.live.lock().await;
        live.secrets
            .clone()
            .or_else(|| Secrets::load(&self.inner.data.secrets_file()))
            .map(|s| (s.admin_email, s.admin_password))
    }

    fn update(&self, change: impl FnOnce(&mut Status)) {
        self.inner.status.send_modify(change);
    }

    fn set_stage(&self, stage: &str) {
        self.inner.logs.append("supervisor", stage);
        self.update(|s| s.stage = Some(stage.to_string()));
    }

    fn set_service(
        &self,
        name: ServiceName,
        state: ServiceState,
        pid: Option<u32>,
        port: Option<u16>,
        detail: Option<String>,
    ) {
        self.update(|s| {
            if let Some(svc) = s.services.iter_mut().find(|svc| svc.name == name) {
                svc.state = state;
                svc.pid = pid;
                if port.is_some() || state == ServiceState::Stopped {
                    svc.port = port;
                }
                svc.detail = detail;
            }
        });
    }

    fn sync_config_flags(&self, config: &StandaloneConfig) {
        self.update(|s| {
            s.enabled = config.enabled;
            s.expose_on_lan = config.expose_on_lan;
            s.lan_port = config.lan_port;
            s.local_node = config.local_node;
        });
    }

    pub fn set_enabled(&self, enabled: bool) -> Result<(), String> {
        let mut config = self.config();
        config.enabled = enabled;
        config
            .save(&self.inner.data.config_file())
            .map_err(|e| format!("cannot save settings: {e}"))?;
        self.sync_config_flags(&config);
        Ok(())
    }

    /// Network-controller mode on or off; a running controller restarts so
    /// the API rebinds.
    pub async fn set_network(&self, expose_on_lan: bool, lan_port: u16) -> Result<Status, String> {
        if lan_port < 1024 {
            return Err("choose a port from 1024 to 65535".into());
        }
        std::fs::create_dir_all(&self.inner.data.root).map_err(|e| e.to_string())?;
        let mut config = self.config();
        config.expose_on_lan = expose_on_lan;
        config.lan_port = lan_port;
        config
            .save(&self.inner.data.config_file())
            .map_err(|e| format!("cannot save settings: {e}"))?;
        self.sync_config_flags(&config);
        if matches!(self.status().phase, Phase::Running | Phase::Error) {
            self.restart().await
        } else {
            Ok(self.status())
        }
    }

    pub async fn set_local_node(&self, enabled: bool) -> Result<Status, String> {
        std::fs::create_dir_all(&self.inner.data.root).map_err(|e| e.to_string())?;
        let mut config = self.config();
        config.local_node = enabled;
        config
            .save(&self.inner.data.config_file())
            .map_err(|e| format!("cannot save settings: {e}"))?;
        self.sync_config_flags(&config);
        if self.status().phase == Phase::Running {
            let _guard = self.inner.ops.lock().await;
            if enabled {
                self.start_agent().await?;
            } else {
                self.stop_service(ServiceName::Agent).await;
            }
        }
        Ok(self.status())
    }

    /// Start every service in dependency order. On failure, whatever had
    /// started is stopped again and the reason is in the status.
    pub async fn start(&self) -> Result<Status, String> {
        let _guard = self.inner.ops.lock().await;
        if matches!(self.status().phase, Phase::Running | Phase::Starting) {
            return Ok(self.status());
        }
        let info = self.inner.runtime_info.clone()?;
        self.update(|s| {
            s.phase = Phase::Starting;
            s.message = None;
            for svc in &mut s.services {
                svc.restarts = 0;
                svc.detail = None;
            }
        });
        match self.start_all(&info).await {
            Ok(()) => {
                self.update(|s| {
                    s.phase = Phase::Running;
                    s.stage = None;
                });
                self.spawn_monitor();
                Ok(self.status())
            }
            Err(message) => {
                self.inner
                    .logs
                    .append("supervisor", &format!("start failed: {message}"));
                // without the lock another instance owns these services and
                // their pid file: touch nothing
                if self.inner.lock.lock().unwrap().is_some() {
                    self.stop_all().await;
                    self.release_lock();
                }
                self.update(|s| {
                    s.phase = Phase::Error;
                    s.stage = None;
                    s.message = Some(message.clone());
                });
                Err(message)
            }
        }
    }

    fn acquire_lock(&self) -> Result<(), String> {
        let mut slot = self.inner.lock.lock().unwrap();
        if slot.is_some() {
            return Ok(());
        }
        let file = File::options()
            .create(true)
            .truncate(false)
            .write(true)
            .open(self.inner.data.lock_file())
            .map_err(|e| format!("cannot open the supervisor lock: {e}"))?;
        file.try_lock().map_err(|_| {
            "another Lycosa app is already running the local controller from this data folder"
                .to_string()
        })?;
        *slot = Some(file);
        Ok(())
    }

    fn release_lock(&self) {
        if let Some(file) = self.inner.lock.lock().unwrap().take() {
            let _ = file.unlock();
        }
    }

    async fn start_all(&self, info: &RuntimeInfo) -> Result<(), String> {
        let inner = &self.inner;
        let data = &inner.data;
        self.set_stage("Preparing the data folder");
        data.create_dirs()
            .map_err(|e| format!("cannot create {}: {e}", data.root.display()))?;
        paths::shell_safe(&data.root)?;
        if let Some(warning) = paths::long_path_warning(data) {
            inner.logs.append("supervisor", &warning);
        }
        self.acquire_lock()?;

        let secrets = Secrets::load_or_create(&data.secrets_file())?;
        inner.logs.set_secrets(secrets.values());
        let mut config = self.config();
        self.sync_config_flags(&config);
        let postgres = Postgres::new(
            inner.runtime.clone(),
            data.clone(),
            secrets.postgres_password.clone(),
            inner.logs.clone(),
        );

        self.set_stage("Cleaning up after the previous session");
        self.kill_leftovers();
        postgres.stop_leftover().await;

        let ports = self.choose_ports(&config)?;
        let bind = if config.expose_on_lan {
            Bind::AllInterfaces
        } else {
            Bind::Loopback
        };
        {
            let mut live = inner.live.lock().await;
            live.secrets = Some(secrets.clone());
            live.ports = ports;
            live.bind = Some(bind);
            live.restarts.clear();
        }

        self.set_stage("Preparing the database");
        self.set_service(
            ServiceName::Postgres,
            ServiceState::Starting,
            None,
            Some(ports.postgres),
            None,
        );
        let major = info
            .postgres_major()
            .ok_or("the bundled runtime reports no PostgreSQL version")?;
        let fresh = postgres.ensure_cluster(major).await?;
        self.set_stage("Starting PostgreSQL");
        postgres.start(ports.postgres).await?;
        inner.live.lock().await.postgres_up = true;
        postgres
            .wait_ready(ports.postgres, Duration::from_secs(90))
            .await?;
        postgres.ensure_database(ports.postgres).await?;
        self.set_service(
            ServiceName::Postgres,
            ServiceState::Running,
            None,
            Some(ports.postgres),
            None,
        );

        self.set_stage("Starting Qdrant");
        self.spawn_qdrant(&secrets, ports).await?;

        let upgrading = config.last_app_version.as_deref() != Some(inner.app_version.as_str());
        if !fresh && upgrading && postgres.has_schema(ports.postgres).await {
            self.set_stage("Backing up the database before upgrading");
            let from = config
                .last_app_version
                .clone()
                .unwrap_or_else(|| "unknown".into());
            self.backup_with(
                &postgres,
                ports.postgres,
                &format!("before-{}-from-{from}", inner.app_version),
            )
            .await
            .map_err(|e| format!("the pre-upgrade backup failed, so nothing was migrated: {e}"))?;
        }

        let env = services::backend_env(
            &inner.runtime,
            data,
            &secrets,
            &BackendEnv {
                postgres_port: ports.postgres,
                qdrant_http_port: ports.qdrant_http,
            },
        );
        self.set_stage("Applying database migrations");
        self.set_service(
            ServiceName::Backend,
            ServiceState::Starting,
            None,
            Some(ports.api),
            None,
        );
        let migrated = process::run(
            services::migrate_command(&inner.runtime, &env),
            "backend",
            &inner.logs,
            Duration::from_secs(900),
        )
        .await
        .map_err(|e| format!("migrations could not run: {e}"))?;
        if !migrated.success {
            return Err("database migrations failed; see the backend log".into());
        }
        self.set_stage("Preparing the admin account");
        let seeded = process::run(
            services::seed_command(&inner.runtime, &env),
            "backend",
            &inner.logs,
            Duration::from_secs(300),
        )
        .await
        .map_err(|e| format!("the admin seed could not run: {e}"))?;
        if !seeded.success {
            return Err("creating the admin account failed; see the backend log".into());
        }

        self.set_stage("Starting the Lycosa API");
        self.spawn_backend(&env, bind, ports).await?;
        let api_url = format!("http://127.0.0.1:{}", ports.api);
        self.update(|s| s.api_url = Some(api_url.clone()));

        config.ports.postgres = Some(ports.postgres);
        config.ports.qdrant_http = Some(ports.qdrant_http);
        config.ports.qdrant_grpc = Some(ports.qdrant_grpc);
        if !config.expose_on_lan {
            config.ports.api = Some(ports.api);
        }
        config.last_app_version = Some(inner.app_version.clone());
        config
            .save(&data.config_file())
            .map_err(|e| format!("cannot save settings: {e}"))?;
        self.write_pids().await;

        if config.local_node {
            self.set_stage("Starting the local node");
            if let Err(e) = self.start_agent().await {
                // the controller itself is fine; the node shows as failed
                inner
                    .logs
                    .append("supervisor", &format!("local node failed: {e}"));
            }
        }
        Ok(())
    }

    fn choose_ports(&self, config: &StandaloneConfig) -> Result<RunPorts, String> {
        let pick = |preferred: Option<u16>, taken: &[u16]| {
            ports::pick(preferred, taken).map_err(|e| format!("no free local port: {e}"))
        };
        let pg = pick(config.ports.postgres, &[])?;
        let qh = pick(config.ports.qdrant_http, &[pg])?;
        let qg = pick(config.ports.qdrant_grpc, &[pg, qh])?;
        let api = if config.expose_on_lan {
            let port = config.lan_port;
            if [pg, qh, qg].contains(&port) || !ports::is_free(Ipv4Addr::UNSPECIFIED, port) {
                return Err(format!(
                    "port {port} is already in use on this computer, so the network \
                     controller cannot listen on it. Stop the program using it (for \
                     example a Docker controller) or choose another port."
                ));
            }
            port
        } else {
            pick(config.ports.api, &[pg, qh, qg])?
        };
        let agent = pick(config.ports.agent, &[pg, qh, qg, api])?;
        Ok(RunPorts {
            postgres: pg,
            qdrant_http: qh,
            qdrant_grpc: qg,
            api,
            agent,
        })
    }

    async fn spawn_qdrant(&self, secrets: &Secrets, ports: RunPorts) -> Result<(), String> {
        let inner = &self.inner;
        self.set_service(
            ServiceName::Qdrant,
            ServiceState::Starting,
            None,
            Some(ports.qdrant_http),
            None,
        );
        let cmd = services::qdrant_command(
            &inner.runtime,
            &inner.data,
            secrets,
            ports.qdrant_http,
            ports.qdrant_grpc,
        );
        let proc = process::spawn_service(cmd, "qdrant", &inner.logs)
            .map_err(|e| format!("Qdrant could not start: {e}"))?;
        let pid = proc.pid;
        inner
            .live
            .lock()
            .await
            .procs
            .insert(ServiceName::Qdrant, proc);
        services::wait_http_ok(
            &format!("http://127.0.0.1:{}/readyz", ports.qdrant_http),
            Duration::from_secs(90),
            || process::pid_alive(pid),
        )
        .await
        .map_err(|e| format!("Qdrant did not become ready: {e}"))?;
        self.set_service(
            ServiceName::Qdrant,
            ServiceState::Running,
            Some(pid),
            Some(ports.qdrant_http),
            None,
        );
        Ok(())
    }

    async fn spawn_backend(
        &self,
        env: &process::Env,
        bind: Bind,
        ports: RunPorts,
    ) -> Result<(), String> {
        let inner = &self.inner;
        let cmd = services::serve_command(&inner.runtime, env, bind, ports.api);
        let proc = process::spawn_service(cmd, "backend", &inner.logs)
            .map_err(|e| format!("the Lycosa API could not start: {e}"))?;
        let pid = proc.pid;
        inner
            .live
            .lock()
            .await
            .procs
            .insert(ServiceName::Backend, proc);
        services::wait_http_ok(
            &format!("http://127.0.0.1:{}/healthz", ports.api),
            Duration::from_secs(180),
            || process::pid_alive(pid),
        )
        .await
        .map_err(|e| format!("the Lycosa API did not become healthy: {e}"))?;
        self.set_service(
            ServiceName::Backend,
            ServiceState::Running,
            Some(pid),
            Some(ports.api),
            None,
        );
        Ok(())
    }

    /// Mint (once) a node key through the local API, then run the agent.
    async fn start_agent(&self) -> Result<(), String> {
        let inner = &self.inner;
        if inner
            .live
            .lock()
            .await
            .procs
            .contains_key(&ServiceName::Agent)
        {
            return Ok(());
        }
        let (ports, mut secrets) = {
            let live = inner.live.lock().await;
            (
                live.ports,
                live.secrets
                    .clone()
                    .ok_or("the controller is not running")?,
            )
        };
        self.set_service(
            ServiceName::Agent,
            ServiceState::Starting,
            None,
            Some(ports.agent),
            None,
        );
        let key = match secrets.node_api_key.clone() {
            Some(key) => key,
            None => {
                let key = mint_node_key(ports.api, &secrets).await.inspect_err(|e| {
                    self.set_service(
                        ServiceName::Agent,
                        ServiceState::Failed,
                        None,
                        None,
                        Some(e.clone()),
                    );
                })?;
                secrets.node_api_key = Some(key.clone());
                secrets
                    .save(&inner.data.secrets_file())
                    .map_err(|e| format!("cannot save the node key: {e}"))?;
                inner.logs.set_secrets(secrets.values());
                inner.live.lock().await.secrets = Some(secrets.clone());
                key
            }
        };
        let cmd = services::agent_command(
            &inner.runtime,
            &inner.data,
            ports.api,
            ports.agent,
            &key,
            &services::node_name(),
        );
        let proc = process::spawn_service(cmd, "agent", &inner.logs)
            .map_err(|e| format!("the local node could not start: {e}"))?;
        let pid = proc.pid;
        inner
            .live
            .lock()
            .await
            .procs
            .insert(ServiceName::Agent, proc);
        let ready = services::wait_http_ok(
            &format!("http://127.0.0.1:{}/healthz", ports.agent),
            Duration::from_secs(60),
            || process::pid_alive(pid),
        )
        .await;
        match ready {
            Ok(()) => {
                self.set_service(
                    ServiceName::Agent,
                    ServiceState::Running,
                    Some(pid),
                    Some(ports.agent),
                    None,
                );
                self.write_pids().await;
                Ok(())
            }
            Err(e) => {
                self.stop_service(ServiceName::Agent).await;
                let message = format!("the local node did not start: {e}");
                self.set_service(
                    ServiceName::Agent,
                    ServiceState::Failed,
                    None,
                    None,
                    Some(message.clone()),
                );
                Err(message)
            }
        }
    }

    async fn stop_service(&self, name: ServiceName) {
        let proc = self.inner.live.lock().await.procs.remove(&name);
        if let Some(mut proc) = proc {
            let grace = if name == ServiceName::Backend { 10 } else { 5 };
            process::terminate(&mut proc, Duration::from_secs(grace)).await;
        }
        self.set_service(name, ServiceState::Stopped, None, None, None);
    }

    async fn stop_all(&self) {
        for name in [
            ServiceName::Agent,
            ServiceName::Backend,
            ServiceName::Qdrant,
        ] {
            self.stop_service(name).await;
        }
        let (postgres_up, secrets) = {
            let live = self.inner.live.lock().await;
            (live.postgres_up, live.secrets.clone())
        };
        if postgres_up {
            if let Some(secrets) = secrets {
                Postgres::new(
                    self.inner.runtime.clone(),
                    self.inner.data.clone(),
                    secrets.postgres_password,
                    self.inner.logs.clone(),
                )
                .stop()
                .await;
            }
            self.inner.live.lock().await.postgres_up = false;
        }
        self.set_service(
            ServiceName::Postgres,
            ServiceState::Stopped,
            None,
            None,
            None,
        );
        let _ = std::fs::remove_file(self.inner.data.pids_file());
        self.update(|s| s.api_url = None);
    }

    /// Stop everything (agent, API, Qdrant, then PostgreSQL).
    pub async fn stop(&self) {
        if let Some(handle) = self.inner.monitor.lock().unwrap().take() {
            handle.abort();
        }
        let _guard = self.inner.ops.lock().await;
        if matches!(self.status().phase, Phase::Off | Phase::Unavailable) {
            return;
        }
        self.update(|s| {
            s.phase = Phase::Stopping;
            s.stage = Some("Stopping the local services".into());
        });
        self.stop_all().await;
        self.release_lock();
        self.inner.logs.append("supervisor", "all services stopped");
        self.update(|s| {
            s.phase = Phase::Off;
            s.stage = None;
            s.message = None;
        });
    }

    pub async fn restart(&self) -> Result<Status, String> {
        self.stop().await;
        self.start().await
    }

    async fn write_pids(&self) {
        let live = self.inner.live.lock().await;
        let pids: HashMap<&str, u32> = live
            .procs
            .iter()
            .map(|(name, p)| (name.key(), p.pid))
            .collect();
        if let Ok(bytes) = serde_json::to_vec(&pids) {
            let _ = config::write_atomic(&self.inner.data.pids_file(), &bytes, true);
        }
    }

    /// Processes from a run that ended without a clean stop (an app crash on
    /// macOS, where children cannot be tied to the app's lifetime).
    fn kill_leftovers(&self) {
        let Ok(text) = std::fs::read_to_string(self.inner.data.pids_file()) else {
            return;
        };
        let Ok(pids) = serde_json::from_str::<HashMap<String, u32>>(&text) else {
            return;
        };
        for (name, pid) in pids {
            let exe = if name == "qdrant" {
                self.inner.runtime.qdrant()
            } else {
                self.inner.runtime.python()
            };
            process::kill_stale(pid, &exe);
        }
        let _ = std::fs::remove_file(self.inner.data.pids_file());
    }

    /// A `pg_dump` into the backups folder; the newest five are kept.
    pub async fn backup_now(&self) -> Result<PathBuf, String> {
        let _guard = self.inner.ops.lock().await;
        if self.status().phase != Phase::Running {
            return Err("start the local controller first".into());
        }
        let (port, secrets) = {
            let live = self.inner.live.lock().await;
            (
                live.ports.postgres,
                live.secrets.clone().ok_or("not running")?,
            )
        };
        let postgres = Postgres::new(
            self.inner.runtime.clone(),
            self.inner.data.clone(),
            secrets.postgres_password,
            self.inner.logs.clone(),
        );
        self.backup_with(&postgres, port, "manual").await
    }

    async fn backup_with(
        &self,
        postgres: &Postgres,
        port: u16,
        label: &str,
    ) -> Result<PathBuf, String> {
        let dir = self.inner.data.backups();
        std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
        let stamp = chrono::Local::now().format("%Y%m%d-%H%M%S");
        let dest = dir.join(format!("lycosa-{stamp}-{label}.dump"));
        postgres.backup(port, &dest).await?;
        prune_backups(&dir, KEEP_BACKUPS);
        self.inner.logs.append(
            "supervisor",
            &format!("database backed up to {}", dest.display()),
        );
        Ok(dest)
    }

    fn spawn_monitor(&self) {
        let this = self.clone();
        let handle = tokio::spawn(async move {
            let mut tick: u64 = 0;
            loop {
                tokio::time::sleep(Duration::from_secs(2)).await;
                tick += 1;
                if this.status().phase != Phase::Running {
                    continue;
                }
                this.check_processes().await;
                if tick.is_multiple_of(3) {
                    this.check_postgres().await;
                }
            }
        });
        if let Some(old) = self.inner.monitor.lock().unwrap().replace(handle) {
            old.abort();
        }
    }

    async fn check_processes(&self) {
        let exited: Vec<(ServiceName, String)> = {
            let mut live = self.inner.live.lock().await;
            let mut out = Vec::new();
            for (name, proc) in live.procs.iter_mut() {
                if let Ok(Some(status)) = proc.child.try_wait() {
                    out.push((*name, format!("exited unexpectedly ({status})")));
                }
            }
            for (name, _) in &out {
                live.procs.remove(name);
            }
            out
        };
        for (name, why) in exited {
            self.inner
                .logs
                .append("supervisor", &format!("{} {why}", name.label()));
            self.recover(name, why).await;
        }
    }

    async fn check_postgres(&self) {
        let (port, secrets) = {
            let live = self.inner.live.lock().await;
            (live.ports.postgres, live.secrets.clone())
        };
        let Some(secrets) = secrets else { return };
        let postgres = Postgres::new(
            self.inner.runtime.clone(),
            self.inner.data.clone(),
            secrets.postgres_password,
            self.inner.logs.clone(),
        );
        if postgres.accepts_tcp(port).await {
            return;
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
        if postgres.accepts_tcp(port).await {
            return;
        }
        self.inner
            .logs
            .append("supervisor", "PostgreSQL stopped answering");
        self.recover(ServiceName::Postgres, "stopped answering".into())
            .await;
    }

    /// Restart a crashed service unless it already crashed RESTART_LIMIT
    /// times within RESTART_WINDOW; then it is marked failed and the reason
    /// shown.
    async fn recover(&self, name: ServiceName, why: String) {
        let _guard = self.inner.ops.lock().await;
        if self.status().phase != Phase::Running {
            return;
        }
        let allowed = {
            let mut live = self.inner.live.lock().await;
            let history = live.restarts.entry(name).or_default();
            history.retain(|t| t.elapsed() < RESTART_WINDOW);
            history.push(Instant::now());
            history.len() <= RESTART_LIMIT
        };
        let attempts = self
            .inner
            .live
            .lock()
            .await
            .restarts
            .get(&name)
            .map_or(0, |h| h.len());
        self.update(|s| {
            if let Some(svc) = s.services.iter_mut().find(|svc| svc.name == name) {
                svc.restarts += 1;
            }
        });
        if !allowed {
            let message = format!(
                "{} keeps stopping ({why}); it was restarted {RESTART_LIMIT} times in 10 minutes. \
                 See its log, then press Restart.",
                name.label()
            );
            self.set_service(name, ServiceState::Failed, None, None, Some(why));
            self.update(|s| {
                s.phase = Phase::Error;
                s.message = Some(message);
            });
            return;
        }
        self.set_service(name, ServiceState::Restarting, None, None, Some(why));
        tokio::time::sleep(Duration::from_secs(attempts as u64)).await;
        let result = self.restart_service(name).await;
        if let Err(e) = result {
            self.inner.logs.append(
                "supervisor",
                &format!("restarting {} failed: {e}", name.label()),
            );
            self.set_service(name, ServiceState::Failed, None, None, Some(e.clone()));
            self.update(|s| {
                s.phase = Phase::Error;
                s.message = Some(format!("{} could not be restarted: {e}", name.label()));
            });
        }
        self.write_pids().await;
    }

    async fn restart_service(&self, name: ServiceName) -> Result<(), String> {
        let (ports, secrets, bind) = {
            let live = self.inner.live.lock().await;
            (
                live.ports,
                live.secrets.clone().ok_or("not running")?,
                live.bind.unwrap_or(Bind::Loopback),
            )
        };
        let env = services::backend_env(
            &self.inner.runtime,
            &self.inner.data,
            &secrets,
            &BackendEnv {
                postgres_port: ports.postgres,
                qdrant_http_port: ports.qdrant_http,
            },
        );
        match name {
            ServiceName::Qdrant => self.spawn_qdrant(&secrets, ports).await,
            ServiceName::Backend => self.spawn_backend(&env, bind, ports).await,
            ServiceName::Agent => self.start_agent().await,
            ServiceName::Postgres => {
                // the API's connection pool would hold dead connections
                self.stop_service(ServiceName::Backend).await;
                let postgres = Postgres::new(
                    self.inner.runtime.clone(),
                    self.inner.data.clone(),
                    secrets.postgres_password.clone(),
                    self.inner.logs.clone(),
                );
                postgres.stop_leftover().await;
                postgres.start(ports.postgres).await?;
                postgres
                    .wait_ready(ports.postgres, Duration::from_secs(90))
                    .await?;
                self.set_service(
                    ServiceName::Postgres,
                    ServiceState::Running,
                    None,
                    Some(ports.postgres),
                    None,
                );
                self.spawn_backend(&env, bind, ports).await
            }
        }
    }
}

async fn mint_node_key(api_port: u16, secrets: &Secrets) -> Result<String, String> {
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| e.to_string())?;
    let base = format!("http://127.0.0.1:{api_port}/api/v1");
    let login: serde_json::Value = client
        .post(format!("{base}/auth/login"))
        .json(
            &serde_json::json!({"email": secrets.admin_email, "password": secrets.admin_password}),
        )
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|_| "the local admin account could not sign in to mint a node key".to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())?;
    let token = login["access_token"].as_str().ok_or("no access token")?;
    let minted: serde_json::Value = client
        .post(format!("{base}/admin/api-keys"))
        .bearer_auth(token)
        .json(&serde_json::json!({"name": format!("local node ({})", services::node_name()), "role": "node"}))
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| format!("minting the node key failed: {e}"))?
        .json()
        .await
        .map_err(|e| e.to_string())?;
    let _ = client
        .post(format!("{base}/auth/logout"))
        .bearer_auth(token)
        .send()
        .await;
    minted["api_key"]
        .as_str()
        .map(str::to_string)
        .ok_or_else(|| "the controller returned no node key".into())
}

fn prune_backups(dir: &std::path::Path, keep: usize) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let mut dumps: Vec<PathBuf> = entries
        .filter_map(|e| e.ok().map(|e| e.path()))
        .filter(|p| p.extension().is_some_and(|x| x == "dump"))
        .collect();
    // names start with a sortable timestamp
    dumps.sort();
    let excess = dumps.len().saturating_sub(keep);
    for old in dumps.into_iter().take(excess) {
        let _ = std::fs::remove_file(old);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn without_a_runtime_the_supervisor_is_unavailable() {
        let rt = tempfile::tempdir().unwrap();
        let data = tempfile::tempdir().unwrap();
        let sup = Supervisor::new(rt.path().into(), data.path().into(), "0.5.0");
        let status = sup.status();
        assert_eq!(status.phase, Phase::Unavailable);
        assert!(status.message.unwrap().contains("not bundled"));
        assert!(!sup.available());
    }

    #[test]
    fn old_backups_are_pruned_oldest_first() {
        let dir = tempfile::tempdir().unwrap();
        for i in 0..7 {
            std::fs::write(
                dir.path().join(format!("lycosa-2026010{i}-000000-x.dump")),
                b"x",
            )
            .unwrap();
        }
        std::fs::write(dir.path().join("notes.txt"), b"keep").unwrap();
        prune_backups(dir.path(), 5);
        let mut left: Vec<String> = std::fs::read_dir(dir.path())
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .collect();
        left.sort();
        assert_eq!(left.len(), 6);
        assert!(left.contains(&"notes.txt".to_string()));
        assert!(!left.contains(&"lycosa-20260100-000000-x.dump".to_string()));
        assert!(!left.contains(&"lycosa-20260101-000000-x.dump".to_string()));
    }

    #[tokio::test]
    async fn network_mode_settings_persist() {
        let rt = tempfile::tempdir().unwrap();
        let data = tempfile::tempdir().unwrap();
        let sup = Supervisor::new(rt.path().into(), data.path().into(), "0.5.0");
        assert!(sup.set_network(true, 80).await.is_err());
        let status = sup.set_network(true, 18123).await.unwrap();
        assert!(status.expose_on_lan);
        assert_eq!(status.lan_port, 18123);
        assert!(sup.config().expose_on_lan);
    }
}
