//! OpenRouter's PKCE sign-in (ADR-031): a one-shot listener on 127.0.0.1
//! receives the browser redirect (`/callback?code=…`). Only this machine can
//! deliver a code; the listener closes after the first code or a timeout.
//! The code verifier never leaves the controller.

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Duration;

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;

use crate::error::{AppError, AppResult};
use crate::supervisor::config::random_hex;

const PAGE: &str = "<html><body style=\"font-family:sans-serif\"><p>Lycosa received the \
                    OpenRouter sign-in. You can close this window.</p></body></html>";

#[derive(Default)]
pub struct Receivers(Mutex<HashMap<String, TcpListener>>);

impl Receivers {
    /// Bind a fresh listener; returns its id and the callback URL to give
    /// the controller.
    pub async fn start(&self) -> AppResult<(String, String)> {
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let port = listener.local_addr()?.port();
        let id = random_hex(8);
        self.0.lock().unwrap().insert(id.clone(), listener);
        Ok((id, format!("http://127.0.0.1:{port}/callback")))
    }

    pub fn cancel(&self, id: &str) {
        self.0.lock().unwrap().remove(id);
    }

    /// Wait for the redirect carrying the code; None on timeout or cancel.
    pub async fn wait(&self, id: &str, timeout: Duration) -> AppResult<Option<String>> {
        let listener = self
            .0
            .lock()
            .unwrap()
            .remove(id)
            .ok_or_else(|| AppError::invalid("unknown sign-in listener"))?;
        let deadline = tokio::time::Instant::now() + timeout;
        loop {
            let accepted = tokio::time::timeout_at(deadline, listener.accept()).await;
            let Ok(Ok((mut stream, _))) = accepted else {
                return Ok(None);
            };
            let mut buf = vec![0u8; 8192];
            let mut len = 0;
            while len < buf.len() {
                match tokio::time::timeout(Duration::from_secs(5), stream.read(&mut buf[len..]))
                    .await
                {
                    Ok(Ok(0)) | Err(_) | Ok(Err(_)) => break,
                    Ok(Ok(n)) => {
                        len += n;
                        if buf[..len].windows(4).any(|w| w == b"\r\n\r\n") {
                            break;
                        }
                    }
                }
            }
            let request = String::from_utf8_lossy(&buf[..len]);
            let code = code_from_request(&request);
            let (status, body) = match code {
                Some(_) => ("200 OK", PAGE),
                None => ("404 Not Found", "Not found"),
            };
            let response = format!(
                "HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            let _ = stream.write_all(response.as_bytes()).await;
            let _ = stream.shutdown().await;
            if code.is_some() {
                return Ok(code);
            }
        }
    }
}

/// The `code` query parameter of a `GET /callback?...` request line.
pub fn code_from_request(request: &str) -> Option<String> {
    let line = request.lines().next()?;
    let mut parts = line.split_whitespace();
    if parts.next()? != "GET" {
        return None;
    }
    let target = parts.next()?;
    let url = url::Url::parse(&format!("http://127.0.0.1{target}")).ok()?;
    if url.path() != "/callback" {
        return None;
    }
    url.query_pairs()
        .find(|(k, _)| k == "code")
        .map(|(_, v)| v.into_owned())
        .filter(|c| !c.is_empty())
}

/// Only OpenRouter's documented sign-in page is ever handed to the OS
/// browser, as a single argument to the platform URL handler.
pub fn is_openrouter_sign_in(url: &str) -> bool {
    url::Url::parse(url).is_ok_and(|u| {
        u.scheme() == "https" && u.host_str() == Some("openrouter.ai") && u.port().is_none()
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_the_code() {
        assert_eq!(
            code_from_request("GET /callback?code=abc%2F1&state=x HTTP/1.1\r\nHost: x\r\n\r\n"),
            Some("abc/1".into())
        );
        assert_eq!(code_from_request("GET /favicon.ico HTTP/1.1\r\n\r\n"), None);
        assert_eq!(
            code_from_request("POST /callback?code=x HTTP/1.1\r\n\r\n"),
            None
        );
        assert_eq!(
            code_from_request("GET /callback?code= HTTP/1.1\r\n\r\n"),
            None
        );
    }

    #[test]
    fn only_openrouter_https_opens() {
        assert!(is_openrouter_sign_in(
            "https://openrouter.ai/auth?callback_url=x"
        ));
        assert!(!is_openrouter_sign_in("http://openrouter.ai/auth"));
        assert!(!is_openrouter_sign_in(
            "https://openrouter.ai.evil.com/auth"
        ));
        assert!(!is_openrouter_sign_in("file:///etc/passwd"));
    }

    #[tokio::test]
    async fn receives_one_code() {
        let receivers = Receivers::default();
        let (id, callback) = receivers.start().await.unwrap();
        let port: u16 = callback
            .split(':')
            .nth(2)
            .unwrap()
            .split('/')
            .next()
            .unwrap()
            .parse()
            .unwrap();
        let waiter =
            tokio::spawn(async move { receivers.wait(&id, Duration::from_secs(10)).await });
        tokio::time::sleep(Duration::from_millis(100)).await;
        let mut probe = tokio::net::TcpStream::connect(("127.0.0.1", port))
            .await
            .unwrap();
        probe
            .write_all(b"GET /other HTTP/1.1\r\n\r\n")
            .await
            .unwrap();
        let mut sink = Vec::new();
        let _ = probe.read_to_end(&mut sink).await;
        assert!(String::from_utf8_lossy(&sink).starts_with("HTTP/1.1 404"));
        let mut real = tokio::net::TcpStream::connect(("127.0.0.1", port))
            .await
            .unwrap();
        real.write_all(b"GET /callback?code=the-code HTTP/1.1\r\n\r\n")
            .await
            .unwrap();
        assert_eq!(waiter.await.unwrap().unwrap(), Some("the-code".into()));
    }
}
