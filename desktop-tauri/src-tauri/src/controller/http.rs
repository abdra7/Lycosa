//! Requests to a controller, made in Rust on the webview's behalf. The
//! webview names a method and an `/api/v1/...` path; Rust adds the active
//! profile's base URL and token, applies the per-call timeout, and refuses
//! anything outside the API. Redirects are never followed (ADR-031's rule for
//! credential-bearing requests, applied to all of them).

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::StreamExt;
use reqwest::{Client, Method};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::Notify;

use crate::error::{AppError, AppResult};

use super::profiles::is_loopback;

pub const DEFAULT_TIMEOUT: Duration = Duration::from_secs(15);
/// Model pulls (16 min) are the longest call the app makes.
pub const MAX_TIMEOUT: Duration = Duration::from_secs(20 * 60);
const MAX_BODY: usize = 8 * 1024 * 1024;

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct ApiCall {
    pub method: String,
    pub path: String,
    #[serde(default)]
    pub query: Vec<(String, String)>,
    #[serde(default)]
    pub body: Option<Value>,
    #[serde(default)]
    pub timeout_ms: Option<u64>,
    /// The body carries a credential: HTTPS or a loopback controller only.
    #[serde(default)]
    pub secret: bool,
}

pub fn http_client() -> Client {
    Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .no_proxy()
        .connect_timeout(Duration::from_secs(10))
        .user_agent(concat!("Lycosa-Desktop/", env!("CARGO_PKG_VERSION")))
        .build()
        .expect("the HTTP client must build")
}

/// Only the controller API and its liveness probe are reachable; no `..`,
/// no doubled slashes, no characters outside a URL path.
pub fn validate_path(path: &str) -> AppResult<()> {
    let ok_chars = path
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '/' | '-' | '_' | '.' | '~' | '%'));
    let under_api = path.starts_with("/api/v1/") || path == "/healthz";
    if !ok_chars
        || !under_api
        || path.contains("//")
        || path.split('/').any(|s| s == "." || s == "..")
    {
        return Err(AppError::invalid(format!("refusing a request to {path:?}")));
    }
    Ok(())
}

pub fn parse_method(method: &str) -> AppResult<Method> {
    match method {
        "GET" => Ok(Method::GET),
        "POST" => Ok(Method::POST),
        "PUT" => Ok(Method::PUT),
        "PATCH" => Ok(Method::PATCH),
        "DELETE" => Ok(Method::DELETE),
        other => Err(AppError::invalid(format!("unsupported method {other}"))),
    }
}

/// Routes whose request bodies carry credentials (or, for Phantom, content
/// that must not cross the network unencrypted), whatever the webview says.
pub fn always_secret(method: &Method, path: &str, body: Option<&Value>) -> bool {
    let carries = |key: &str| body.and_then(|b| b.get(key)).is_some_and(|v| !v.is_null());
    (method == Method::PUT
        && path.starts_with("/api/v1/admin/providers/")
        && path.ends_with("/credential"))
        || path == "/api/v1/llm/oauth/openrouter/complete"
        || path.starts_with("/api/v1/phantom/tasks")
        || (path.starts_with("/api/v1/llm/accounts") && carries("api_key"))
}

pub fn check_transport(base_url: &str, secret: bool) -> AppResult<()> {
    if secret && !base_url.starts_with("https://") && !is_loopback(base_url) {
        return Err(AppError::Insecure {
            message: "API keys and private inputs need an HTTPS controller URL, or a controller on this computer (localhost).".into(),
        });
    }
    Ok(())
}

pub fn unreachable(base_url: &str, error: &reqwest::Error) -> AppError {
    let message = if error.is_timeout() {
        "the request timed out".to_string()
    } else if error.is_connect() {
        "the connection failed".to_string()
    } else {
        "the request failed".to_string()
    };
    AppError::Unreachable {
        base_url: base_url.to_string(),
        message,
    }
}

/// Decode a controller response: JSON (or null when empty) on success, the
/// error envelope otherwise.
pub fn decode(status: u16, bytes: &[u8]) -> AppResult<Value> {
    let body: Value = if bytes.is_empty() {
        Value::Null
    } else {
        serde_json::from_slice(bytes)
            .unwrap_or_else(|_| Value::String(String::from_utf8_lossy(bytes).into_owned()))
    };
    if status >= 400 {
        return Err(AppError::from_envelope(status, &body));
    }
    if (300..400).contains(&status) {
        return Err(AppError::Insecure {
            message: "the controller tried to redirect the request; redirects are not followed"
                .into(),
        });
    }
    Ok(body)
}

pub async fn send(
    client: &Client,
    base_url: &str,
    token: Option<&str>,
    call: &ApiCall,
) -> AppResult<Value> {
    validate_path(&call.path)?;
    let method = parse_method(&call.method)?;
    let secret = call.secret || always_secret(&method, &call.path, call.body.as_ref());
    check_transport(base_url, secret)?;
    let timeout = call
        .timeout_ms
        .map(Duration::from_millis)
        .unwrap_or(DEFAULT_TIMEOUT)
        .min(MAX_TIMEOUT);
    let mut request = client
        .request(method, format!("{base_url}{}", call.path))
        .timeout(timeout);
    if !call.query.is_empty() {
        request = request.query(&call.query);
    }
    if let Some(token) = token {
        request = request.bearer_auth(token);
    }
    if call.path.starts_with("/api/v1/phantom/") {
        request = request.header("Cache-Control", "no-store");
    }
    if let Some(body) = &call.body {
        let bytes = serde_json::to_vec(body).map_err(|e| AppError::invalid(e.to_string()))?;
        if bytes.len() > MAX_BODY {
            return Err(AppError::invalid("request body too large"));
        }
        request = request
            .header("Content-Type", "application/json")
            .body(bytes);
    }
    let response = request
        .send()
        .await
        .map_err(|e| unreachable(base_url, &e))?;
    let status = response.status().as_u16();
    let bytes = response
        .bytes()
        .await
        .map_err(|e| unreachable(base_url, &e))?;
    decode(status, &bytes)
}

/// One server-sent event of `/llm/chat` (ADR-031 normalized chunks).
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct SseEvent {
    pub event: String,
    pub data: Value,
}

/// Incremental SSE parser, the Dart client's rules: `event:` names the
/// event, `data:` lines accumulate, a blank line dispatches, `:` lines are
/// comments.
#[derive(Default)]
pub struct SseParser {
    buffer: String,
    event: Option<String>,
    data: Vec<String>,
}

impl SseParser {
    pub fn push(&mut self, chunk: &str) -> Vec<SseEvent> {
        self.buffer.push_str(chunk);
        let mut out = Vec::new();
        while let Some(pos) = self.buffer.find('\n') {
            let mut line: String = self.buffer.drain(..=pos).collect();
            line.pop();
            if line.ends_with('\r') {
                line.pop();
            }
            if line.is_empty() {
                if !self.data.is_empty() {
                    let joined = self.data.join("\n");
                    let data = serde_json::from_str(&joined).unwrap_or(Value::Null);
                    out.push(SseEvent {
                        event: self.event.take().unwrap_or_else(|| "message".into()),
                        data: if data.is_object() {
                            data
                        } else {
                            Value::Object(Default::default())
                        },
                    });
                }
                self.event = None;
                self.data.clear();
                continue;
            }
            if line.starts_with(':') {
                continue;
            }
            let (name, value) = match line.find(':') {
                Some(i) => (
                    &line[..i],
                    line[i + 1..].strip_prefix(' ').unwrap_or(&line[i + 1..]),
                ),
                None => (line.as_str(), ""),
            };
            match name {
                "event" => self.event = Some(value.to_string()),
                "data" => self.data.push(value.to_string()),
                _ => {}
            }
        }
        out
    }
}

/// Cancel handles of running streams, by the webview's stream id.
#[derive(Default)]
pub struct Streams(Mutex<HashMap<String, Arc<Notify>>>);

impl Streams {
    pub fn register(&self, id: &str) -> Arc<Notify> {
        let notify = Arc::new(Notify::new());
        self.0
            .lock()
            .unwrap()
            .insert(id.to_string(), notify.clone());
        notify
    }
    pub fn cancel(&self, id: &str) {
        if let Some(notify) = self.0.lock().unwrap().remove(id) {
            notify.notify_one();
        }
    }
    pub fn finish(&self, id: &str) {
        self.0.lock().unwrap().remove(id);
    }
}

/// POST a streaming request and hand each event to `emit` until the stream
/// ends, breaks, or `cancel` fires. The first byte must arrive within two
/// minutes (as in the Flutter client); after that the model may take as long
/// as it needs.
pub async fn stream(
    client: &Client,
    base_url: &str,
    token: &str,
    call: &ApiCall,
    cancel: Arc<Notify>,
    mut emit: impl FnMut(SseEvent),
) -> AppResult<()> {
    validate_path(&call.path)?;
    let method = parse_method(&call.method)?;
    check_transport(base_url, call.secret)?;
    let mut request = client
        .request(method, format!("{base_url}{}", call.path))
        .bearer_auth(token)
        .header("Accept", "text/event-stream");
    if let Some(body) = &call.body {
        request = request.json(body);
    }
    let response = tokio::select! {
        r = tokio::time::timeout(Duration::from_secs(120), request.send()) => match r {
            Ok(Ok(response)) => response,
            Ok(Err(e)) => return Err(unreachable(base_url, &e)),
            Err(_) => return Err(AppError::Unreachable { base_url: base_url.into(), message: "the request timed out".into() }),
        },
        _ = cancel.notified() => return Ok(()),
    };
    let status = response.status().as_u16();
    if status >= 300 {
        let bytes = response.bytes().await.unwrap_or_default();
        decode(status, &bytes)?;
        return Ok(());
    }
    let mut body = response.bytes_stream();
    let mut parser = SseParser::default();
    let mut pending = Vec::new();
    loop {
        tokio::select! {
            chunk = body.next() => match chunk {
                Some(Ok(bytes)) => {
                    pending.extend_from_slice(&bytes);
                    // only hand complete UTF-8 to the parser
                    let valid = match std::str::from_utf8(&pending) {
                        Ok(_) => pending.len(),
                        Err(e) => e.valid_up_to(),
                    };
                    let text = String::from_utf8_lossy(&pending[..valid]).into_owned();
                    pending.drain(..valid);
                    for event in parser.push(&text) {
                        emit(event);
                    }
                }
                Some(Err(e)) => return Err(unreachable(base_url, &e)),
                None => {
                    for event in parser.push("\n\n") {
                        emit(event);
                    }
                    return Ok(());
                }
            },
            _ = cancel.notified() => return Ok(()),
        }
    }
}

/// Multipart upload of one file to a knowledge collection. Ingestion is
/// synchronous on the controller, so this waits up to five minutes.
pub async fn upload(
    client: &Client,
    base_url: &str,
    token: &str,
    collection_id: &str,
    filename: &str,
    bytes: Vec<u8>,
) -> AppResult<Value> {
    if collection_id.is_empty()
        || collection_id.len() > 64
        || !collection_id
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '-')
    {
        return Err(AppError::invalid("invalid collection id"));
    }
    let part = reqwest::multipart::Part::bytes(bytes).file_name(filename.to_string());
    let form = reqwest::multipart::Form::new().part("file", part);
    let response = client
        .post(format!(
            "{base_url}/api/v1/knowledge/collections/{collection_id}/documents"
        ))
        .bearer_auth(token)
        .timeout(Duration::from_secs(300))
        .multipart(form)
        .send()
        .await
        .map_err(|e| unreachable(base_url, &e))?;
    let status = response.status().as_u16();
    let bytes = response
        .bytes()
        .await
        .map_err(|e| unreachable(base_url, &e))?;
    decode(status, &bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn only_api_paths_are_allowed() {
        for ok in [
            "/api/v1/me",
            "/healthz",
            "/api/v1/nodes/abc-123/models",
            "/api/v1/llm/routing/default",
        ] {
            assert!(validate_path(ok).is_ok(), "{ok}");
        }
        for bad in [
            "/metrics",
            "/api/v1/../metrics",
            "/api/v1//me",
            "http://evil/api/v1/me",
            "/api/v1/me?x=1",
            "/api/v1/me#f",
            "api/v1/me",
            "/api/v1/a b",
            "/api/v10/me",
        ] {
            assert!(validate_path(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn credential_routes_are_always_secret() {
        assert!(always_secret(
            &Method::PUT,
            "/api/v1/admin/providers/openai/credential",
            None
        ));
        assert!(always_secret(
            &Method::POST,
            "/api/v1/llm/oauth/openrouter/complete",
            None
        ));
        assert!(always_secret(&Method::POST, "/api/v1/phantom/tasks", None));
        assert!(always_secret(
            &Method::POST,
            "/api/v1/llm/accounts",
            Some(&json!({"api_key": "k"}))
        ));
        assert!(!always_secret(
            &Method::POST,
            "/api/v1/llm/accounts",
            Some(&json!({"label": "x"}))
        ));
        assert!(!always_secret(&Method::GET, "/api/v1/nodes", None));
    }

    #[test]
    fn secrets_need_https_or_loopback() {
        assert!(check_transport("http://192.168.1.5:8000", true).is_err());
        assert!(check_transport("http://127.0.0.1:8000", true).is_ok());
        assert!(check_transport("https://ctl.example", true).is_ok());
        assert!(check_transport("http://192.168.1.5:8000", false).is_ok());
    }

    #[test]
    fn responses_decode_like_the_dart_client() {
        assert_eq!(decode(204, b"").unwrap(), Value::Null);
        assert_eq!(decode(200, br#"{"a":1}"#).unwrap(), json!({"a": 1}));
        let err = decode(
            401,
            br#"{"error":{"code":"unauthorized","message":"Not authenticated"}}"#,
        )
        .unwrap_err();
        assert!(matches!(err, AppError::Api { status: 401, .. }));
        assert!(matches!(
            decode(307, b"").unwrap_err(),
            AppError::Insecure { .. }
        ));
    }

    #[test]
    fn sse_events_are_parsed_across_chunks() {
        let mut parser = SseParser::default();
        assert!(parser.push("event: start\ndata: {\"metadata\":").is_empty());
        let events = parser.push(
            " {\"model\":\"m\"}}\n\n: keepalive\n\nevent: text\r\ndata: {\"text\":\"hi\"}\r\n\r\n",
        );
        assert_eq!(
            events,
            vec![
                SseEvent {
                    event: "start".into(),
                    data: json!({"metadata": {"model": "m"}})
                },
                SseEvent {
                    event: "text".into(),
                    data: json!({"text": "hi"})
                },
            ]
        );
        let events = parser.push("data: [1]\n\n");
        assert_eq!(
            events,
            vec![SseEvent {
                event: "message".into(),
                data: json!({})
            }]
        );
    }
}
