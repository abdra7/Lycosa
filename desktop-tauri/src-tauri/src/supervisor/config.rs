//! The standalone controller's settings (`standalone.json`) and generated
//! secrets (`secrets.json`), both under the data root.

use std::io::Write;
use std::path::Path;

use serde::{Deserialize, Serialize};

pub const ADMIN_EMAIL: &str = "admin@lycosa.local";
pub const DEFAULT_LAN_PORT: u16 = 8000;

/// Ports used by the last successful start; reused when still free so the
/// local profile's URL stays stable across launches.
#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Ports {
    pub postgres: Option<u16>,
    pub qdrant_http: Option<u16>,
    pub qdrant_grpc: Option<u16>,
    pub api: Option<u16>,
    pub agent: Option<u16>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", default)]
pub struct StandaloneConfig {
    /// The user chose to run the controller on this computer; the services
    /// start with the app.
    pub enabled: bool,
    /// Network-controller mode: the API listens on every interface at
    /// `lan_port` so agents on other machines can register. Off = loopback
    /// only on a free port.
    pub expose_on_lan: bool,
    pub lan_port: u16,
    /// Also run a Lycosa node (the agent) on this computer.
    pub local_node: bool,
    pub ports: Ports,
    /// App version that last started these services; a change triggers a
    /// database backup before migrations run.
    pub last_app_version: Option<String>,
}

impl Default for StandaloneConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            expose_on_lan: false,
            lan_port: DEFAULT_LAN_PORT,
            local_node: false,
            ports: Ports::default(),
            last_app_version: None,
        }
    }
}

impl StandaloneConfig {
    /// A missing file is a first run. A corrupt one is set aside (never
    /// silently overwritten) and defaults are used.
    pub fn load(path: &Path) -> Self {
        match std::fs::read_to_string(path) {
            Ok(text) => serde_json::from_str(&text).unwrap_or_else(|_| {
                let aside = path.with_extension("json.corrupt");
                let _ = std::fs::rename(path, &aside);
                Self::default()
            }),
            Err(_) => Self::default(),
        }
    }

    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        write_atomic(path, &serde_json::to_vec_pretty(self)?, false)
    }
}

/// Secrets the supervisor generates once and hands to the services through
/// their environment. The backend's own LLM credential key is separate: the
/// backend creates it in its DATA_DIR (ADR-031).
#[derive(Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Secrets {
    pub postgres_password: String,
    pub qdrant_api_key: String,
    pub jwt_secret: String,
    pub admin_email: String,
    pub admin_password: String,
    /// The node API key of the local agent, minted on first use.
    #[serde(default)]
    pub node_api_key: Option<String>,
}

impl std::fmt::Debug for Secrets {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Secrets { .. }")
    }
}

impl Secrets {
    pub fn generate() -> Self {
        Self {
            postgres_password: random_hex(24),
            qdrant_api_key: random_hex(32),
            jwt_secret: random_hex(32),
            admin_email: ADMIN_EMAIL.to_string(),
            // the admin types this only if the one-click sign-in is lost
            admin_password: random_hex(12),
            node_api_key: None,
        }
    }

    /// Existing secrets, or new ones persisted before first use. An
    /// unreadable file is an error: regenerating would lock the user out of
    /// their own database.
    pub fn load_or_create(path: &Path) -> Result<Self, String> {
        match std::fs::read_to_string(path) {
            Ok(text) => serde_json::from_str(&text).map_err(|e| {
                format!(
                    "{} is damaged ({e}); restore it from a backup of the data folder",
                    path.display()
                )
            }),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                let secrets = Self::generate();
                secrets.save(path).map_err(|e| e.to_string())?;
                Ok(secrets)
            }
            Err(e) => Err(format!("cannot read {}: {e}", path.display())),
        }
    }

    pub fn save(&self, path: &Path) -> std::io::Result<()> {
        write_atomic(path, &serde_json::to_vec_pretty(self)?, true)
    }

    /// Read existing secrets without creating any.
    pub fn load(path: &Path) -> Option<Self> {
        serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
    }

    /// Every value the log redactor must never let through.
    pub fn values(&self) -> Vec<String> {
        let mut values = vec![
            self.postgres_password.clone(),
            self.qdrant_api_key.clone(),
            self.jwt_secret.clone(),
            self.admin_password.clone(),
        ];
        values.extend(self.node_api_key.clone());
        values
    }
}

pub fn random_hex(bytes: usize) -> String {
    let mut buf = vec![0u8; bytes];
    getrandom::fill(&mut buf).expect("the OS random number generator failed");
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// Write to a sibling temp file, then rename over the target, so a crash
/// mid-write never leaves a truncated settings or secrets file.
pub fn write_atomic(path: &Path, bytes: &[u8], private: bool) -> std::io::Result<()> {
    let tmp = path.with_extension(format!("tmp-{}", random_hex(4)));
    {
        let mut options = std::fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        if private {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        #[cfg(not(unix))]
        let _ = private;
        let mut file = options.open(&tmp)?;
        file.write_all(bytes)?;
        file.sync_all()?;
    }
    std::fs::rename(&tmp, path).inspect_err(|_| {
        let _ = std::fs::remove_file(&tmp);
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_round_trips_and_defaults_missing_fields() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("standalone.json");
        assert_eq!(StandaloneConfig::load(&path), StandaloneConfig::default());
        let mut config = StandaloneConfig {
            enabled: true,
            ..Default::default()
        };
        config.ports.api = Some(18000);
        config.save(&path).unwrap();
        assert_eq!(StandaloneConfig::load(&path), config);

        std::fs::write(&path, r#"{"enabled": true}"#).unwrap();
        let partial = StandaloneConfig::load(&path);
        assert!(partial.enabled);
        assert_eq!(partial.lan_port, DEFAULT_LAN_PORT);
    }

    #[test]
    fn a_corrupt_config_is_set_aside() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("standalone.json");
        std::fs::write(&path, "{not json").unwrap();
        assert_eq!(StandaloneConfig::load(&path), StandaloneConfig::default());
        assert!(dir.path().join("standalone.json.corrupt").is_file());
    }

    #[test]
    fn secrets_are_generated_once_and_kept() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("secrets.json");
        let first = Secrets::load_or_create(&path).unwrap();
        let second = Secrets::load_or_create(&path).unwrap();
        assert!(first == second);
        assert_eq!(first.jwt_secret.len(), 64);
        assert_eq!(first.admin_email, ADMIN_EMAIL);
        assert_eq!(format!("{first:?}"), "Secrets { .. }");
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&path).unwrap().permissions().mode();
            assert_eq!(mode & 0o777, 0o600);
        }
    }

    #[test]
    fn damaged_secrets_are_not_regenerated() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("secrets.json");
        std::fs::write(&path, "garbage").unwrap();
        let err = Secrets::load_or_create(&path).unwrap_err();
        assert!(err.contains("damaged"), "{err}");
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "garbage");
    }
}
