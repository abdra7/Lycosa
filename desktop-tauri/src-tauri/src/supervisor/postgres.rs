//! The bundled PostgreSQL. `initdb` and `pg_ctl` do the work: `pg_ctl`
//! handles Windows' restricted-token requirement and waits for readiness,
//! and its `stop -m fast` is the clean shutdown on every OS.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use super::logs::LogBook;
use super::paths::{DataLayout, RuntimeLayout};
use super::process::{self, Env};

pub const DB_USER: &str = "lycosa";
pub const DB_NAME: &str = "lycosa";
const CONF_INCLUDE: &str = "include_if_exists = 'lycosa.conf'";

pub struct Postgres {
    runtime: RuntimeLayout,
    data: DataLayout,
    password: String,
    logs: Arc<LogBook>,
}

impl Postgres {
    pub fn new(
        runtime: RuntimeLayout,
        data: DataLayout,
        password: String,
        logs: Arc<LogBook>,
    ) -> Self {
        Self {
            runtime,
            data,
            password,
            logs,
        }
    }

    fn env(&self) -> Env {
        let mut extra = vec![("PGPASSWORD", self.password.clone())];
        let lib = self.runtime.postgres_lib().to_string_lossy().into_owned();
        if cfg!(target_os = "linux") {
            extra.push(("LD_LIBRARY_PATH", lib));
        } else if cfg!(target_os = "macos") {
            extra.push(("DYLD_FALLBACK_LIBRARY_PATH", lib));
        }
        process::with(&process::base_env(), &extra)
    }

    fn tool(&self, name: &str, args: &[&str]) -> tokio::process::Command {
        process::command(
            &self.runtime.postgres_bin(name),
            args,
            &self.data.root,
            &self.env(),
        )
    }

    fn pgdata(&self) -> PathBuf {
        self.data.pgdata()
    }

    fn log_file(&self) -> PathBuf {
        self.data.logs().join("postgres.log")
    }

    /// The cluster's major version (from PG_VERSION), if a cluster exists.
    pub fn cluster_major(&self) -> Option<u32> {
        let text = std::fs::read_to_string(self.pgdata().join("PG_VERSION")).ok()?;
        text.trim().parse().ok()
    }

    /// Create the cluster on first run. Returns true when it was created now.
    /// A directory that exists but holds no cluster is never wiped.
    pub async fn ensure_cluster(&self, bundled_major: u32) -> Result<bool, String> {
        let pgdata = self.pgdata();
        if let Some(major) = self.cluster_major() {
            if major != bundled_major {
                return Err(format!(
                    "the database in {} was created by PostgreSQL {major}, but this \
                     version of Lycosa ships PostgreSQL {bundled_major}. Install the \
                     Lycosa version that matches it, or move the folder aside to start \
                     fresh (see the desktop README: upgrades).",
                    pgdata.display()
                ));
            }
            return Ok(false);
        }
        if pgdata.exists()
            && std::fs::read_dir(&pgdata)
                .map(|mut d| d.next().is_some())
                .unwrap_or(false)
        {
            return Err(format!(
                "{} is not empty but holds no PostgreSQL cluster; refusing to touch it",
                pgdata.display()
            ));
        }
        // the password reaches initdb through a private file, never argv
        let pwfile = self
            .data
            .root
            .join(format!(".pw-{}", super::config::random_hex(6)));
        super::config::write_atomic(&pwfile, self.password.as_bytes(), true)
            .map_err(|e| format!("cannot write the initdb password file: {e}"))?;
        let pgdata_arg = pgdata.to_string_lossy().into_owned();
        let pw_arg = format!("--pwfile={}", pwfile.to_string_lossy());
        let result = process::run(
            self.tool(
                "initdb",
                &[
                    "-D",
                    &pgdata_arg,
                    "-U",
                    DB_USER,
                    &pw_arg,
                    "-A",
                    "scram-sha-256",
                    "-E",
                    "UTF8",
                    "--no-locale",
                    "--no-instructions",
                ],
            ),
            "postgres-setup",
            &self.logs,
            Duration::from_secs(180),
        )
        .await;
        let _ = std::fs::remove_file(&pwfile);
        let finished = result.map_err(|e| format!("initdb could not run: {e}"))?;
        if !finished.success {
            return Err(format!(
                "initdb failed (exit {:?}); see the postgres-setup log",
                finished.code
            ));
        }
        let conf = pgdata.join("postgresql.conf");
        let mut text = std::fs::read_to_string(&conf).map_err(|e| e.to_string())?;
        if !text.contains(CONF_INCLUDE) {
            text.push_str(&format!("\n# Lycosa desktop (ADR-037)\n{CONF_INCLUDE}\n"));
            std::fs::write(&conf, text).map_err(|e| e.to_string())?;
        }
        Ok(true)
    }

    /// Listen on loopback only, on this start's port; no Unix socket.
    fn write_runtime_conf(&self, port: u16) -> std::io::Result<()> {
        let conf = format!(
            "# written by the Lycosa desktop app at every start; edits are lost\n\
             listen_addresses = '127.0.0.1'\n\
             port = {port}\n\
             unix_socket_directories = ''\n\
             max_connections = 50\n\
             password_encryption = 'scram-sha-256'\n"
        );
        std::fs::write(self.pgdata().join("lycosa.conf"), conf)
    }

    pub async fn is_running(&self) -> bool {
        if !self.pgdata().join("postmaster.pid").exists() {
            return false;
        }
        let pgdata = self.pgdata().to_string_lossy().into_owned();
        match process::run(
            self.tool("pg_ctl", &["status", "-D", &pgdata]),
            "postgres-setup",
            &self.logs,
            Duration::from_secs(30),
        )
        .await
        {
            Ok(finished) => finished.success,
            Err(_) => false,
        }
    }

    /// A server left over from a crashed app run would hold the data
    /// directory; stop it before starting ours.
    pub async fn stop_leftover(&self) {
        if self.is_running().await {
            self.logs.append(
                "supervisor",
                "stopping a PostgreSQL server left from an earlier run",
            );
            self.stop().await;
        }
    }

    pub async fn start(&self, port: u16) -> Result<(), String> {
        self.write_runtime_conf(port)
            .map_err(|e| format!("cannot write lycosa.conf: {e}"))?;
        let pgdata = self.pgdata().to_string_lossy().into_owned();
        let log = self.log_file().to_string_lossy().into_owned();
        let finished = process::run_detached(
            self.tool(
                "pg_ctl",
                &["start", "-D", &pgdata, "-l", &log, "-w", "-t", "120", "-s"],
            ),
            Duration::from_secs(150),
        )
        .await
        .map_err(|e| format!("pg_ctl could not run: {e}"))?;
        if !finished.success {
            let tail = super::logs::tail_file(&self.log_file(), 5)
                .into_iter()
                .map(|line| self.logs.redact(&line))
                .collect::<Vec<_>>()
                .join(" | ");
            return Err(format!("PostgreSQL did not start: {tail}"));
        }
        Ok(())
    }

    pub async fn wait_ready(&self, port: u16, timeout: Duration) -> Result<(), String> {
        let deadline = tokio::time::Instant::now() + timeout;
        let port_arg = port.to_string();
        loop {
            let probe = process::run(
                self.tool(
                    "pg_isready",
                    &[
                        "-h",
                        "127.0.0.1",
                        "-p",
                        &port_arg,
                        "-U",
                        DB_USER,
                        "-d",
                        "postgres",
                        "-q",
                    ],
                ),
                "postgres-setup",
                &self.logs,
                Duration::from_secs(10),
            )
            .await;
            if probe.map(|f| f.success).unwrap_or(false) {
                return Ok(());
            }
            if tokio::time::Instant::now() >= deadline {
                return Err(format!(
                    "PostgreSQL did not accept connections within {}s",
                    timeout.as_secs()
                ));
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
    }

    async fn psql(&self, port: u16, sql: &str) -> Result<String, String> {
        let port_arg = port.to_string();
        let finished = process::run(
            self.tool(
                "psql",
                &[
                    "-h",
                    "127.0.0.1",
                    "-p",
                    &port_arg,
                    "-U",
                    DB_USER,
                    "-d",
                    "postgres",
                    "-v",
                    "ON_ERROR_STOP=1",
                    "-tAc",
                    sql,
                ],
            ),
            "postgres-setup",
            &self.logs,
            Duration::from_secs(60),
        )
        .await
        .map_err(|e| format!("psql could not run: {e}"))?;
        if finished.success {
            Ok(finished.stdout.trim().to_string())
        } else {
            Err(format!("psql failed (exit {:?})", finished.code))
        }
    }

    /// The `lycosa` database (initdb only creates `postgres`).
    pub async fn ensure_database(&self, port: u16) -> Result<(), String> {
        let exists = self
            .psql(
                port,
                &format!("SELECT 1 FROM pg_database WHERE datname = '{DB_NAME}'"),
            )
            .await?;
        if exists != "1" {
            self.psql(port, &format!("CREATE DATABASE {DB_NAME} OWNER {DB_USER}"))
                .await?;
        }
        Ok(())
    }

    /// True when the `lycosa` database has tables (an existing install).
    pub async fn has_schema(&self, port: u16) -> bool {
        let port_arg = port.to_string();
        let finished = process::run(
            self.tool(
                "psql",
                &[
                    "-h", "127.0.0.1", "-p", &port_arg, "-U", DB_USER, "-d", DB_NAME, "-tAc",
                    "SELECT count(*) FROM information_schema.tables WHERE table_name = 'alembic_version'",
                ],
            ),
            "postgres-setup",
            &self.logs,
            Duration::from_secs(60),
        )
        .await;
        matches!(finished, Ok(f) if f.success && f.stdout.trim() == "1")
    }

    pub async fn stop(&self) {
        let pgdata = self.pgdata().to_string_lossy().into_owned();
        for mode in ["fast", "immediate"] {
            let stopped = process::run(
                self.tool(
                    "pg_ctl",
                    &["stop", "-D", &pgdata, "-m", mode, "-w", "-t", "60", "-s"],
                ),
                "postgres-setup",
                &self.logs,
                Duration::from_secs(90),
            )
            .await
            .map(|f| f.success)
            .unwrap_or(false);
            if stopped || !self.pgdata().join("postmaster.pid").exists() {
                return;
            }
        }
    }

    /// `pg_dump` in custom format, the input `pg_restore` expects.
    pub async fn backup(&self, port: u16, dest: &Path) -> Result<(), String> {
        let port_arg = port.to_string();
        let dest_arg = dest.to_string_lossy().into_owned();
        let finished = process::run(
            self.tool(
                "pg_dump",
                &[
                    "-h",
                    "127.0.0.1",
                    "-p",
                    &port_arg,
                    "-U",
                    DB_USER,
                    "-d",
                    DB_NAME,
                    "-Fc",
                    "-f",
                    &dest_arg,
                ],
            ),
            "postgres-setup",
            &self.logs,
            Duration::from_secs(1800),
        )
        .await
        .map_err(|e| format!("pg_dump could not run: {e}"))?;
        if finished.success {
            Ok(())
        } else {
            let _ = std::fs::remove_file(dest);
            Err(format!("pg_dump failed (exit {:?})", finished.code))
        }
    }

    pub async fn accepts_tcp(&self, port: u16) -> bool {
        tokio::time::timeout(
            Duration::from_secs(2),
            tokio::net::TcpStream::connect(("127.0.0.1", port)),
        )
        .await
        .map(|r| r.is_ok())
        .unwrap_or(false)
    }
}
