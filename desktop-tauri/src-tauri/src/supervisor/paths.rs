//! Where the bundled runtime lives (read-only, inside the installed app) and
//! where the standalone controller keeps its state (the per-user app data
//! directory, never touched by installers or updates).

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

const EXE: &str = if cfg!(windows) { ".exe" } else { "" };

/// What `stage_runtime.py` wrote into `runtime/runtime.json`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RuntimeInfo {
    pub target: String,
    pub python: String,
    pub postgres: String,
    pub qdrant: String,
    pub backend: String,
}

impl RuntimeInfo {
    /// The PostgreSQL major version this build ships (16 for "16.15.0").
    pub fn postgres_major(&self) -> Option<u32> {
        self.postgres.split('.').next()?.parse().ok()
    }
}

/// The bundled, read-only runtime (`<resources>/runtime`).
#[derive(Debug, Clone)]
pub struct RuntimeLayout {
    pub root: PathBuf,
}

/// Windows extended-length paths (`\\?\C:\...`, which Tauri's resource
/// resolver returns for an installed app) back to ordinary ones. PostgreSQL's
/// tools cannot work from a verbatim path: initdb then fails to find
/// `postgres.exe` "in the same directory" as itself.
pub fn plain(path: PathBuf) -> PathBuf {
    let text = path.to_string_lossy();
    if let Some(rest) = text.strip_prefix(r"\\?\UNC\") {
        return PathBuf::from(format!(r"\\{rest}"));
    }
    if let Some(rest) = text.strip_prefix(r"\\?\") {
        // only drive paths; other verbatim forms have no plain equivalent
        if rest.as_bytes().get(1) == Some(&b':') {
            return PathBuf::from(rest);
        }
    }
    path
}

impl RuntimeLayout {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self {
            root: plain(root.into()),
        }
    }

    pub fn python(&self) -> PathBuf {
        if cfg!(windows) {
            self.root.join("python").join("python.exe")
        } else {
            self.root.join("python").join("bin").join("python3")
        }
    }

    pub fn backend_dir(&self) -> PathBuf {
        self.root.join("backend")
    }

    pub fn agent_dir(&self) -> PathBuf {
        self.root.join("agent")
    }

    pub fn postgres_bin(&self, tool: &str) -> PathBuf {
        self.root
            .join("postgres")
            .join("bin")
            .join(format!("{tool}{EXE}"))
    }

    pub fn postgres_lib(&self) -> PathBuf {
        self.root.join("postgres").join("lib")
    }

    pub fn qdrant(&self) -> PathBuf {
        self.root.join("qdrant").join(format!("qdrant{EXE}"))
    }

    /// The runtime's manifest, or why the runtime is unusable. A dev build
    /// without a staged runtime ends up here.
    pub fn check(&self) -> Result<RuntimeInfo, String> {
        let manifest = self.root.join("runtime.json");
        let text = std::fs::read_to_string(&manifest).map_err(|_| {
            format!(
                "the standalone runtime is not bundled with this build ({} is missing)",
                manifest.display()
            )
        })?;
        let info: RuntimeInfo = serde_json::from_str(&text)
            .map_err(|e| format!("{} is not valid: {e}", manifest.display()))?;
        let required = [
            self.python(),
            self.backend_dir().join("app").join("main.py"),
            self.backend_dir().join("alembic.ini"),
            self.agent_dir().join("lycosa_agent").join("main.py"),
            self.postgres_bin("postgres"),
            self.postgres_bin("initdb"),
            self.postgres_bin("pg_ctl"),
            self.postgres_bin("psql"),
            self.postgres_bin("pg_dump"),
            self.qdrant(),
        ];
        for path in required {
            if !path.is_file() {
                return Err(format!(
                    "the standalone runtime is incomplete: {} is missing",
                    path.display()
                ));
            }
        }
        Ok(info)
    }
}

/// The standalone controller's writable state.
#[derive(Debug, Clone)]
pub struct DataLayout {
    pub root: PathBuf,
}

impl DataLayout {
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self {
            root: plain(root.into()),
        }
    }

    pub fn pgdata(&self) -> PathBuf {
        self.root.join("postgres")
    }

    /// Qdrant's storage, kept shallow: its segment files nest about 120
    /// characters deep, and Windows refuses paths over 260.
    pub fn qdrant_storage(&self) -> PathBuf {
        self.root.join("qdrant")
    }

    pub fn qdrant_snapshots(&self) -> PathBuf {
        self.root.join("qdrant-snapshots")
    }

    /// The backend's DATA_DIR: its credential encryption key (ADR-031).
    pub fn backend_data(&self) -> PathBuf {
        self.root.join("backend")
    }

    pub fn logs(&self) -> PathBuf {
        self.root.join("logs")
    }

    pub fn backups(&self) -> PathBuf {
        self.root.join("backups")
    }

    pub fn config_file(&self) -> PathBuf {
        self.root.join("standalone.json")
    }

    pub fn secrets_file(&self) -> PathBuf {
        self.root.join("secrets.json")
    }

    pub fn pids_file(&self) -> PathBuf {
        self.root.join("pids.json")
    }

    pub fn lock_file(&self) -> PathBuf {
        self.root.join("supervisor.lock")
    }

    pub fn create_dirs(&self) -> std::io::Result<()> {
        for dir in [
            self.root.clone(),
            self.qdrant_storage(),
            self.qdrant_snapshots(),
            self.backend_data(),
            self.logs(),
            self.backups(),
        ] {
            std::fs::create_dir_all(dir)?;
        }
        restrict_dir(&self.root);
        Ok(())
    }
}

/// Owner-only access to the data root on Unix (the database, the keys and
/// the logs live under it). On Windows the per-user app data directory is
/// already private to the account.
pub fn restrict_dir(path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700));
    }
    #[cfg(not(unix))]
    let _ = path;
}

/// Windows only: how close Qdrant's deepest files would come to MAX_PATH.
/// Returns a warning when the data folder leaves less than ~110 characters
/// for Qdrant's own nesting.
pub fn long_path_warning(data: &DataLayout) -> Option<String> {
    let len = data.qdrant_storage().to_string_lossy().chars().count();
    (cfg!(windows) && len > 140).then(|| {
        format!(
            "the data folder path is long ({len} characters); Windows limits paths to 260 \
             and Qdrant nests its files about 120 characters deep, so collections may fail"
        )
    })
}

/// Paths are passed as separate process arguments and never through a
/// shell, except that `pg_ctl` on Unix builds a `/bin/sh -c` line with the
/// data directory in double quotes. Refuse the characters that could break
/// out of those quotes.
pub fn shell_safe(path: &Path) -> Result<(), String> {
    let text = path.to_string_lossy();
    if cfg!(unix)
        && text
            .chars()
            .any(|c| matches!(c, '"' | '`' | '$' | '\\' | '\n'))
    {
        return Err(format!(
            "the data directory {text} contains a character PostgreSQL's tools cannot quote"
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_the_postgres_major() {
        let info = RuntimeInfo {
            target: "x".into(),
            python: "3.12.15".into(),
            postgres: "16.15.0".into(),
            qdrant: "1.18.2".into(),
            backend: "0.5.0".into(),
        };
        assert_eq!(info.postgres_major(), Some(16));
    }

    #[test]
    fn verbatim_paths_become_plain() {
        assert_eq!(
            plain(PathBuf::from(
                r"\\?\C:\Users\me\AppData\Local\Lycosa\runtime"
            )),
            PathBuf::from(r"C:\Users\me\AppData\Local\Lycosa\runtime")
        );
        assert_eq!(
            plain(PathBuf::from(r"\\?\UNC\server\share\x")),
            PathBuf::from(r"\\server\share\x")
        );
        assert_eq!(
            plain(PathBuf::from(r"\\?\Volume{abc}\x")),
            PathBuf::from(r"\\?\Volume{abc}\x")
        );
        assert_eq!(
            plain(PathBuf::from("/opt/lycosa")),
            PathBuf::from("/opt/lycosa")
        );
        assert_eq!(
            RuntimeLayout::new(r"\\?\D:\app\runtime").root,
            PathBuf::from(r"D:\app\runtime")
        );
    }

    #[test]
    fn an_unstaged_runtime_is_reported() {
        let dir = tempfile::tempdir().unwrap();
        let err = RuntimeLayout::new(dir.path()).check().unwrap_err();
        assert!(err.contains("not bundled"), "{err}");
    }

    #[test]
    fn quotes_are_refused_on_unix_only() {
        let result = shell_safe(Path::new("/home/a\"b/data"));
        assert_eq!(result.is_err(), cfg!(unix));
        assert!(shell_safe(Path::new("/home/a b/Application Support/x")).is_ok());
    }
}
