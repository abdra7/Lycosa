//! Saved controller connections (ADR-006/015). Names and URLs live in
//! `profiles.json` in the app config dir; each profile's session token lives
//! in the OS keychain (Windows Credential Manager, macOS Keychain, Secret
//! Service on Linux) and never reaches the webview. Without a working
//! keychain, tokens are kept in memory for this run only and the UI says so.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

use crate::error::{AppError, AppResult};
use crate::supervisor::config::{random_hex, write_atomic};

const KEYRING_SERVICE: &str = "com.lycosa.desktop";

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum ProfileKind {
    /// A controller elsewhere (or a Docker install on this machine).
    #[default]
    Remote,
    /// The controller this app runs itself (standalone mode).
    Local,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct Profile {
    pub id: String,
    pub name: String,
    pub base_url: String,
    #[serde(default)]
    pub kind: ProfileKind,
}

/// What the webview sees: no token, only whether one is held.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct ProfileView {
    pub id: String,
    pub name: String,
    pub base_url: String,
    pub kind: ProfileKind,
    pub signed_in: bool,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
struct ProfilesFile {
    profiles: Vec<Profile>,
    active_id: Option<String>,
}

pub trait TokenStore: Send + Sync {
    fn get(&self, profile_id: &str) -> Option<String>;
    fn set(&self, profile_id: &str, token: &str) -> AppResult<()>;
    fn delete(&self, profile_id: &str);
    /// False when tokens only last for this run.
    fn persistent(&self) -> bool;
}

#[derive(Default)]
pub struct MemoryTokens(Mutex<HashMap<String, String>>);

impl TokenStore for MemoryTokens {
    fn get(&self, profile_id: &str) -> Option<String> {
        self.0.lock().unwrap().get(profile_id).cloned()
    }
    fn set(&self, profile_id: &str, token: &str) -> AppResult<()> {
        self.0
            .lock()
            .unwrap()
            .insert(profile_id.to_string(), token.to_string());
        Ok(())
    }
    fn delete(&self, profile_id: &str) {
        self.0.lock().unwrap().remove(profile_id);
    }
    fn persistent(&self) -> bool {
        false
    }
}

/// The OS keychain, falling back to memory for the rest of the run the
/// first time the platform store is unusable (no Secret Service on a
/// minimal Linux desktop, for example). Never falls back to a file.
pub struct KeyringTokens {
    memory: MemoryTokens,
    broken: AtomicBool,
}

impl Default for KeyringTokens {
    fn default() -> Self {
        Self {
            memory: MemoryTokens::default(),
            broken: AtomicBool::new(false),
        }
    }
}

impl KeyringTokens {
    fn entry(profile_id: &str) -> Option<keyring::Entry> {
        keyring::Entry::new(KEYRING_SERVICE, &format!("profile:{profile_id}")).ok()
    }

    fn mark_broken(&self, error: &keyring::Error) -> bool {
        let unusable = matches!(
            error,
            keyring::Error::PlatformFailure(_) | keyring::Error::NoStorageAccess(_)
        );
        if unusable {
            self.broken.store(true, Ordering::SeqCst);
        }
        unusable
    }
}

impl TokenStore for KeyringTokens {
    fn get(&self, profile_id: &str) -> Option<String> {
        if self.broken.load(Ordering::SeqCst) {
            return self.memory.get(profile_id);
        }
        let entry = Self::entry(profile_id)?;
        match entry.get_password() {
            Ok(token) => Some(token),
            Err(keyring::Error::NoEntry) => None,
            Err(e) => {
                self.mark_broken(&e);
                self.memory.get(profile_id)
            }
        }
    }

    fn set(&self, profile_id: &str, token: &str) -> AppResult<()> {
        if !self.broken.load(Ordering::SeqCst) {
            if let Some(entry) = Self::entry(profile_id) {
                match entry.set_password(token) {
                    Ok(()) => return Ok(()),
                    Err(e) if self.mark_broken(&e) => {}
                    Err(e) => {
                        return Err(AppError::Keychain {
                            message: format!("the OS keychain refused the token: {e}"),
                        })
                    }
                }
            }
        }
        self.memory.set(profile_id, token)
    }

    fn delete(&self, profile_id: &str) {
        self.memory.delete(profile_id);
        if let Some(entry) = Self::entry(profile_id) {
            let _ = entry.delete_credential();
        }
    }

    fn persistent(&self) -> bool {
        !self.broken.load(Ordering::SeqCst)
    }
}

pub struct ProfileStore {
    path: PathBuf,
    tokens: Box<dyn TokenStore>,
    state: Mutex<ProfilesFile>,
}

impl ProfileStore {
    pub fn load(path: PathBuf, tokens: Box<dyn TokenStore>) -> Self {
        let state = std::fs::read_to_string(&path)
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default();
        Self {
            path,
            tokens,
            state: Mutex::new(state),
        }
    }

    fn save(&self, state: &ProfilesFile) -> AppResult<()> {
        if let Some(dir) = self.path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let bytes =
            serde_json::to_vec_pretty(state).map_err(|e| AppError::internal(e.to_string()))?;
        write_atomic(&self.path, &bytes, false).map_err(AppError::from)
    }

    pub fn keychain_persistent(&self) -> bool {
        self.tokens.persistent()
    }

    pub fn views(&self) -> (Vec<ProfileView>, Option<String>) {
        let state = self.state.lock().unwrap();
        let views = state
            .profiles
            .iter()
            .map(|p| ProfileView {
                id: p.id.clone(),
                name: p.name.clone(),
                base_url: p.base_url.clone(),
                kind: p.kind,
                signed_in: self.tokens.get(&p.id).is_some(),
            })
            .collect();
        (views, active_id(&state))
    }

    /// The active profile: the saved choice, else the first one.
    pub fn active(&self) -> Option<Profile> {
        let state = self.state.lock().unwrap();
        let id = active_id(&state)?;
        state.profiles.iter().find(|p| p.id == id).cloned()
    }

    pub fn get(&self, id: &str) -> Option<Profile> {
        self.state
            .lock()
            .unwrap()
            .profiles
            .iter()
            .find(|p| p.id == id)
            .cloned()
    }

    pub fn token(&self, id: &str) -> Option<String> {
        self.tokens.get(id)
    }

    pub fn set_token(&self, id: &str, token: &str) -> AppResult<()> {
        self.tokens.set(id, token)
    }

    pub fn clear_token(&self, id: &str) {
        self.tokens.delete(id);
    }

    pub fn add(&self, name: &str, base_url: &str, kind: ProfileKind) -> AppResult<Profile> {
        let mut state = self.state.lock().unwrap();
        let profile = Profile {
            id: random_hex(8),
            name: name.to_string(),
            base_url: base_url.to_string(),
            kind,
        };
        state.profiles.push(profile.clone());
        state.active_id = Some(profile.id.clone());
        self.save(&state)?;
        Ok(profile)
    }

    pub fn set_active(&self, id: &str) -> AppResult<()> {
        let mut state = self.state.lock().unwrap();
        if !state.profiles.iter().any(|p| p.id == id) {
            return Err(AppError::invalid("no such controller profile"));
        }
        state.active_id = Some(id.to_string());
        self.save(&state)
    }

    pub fn remove(&self, id: &str) -> AppResult<()> {
        let mut state = self.state.lock().unwrap();
        state.profiles.retain(|p| p.id != id);
        if state.active_id.as_deref() == Some(id) {
            state.active_id = state.profiles.first().map(|p| p.id.clone());
        }
        self.save(&state)?;
        drop(state);
        self.tokens.delete(id);
        Ok(())
    }

    /// The standalone controller's profile, created on first use; its URL
    /// follows the port the local API got at this start.
    pub fn upsert_local(&self, base_url: &str) -> AppResult<Profile> {
        let mut state = self.state.lock().unwrap();
        if let Some(existing) = state
            .profiles
            .iter_mut()
            .find(|p| p.kind == ProfileKind::Local)
        {
            if existing.base_url != base_url {
                existing.base_url = base_url.to_string();
            }
            let profile = existing.clone();
            self.save(&state)?;
            return Ok(profile);
        }
        let profile = Profile {
            id: random_hex(8),
            name: "This computer".into(),
            base_url: base_url.to_string(),
            kind: ProfileKind::Local,
        };
        state.profiles.insert(0, profile.clone());
        self.save(&state)?;
        Ok(profile)
    }
}

fn active_id(state: &ProfilesFile) -> Option<String> {
    state
        .active_id
        .clone()
        .filter(|id| state.profiles.iter().any(|p| &p.id == id))
        .or_else(|| state.profiles.first().map(|p| p.id.clone()))
}

/// A controller URL as the user typed it, normalized: http(s), a host, no
/// credentials, query or fragment, no trailing slash. A path prefix is kept
/// for controllers behind a reverse proxy.
pub fn normalize_base_url(input: &str) -> AppResult<String> {
    let invalid = || AppError::invalid("Enter a valid URL like http://host:8000");
    let url = url::Url::parse(input.trim()).map_err(|_| invalid())?;
    if !matches!(url.scheme(), "http" | "https") || url.host_str().is_none_or(str::is_empty) {
        return Err(invalid());
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err(AppError::invalid(
            "The controller URL must not contain a user name or password",
        ));
    }
    if url.query().is_some() || url.fragment().is_some() {
        return Err(invalid());
    }
    Ok(url.as_str().trim_end_matches('/').to_string())
}

pub fn is_loopback(base_url: &str) -> bool {
    let Ok(url) = url::Url::parse(base_url) else {
        return false;
    };
    match url.host() {
        Some(url::Host::Domain(d)) => d.eq_ignore_ascii_case("localhost"),
        Some(url::Host::Ipv4(ip)) => ip.is_loopback(),
        Some(url::Host::Ipv6(ip)) => ip.is_loopback(),
        None => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn store(dir: &tempfile::TempDir) -> ProfileStore {
        ProfileStore::load(
            dir.path().join("profiles.json"),
            Box::<MemoryTokens>::default(),
        )
    }

    #[test]
    fn profiles_persist_without_tokens_in_the_file() {
        let dir = tempfile::tempdir().unwrap();
        let s = store(&dir);
        let p = s
            .add("Home lab", "http://10.0.0.5:8000", ProfileKind::Remote)
            .unwrap();
        s.set_token(&p.id, "secret-token").unwrap();
        let text = std::fs::read_to_string(dir.path().join("profiles.json")).unwrap();
        assert!(!text.contains("secret-token"));
        let reloaded = store(&dir);
        assert_eq!(reloaded.active().unwrap(), p);
        let (views, active) = s.views();
        assert_eq!(active.as_deref(), Some(p.id.as_str()));
        assert!(views[0].signed_in);
    }

    #[test]
    fn the_first_profile_is_active_when_the_saved_one_is_gone() {
        let dir = tempfile::tempdir().unwrap();
        let s = store(&dir);
        let a = s.add("a", "http://a:8000", ProfileKind::Remote).unwrap();
        let b = s.add("b", "http://b:8000", ProfileKind::Remote).unwrap();
        assert_eq!(s.active().unwrap().id, b.id);
        s.remove(&b.id).unwrap();
        assert_eq!(s.active().unwrap().id, a.id);
        assert!(s.set_active("missing").is_err());
    }

    #[test]
    fn the_local_profile_follows_the_api_port() {
        let dir = tempfile::tempdir().unwrap();
        let s = store(&dir);
        let first = s.upsert_local("http://127.0.0.1:18001").unwrap();
        let second = s.upsert_local("http://127.0.0.1:18002").unwrap();
        assert_eq!(first.id, second.id);
        assert_eq!(s.get(&first.id).unwrap().base_url, "http://127.0.0.1:18002");
        assert_eq!(second.kind, ProfileKind::Local);
    }

    #[test]
    fn base_urls_are_validated_and_normalized() {
        assert_eq!(
            normalize_base_url(" http://host:8000/ ").unwrap(),
            "http://host:8000"
        );
        assert_eq!(
            normalize_base_url("https://x.example/lycosa/").unwrap(),
            "https://x.example/lycosa"
        );
        for bad in [
            "host:8000",
            "ftp://host",
            "http://user:pw@host",
            "http://host/?a=1",
            "not a url",
        ] {
            assert!(normalize_base_url(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn loopback_hosts_are_recognized() {
        assert!(is_loopback("http://localhost:8000"));
        assert!(is_loopback("http://127.0.0.1:8000"));
        assert!(is_loopback("http://[::1]:8000"));
        assert!(!is_loopback("http://192.168.1.4:8000"));
        assert!(!is_loopback("http://localhost.evil.com"));
    }
}
