//! Service logs: every line is redacted, kept in a per-service ring buffer
//! for the app's log viewer, and appended to `logs/<service>.log` (rotated
//! at 5 MB, one previous file kept).

use std::collections::{HashMap, VecDeque};
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, RwLock};

use regex::Regex;
use serde::Serialize;

const RING_LINES: usize = 1000;
const ROTATE_BYTES: u64 = 5 * 1024 * 1024;
const REDACTED: &str = "[redacted]";

/// Removes secrets from log text: every known secret value, plus anything
/// shaped like a credential (bearer tokens, `token=` query parameters,
/// `password: x`, Lycosa API keys, passwords inside database URLs).
pub struct Redactor {
    secrets: Vec<String>,
    patterns: Vec<(Regex, &'static str)>,
}

impl Redactor {
    pub fn new(secrets: Vec<String>) -> Self {
        let patterns = [
            (
                r"(?i)((?:bearer|basic)\s+)[A-Za-z0-9\-_.~+/=]+",
                "${1}[redacted]",
            ),
            (
                r"(?i)([?&](?:token|access_token|code|api_key|key)=)[^&\s]+",
                "${1}[redacted]",
            ),
            (
                r#"(?i)("?(?:password|passwd|secret|token|api[_-]?key|credential)"?\s*[:=]\s*"?)[^\s",&}]+"#,
                "${1}[redacted]",
            ),
            (r"lyc_[0-9a-f]+_[A-Za-z0-9_\-]+", "lyc_[redacted]"),
            (r"(://[^:/@\s]+:)[^@\s]+@", "${1}[redacted]@"),
        ];
        Self {
            // longest first, so a secret containing another is fully removed
            secrets: {
                let mut values: Vec<String> =
                    secrets.into_iter().filter(|s| s.len() >= 6).collect();
                values.sort_by_key(|s| std::cmp::Reverse(s.len()));
                values
            },
            patterns: patterns
                .into_iter()
                .map(|(p, r)| (Regex::new(p).expect("redaction pattern"), r))
                .collect(),
        }
    }

    pub fn redact(&self, line: &str) -> String {
        let mut out = line.to_string();
        for secret in &self.secrets {
            if out.contains(secret.as_str()) {
                out = out.replace(secret.as_str(), REDACTED);
            }
        }
        for (pattern, replacement) in &self.patterns {
            out = pattern.replace_all(&out, *replacement).into_owned();
        }
        out
    }
}

#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct LogLine {
    pub ts: String,
    pub line: String,
}

pub struct LogBook {
    dir: PathBuf,
    redactor: RwLock<Redactor>,
    rings: Mutex<HashMap<String, VecDeque<LogLine>>>,
    files: Mutex<HashMap<String, File>>,
}

impl LogBook {
    pub fn new(dir: impl Into<PathBuf>, secrets: Vec<String>) -> Self {
        Self {
            dir: dir.into(),
            redactor: RwLock::new(Redactor::new(secrets)),
            rings: Mutex::new(HashMap::new()),
            files: Mutex::new(HashMap::new()),
        }
    }

    /// New secrets (e.g. a freshly minted node key) join the redaction list.
    pub fn set_secrets(&self, secrets: Vec<String>) {
        *self.redactor.write().unwrap() = Redactor::new(secrets);
    }

    pub fn redact(&self, text: &str) -> String {
        self.redactor.read().unwrap().redact(text)
    }

    pub fn path_for(&self, service: &str) -> PathBuf {
        self.dir.join(format!("{service}.log"))
    }

    pub fn append(&self, service: &str, raw: &str) {
        let line = self.redact(raw.trim_end_matches(['\r', '\n']));
        let entry = LogLine {
            ts: chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string(),
            line,
        };
        {
            let mut rings = self.rings.lock().unwrap();
            let ring = rings.entry(service.to_string()).or_default();
            if ring.len() == RING_LINES {
                ring.pop_front();
            }
            ring.push_back(entry.clone());
        }
        self.write_file(service, &format!("{} {}\n", entry.ts, entry.line));
    }

    fn write_file(&self, service: &str, text: &str) {
        let mut files = self.files.lock().unwrap();
        let path = self.path_for(service);
        if !files.contains_key(service) {
            let _ = std::fs::create_dir_all(&self.dir);
            match OpenOptions::new().create(true).append(true).open(&path) {
                Ok(file) => {
                    files.insert(service.to_string(), file);
                }
                Err(_) => return,
            }
        }
        let rotate = files
            .get(service)
            .and_then(|f| f.metadata().ok())
            .is_some_and(|m| m.len() > ROTATE_BYTES);
        if rotate {
            files.remove(service);
            let _ = std::fs::rename(&path, path.with_extension("log.1"));
            if let Ok(file) = OpenOptions::new().create(true).append(true).open(&path) {
                files.insert(service.to_string(), file);
            }
        }
        if let Some(file) = files.get_mut(service) {
            let _ = file.write_all(text.as_bytes());
        }
    }

    /// The newest `limit` lines of a service. PostgreSQL writes its own log
    /// file (through `pg_ctl -l`), so its lines come from the file's tail.
    pub fn tail(&self, service: &str, limit: usize) -> Vec<LogLine> {
        if service == "postgres" {
            return tail_file(&self.path_for(service), limit)
                .into_iter()
                .map(|line| LogLine {
                    ts: String::new(),
                    line: self.redact(&line),
                })
                .collect();
        }
        let rings = self.rings.lock().unwrap();
        rings
            .get(service)
            .map(|ring| {
                let skip = ring.len().saturating_sub(limit);
                ring.iter().skip(skip).cloned().collect()
            })
            .unwrap_or_default()
    }
}

/// The last `limit` lines of a file, reading at most its final 256 KB.
pub fn tail_file(path: &Path, limit: usize) -> Vec<String> {
    let Ok(mut file) = File::open(path) else {
        return Vec::new();
    };
    let len = file.metadata().map(|m| m.len()).unwrap_or(0);
    let start = len.saturating_sub(256 * 1024);
    if file.seek(SeekFrom::Start(start)).is_err() {
        return Vec::new();
    }
    let mut bytes = Vec::new();
    if file.read_to_end(&mut bytes).is_err() {
        return Vec::new();
    }
    let text = String::from_utf8_lossy(&bytes);
    let lines: Vec<&str> = text.lines().collect();
    // the first line may be cut in half when the read started mid-file
    let skip_partial = usize::from(start > 0);
    let usable = &lines[skip_partial.min(lines.len())..];
    usable[usable.len().saturating_sub(limit)..]
        .iter()
        .map(|s| s.to_string())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_secrets_are_removed() {
        let redactor = Redactor::new(vec!["s3cr3t-value".into(), "abc".into()]);
        assert_eq!(
            redactor.redact("connecting with s3cr3t-value now"),
            "connecting with [redacted] now"
        );
        // too short to redact safely by value
        assert_eq!(redactor.redact("abc"), "abc");
    }

    #[test]
    fn credential_shapes_are_removed() {
        let redactor = Redactor::new(vec![]);
        let cases = [
            (
                "GET /api/v1/events?token=eyJhbGciOi.x.y HTTP/1.1",
                "GET /api/v1/events?token=[redacted] HTTP/1.1",
            ),
            (
                "Authorization: Bearer abc.def-ghi",
                "Authorization: Bearer [redacted]",
            ),
            (
                r#"{"password": "hunter22"}"#,
                r#"{"password": "[redacted]"}"#,
            ),
            ("key lyc_1a2b3c4d_AbCd-ef used", "key lyc_[redacted] used"),
            (
                "postgresql+asyncpg://lycosa:pw123@127.0.0.1:5432/lycosa",
                "postgresql+asyncpg://lycosa:[redacted]@127.0.0.1:5432/lycosa",
            ),
            ("QDRANT_API_KEY=deadbeef", "QDRANT_API_KEY=[redacted]"),
        ];
        for (input, expected) in cases {
            assert_eq!(redactor.redact(input), expected, "input: {input}");
        }
    }

    #[test]
    fn ring_keeps_the_newest_lines_and_files_are_written() {
        let dir = tempfile::tempdir().unwrap();
        let book = LogBook::new(dir.path(), vec!["topsecretvalue".into()]);
        for i in 0..(RING_LINES + 5) {
            book.append("backend", &format!("line {i} topsecretvalue"));
        }
        let tail = book.tail("backend", 3);
        assert_eq!(tail.len(), 3);
        assert_eq!(tail[2].line, format!("line {} [redacted]", RING_LINES + 4));
        let file = std::fs::read_to_string(dir.path().join("backend.log")).unwrap();
        assert!(!file.contains("topsecretvalue"));
        assert_eq!(file.lines().count(), RING_LINES + 5);
    }

    #[test]
    fn postgres_lines_come_from_its_file() {
        let dir = tempfile::tempdir().unwrap();
        let book = LogBook::new(dir.path(), vec!["pgpassword1".into()]);
        std::fs::write(
            dir.path().join("postgres.log"),
            "one\ntwo pgpassword1\nthree\n",
        )
        .unwrap();
        let lines: Vec<String> = book
            .tail("postgres", 2)
            .into_iter()
            .map(|l| l.line)
            .collect();
        assert_eq!(lines, vec!["two [redacted]", "three"]);
    }
}
