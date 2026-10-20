//! The controller's live event stream (`WS /api/v1/events`, ADR-016), held
//! open in Rust for the active profile and forwarded to the webview, with
//! the Flutter app's five-second reconnect.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use futures_util::StreamExt;
use serde::Serialize;
use serde_json::Value;
use tauri::async_runtime::JoinHandle;
use tokio_tungstenite::tungstenite::Message;

pub const RECONNECT_DELAY: Duration = Duration::from_secs(5);

#[derive(Debug, Clone, Serialize, PartialEq)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Outgoing {
    Status { connected: bool },
    Event { event: Value },
}

pub type Sink = Arc<dyn Fn(Outgoing) + Send + Sync>;

#[derive(Default)]
pub struct EventsHub {
    current: Mutex<Option<(String, JoinHandle<()>)>>,
}

impl EventsHub {
    /// Follow `profile_id`'s stream; a no-op when already following it.
    pub fn start(&self, profile_id: &str, base_url: &str, token: &str, sink: Sink) {
        let mut current = self.current.lock().unwrap();
        if let Some((id, handle)) = current.as_ref() {
            if id == profile_id && !handle.inner().is_finished() {
                return;
            }
        }
        if let Some((_, handle)) = current.take() {
            handle.abort();
        }
        let url = ws_url(base_url, token);
        // Tauri's runtime, not tokio::spawn: synchronous commands run on the
        // UI thread, which has no Tokio context, and a panic there aborts
        // the whole app
        let handle = tauri::async_runtime::spawn(run(url, sink));
        *current = Some((profile_id.to_string(), handle));
    }

    pub fn stop(&self, sink: &Sink) {
        if let Some((_, handle)) = self.current.lock().unwrap().take() {
            handle.abort();
            sink(Outgoing::Status { connected: false });
        }
    }
}

/// `http(s)://host[/prefix]` → `ws(s)://host[/prefix]/api/v1/events?token=…`
pub fn ws_url(base_url: &str, token: &str) -> String {
    let ws = if let Some(rest) = base_url.strip_prefix("https://") {
        format!("wss://{rest}")
    } else {
        format!("ws://{}", base_url.trim_start_matches("http://"))
    };
    let encoded: String = url::form_urlencoded::byte_serialize(token.as_bytes()).collect();
    format!("{ws}/api/v1/events?token={encoded}")
}

async fn run(url: String, sink: Sink) {
    loop {
        if let Ok((socket, _)) = tokio_tungstenite::connect_async(url.as_str()).await {
            sink(Outgoing::Status { connected: true });
            let (_, mut read) = socket.split();
            while let Some(message) = read.next().await {
                match message {
                    Ok(Message::Text(text)) => {
                        if let Ok(event) = serde_json::from_str::<Value>(text.as_str()) {
                            sink(Outgoing::Event { event });
                        }
                    }
                    Ok(Message::Close(_)) | Err(_) => break,
                    Ok(_) => {}
                }
            }
            sink(Outgoing::Status { connected: false });
        }
        tokio::time::sleep(RECONNECT_DELAY).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn builds_the_event_stream_url() {
        assert_eq!(
            ws_url("http://10.0.0.5:8000", "a.b+c/d="),
            "ws://10.0.0.5:8000/api/v1/events?token=a.b%2Bc%2Fd%3D"
        );
        assert_eq!(
            ws_url("https://ctl.example/lycosa", "t"),
            "wss://ctl.example/lycosa/api/v1/events?token=t"
        );
    }

    /// `events_start` is a synchronous command: it runs on the UI thread,
    /// outside any Tokio runtime. Starting the stream there must not panic
    /// (it did, and a panic in the webview callback aborted the app).
    #[test]
    fn starts_from_a_thread_without_a_tokio_runtime() {
        let hub = Arc::new(EventsHub::default());
        let worker = hub.clone();
        std::thread::spawn(move || {
            worker.start("p1", "http://127.0.0.1:9", "t", Arc::new(|_| {}));
        })
        .join()
        .expect("starting the event stream panicked");
        hub.stop(&(Arc::new(|_| {}) as Sink));
    }

    #[tokio::test]
    async fn relays_events_and_reports_disconnects() {
        use tokio::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            let mut ws = tokio_tungstenite::accept_async(stream).await.unwrap();
            use futures_util::SinkExt;
            ws.send(Message::Text(
                r#"{"type":"node.connected","ts":"2026-10-20T00:00:00Z","data":{"name":"n1"}}"#
                    .into(),
            ))
            .await
            .unwrap();
            ws.close(None).await.unwrap();
        });
        let received = Arc::new(Mutex::new(Vec::new()));
        let log = received.clone();
        let hub = EventsHub::default();
        hub.start(
            "p1",
            &format!("http://127.0.0.1:{port}"),
            "tok",
            Arc::new(move |o| log.lock().unwrap().push(o)),
        );
        for _ in 0..50 {
            if received.lock().unwrap().len() >= 3 {
                break;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        let got = received.lock().unwrap().clone();
        assert_eq!(got[0], Outgoing::Status { connected: true });
        assert!(matches!(&got[1], Outgoing::Event { event } if event["type"] == "node.connected"));
        assert_eq!(got[2], Outgoing::Status { connected: false });
    }
}
