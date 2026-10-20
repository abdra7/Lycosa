//! Integration tests of the supervisor against a real staged runtime:
//! PostgreSQL, Qdrant and the Lycosa API actually run, each test in its own
//! throwaway data directory (never the user's data).
//!
//! They need `LYCOSA_RUNTIME_DIR` (a runtime staged by
//! scripts/stage_runtime.py) and skip themselves without it. Run them one
//! at a time: `cargo test --test standalone -- --test-threads=1`.

use std::net::{Ipv4Addr, TcpListener};
use std::path::{Path, PathBuf};
use std::time::Duration;

use lycosa_desktop_lib::supervisor::config::{Secrets, StandaloneConfig};
use lycosa_desktop_lib::supervisor::paths::RuntimeLayout;
use lycosa_desktop_lib::supervisor::{process, Phase, ServiceName, ServiceState, Supervisor};

fn runtime() -> Option<PathBuf> {
    let dir = std::env::var_os("LYCOSA_RUNTIME_DIR").map(PathBuf::from);
    if dir.is_none() {
        eprintln!("LYCOSA_RUNTIME_DIR is not set; skipping the standalone integration test");
    }
    dir
}

fn supervisor(runtime: &Path, data: &tempfile::TempDir) -> Supervisor {
    Supervisor::new(runtime.to_path_buf(), data.path().to_path_buf(), "0.5.0")
}

async fn health(api: &str) -> bool {
    reqwest::Client::new()
        .get(format!("{api}/healthz"))
        .timeout(Duration::from_secs(5))
        .send()
        .await
        .is_ok_and(|r| r.status().is_success())
}

fn service(sup: &Supervisor, name: ServiceName) -> lycosa_desktop_lib::supervisor::ServiceStatus {
    sup.status()
        .services
        .into_iter()
        .find(|s| s.name == name)
        .unwrap()
}

#[tokio::test(flavor = "multi_thread")]
async fn a_killed_api_is_restarted() {
    let Some(rt) = runtime() else { return };
    let data = tempfile::tempdir().unwrap();
    let sup = supervisor(&rt, &data);
    let status = sup.start().await.expect("start");
    let api = status.api_url.unwrap();
    let pid = service(&sup, ServiceName::Backend).pid.unwrap();

    process::kill_stale(pid, &RuntimeLayout::new(&rt).python());

    let mut recovered = false;
    for _ in 0..90 {
        tokio::time::sleep(Duration::from_secs(1)).await;
        let backend = service(&sup, ServiceName::Backend);
        if backend.restarts >= 1
            && backend.state == ServiceState::Running
            && backend.pid != Some(pid)
            && health(&api).await
        {
            recovered = true;
            break;
        }
    }
    sup.stop().await;
    assert!(recovered, "the API was not restarted: {:?}", sup.status());
}

#[tokio::test(flavor = "multi_thread")]
async fn network_mode_refuses_a_busy_port_and_leaves_nothing_running() {
    let Some(rt) = runtime() else { return };
    let data = tempfile::tempdir().unwrap();
    let squatter = TcpListener::bind((Ipv4Addr::UNSPECIFIED, 0)).unwrap();
    let port = squatter.local_addr().unwrap().port();
    let sup = supervisor(&rt, &data);
    sup.set_network(true, port).await.unwrap();

    let err = sup.start().await.unwrap_err();
    assert!(err.contains("already in use"), "{err}");
    let status = sup.status();
    assert_eq!(status.phase, Phase::Error);
    assert!(status
        .services
        .iter()
        .all(|s| s.state == ServiceState::Stopped));
    // PostgreSQL never started: no server owns the data directory
    assert!(!data.path().join("postgres").join("postmaster.pid").exists());
}

#[tokio::test(flavor = "multi_thread")]
async fn a_cluster_from_another_postgres_major_is_refused() {
    let Some(rt) = runtime() else { return };
    let data = tempfile::tempdir().unwrap();
    let pgdata = data.path().join("postgres");
    std::fs::create_dir_all(&pgdata).unwrap();
    std::fs::write(pgdata.join("PG_VERSION"), "15\n").unwrap();
    let sup = supervisor(&rt, &data);
    let err = sup.start().await.unwrap_err();
    assert!(err.contains("PostgreSQL 15"), "{err}");
    // the existing data is untouched
    assert_eq!(
        std::fs::read_to_string(pgdata.join("PG_VERSION")).unwrap(),
        "15\n"
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn an_upgrade_backs_up_the_database_before_migrating() {
    let Some(rt) = runtime() else { return };
    let data = tempfile::tempdir().unwrap();
    let sup = supervisor(&rt, &data);
    sup.start().await.expect("first start");
    sup.stop().await;
    let backups = data.path().join("backups");
    let count = || std::fs::read_dir(&backups).map(|d| d.count()).unwrap_or(0);
    assert_eq!(count(), 0, "a fresh install needs no backup");

    // pretend the data was last used by an older app version
    let config_path = data.path().join("standalone.json");
    let mut config = StandaloneConfig::load(&config_path);
    config.last_app_version = Some("0.4.0".into());
    config.save(&config_path).unwrap();

    let sup = supervisor(&rt, &data);
    sup.start().await.expect("upgrade start");
    sup.stop().await;
    let dumps: Vec<String> = std::fs::read_dir(&backups)
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
        .collect();
    assert_eq!(dumps.len(), 1, "{dumps:?}");
    assert!(
        dumps[0].ends_with("before-0.5.0-from-0.4.0.dump"),
        "{dumps:?}"
    );
    assert!(
        StandaloneConfig::load(&config_path)
            .last_app_version
            .as_deref()
            == Some("0.5.0")
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn loopback_by_default_and_secrets_never_reach_the_logs() {
    let Some(rt) = runtime() else { return };
    let data = tempfile::tempdir().unwrap();
    let sup = supervisor(&rt, &data);
    let status = sup.start().await.expect("start");
    let api = status.api_url.clone().unwrap();
    assert!(api.starts_with("http://127.0.0.1:"));
    let port: u16 = api.rsplit(':').next().unwrap().parse().unwrap();

    // the API answers loopback only
    if let Some(ip) = lycosa_desktop_lib::netinfo::detect_lan_ipv4() {
        let reachable = tokio::time::timeout(
            Duration::from_secs(3),
            tokio::net::TcpStream::connect((ip.as_str(), port)),
        )
        .await
        .is_ok_and(|r| r.is_ok());
        assert!(!reachable, "the API must not listen on {ip}");
    }

    // sign in and open the event stream so a token passes through the logs
    let (email, password) = sup.admin_credentials().await.unwrap();
    let login: serde_json::Value = reqwest::Client::new()
        .post(format!("{api}/api/v1/auth/login"))
        .json(&serde_json::json!({"email": email, "password": password}))
        .send()
        .await
        .unwrap()
        .json()
        .await
        .unwrap();
    let token = login["access_token"].as_str().unwrap().to_string();
    let ws = format!(
        "ws{}/api/v1/events?token={token}",
        api.trim_start_matches("http")
    );
    if let Ok((mut socket, _)) = tokio_tungstenite::connect_async(ws).await {
        let _ = socket.close(None).await;
    }
    tokio::time::sleep(Duration::from_secs(1)).await;
    sup.stop().await;

    let secrets = Secrets::load(&data.path().join("secrets.json")).unwrap();
    let mut forbidden = secrets.values();
    forbidden.push(token);
    for entry in std::fs::read_dir(data.path().join("logs")).unwrap() {
        let path = entry.unwrap().path();
        let text = std::fs::read_to_string(&path).unwrap_or_default();
        for secret in &forbidden {
            assert!(
                !text.contains(secret.as_str()),
                "{} contains a secret",
                path.display()
            );
        }
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_second_supervisor_on_the_same_data_is_refused() {
    let Some(rt) = runtime() else { return };
    let data = tempfile::tempdir().unwrap();
    let first = supervisor(&rt, &data);
    first.start().await.expect("first start");
    let second = supervisor(&rt, &data);
    let err = second.start().await.unwrap_err();
    first.stop().await;
    assert!(err.contains("already running"), "{err}");
}
