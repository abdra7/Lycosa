//! `lycosa --smoke-test --data-dir <empty dir> [--report <file>]`
//!
//! Exercises the installed app's bundled runtime without opening a window:
//! starts every standalone service in a throwaway data directory, signs in
//! with the generated admin account, round-trips a document through the
//! knowledge plane (PostgreSQL + Qdrant + the API), stops cleanly, starts
//! again and checks the data survived, then stops. Exit code 0 means every
//! step passed. CI runs it against each installed package; it never touches
//! a real user's data because the data directory is mandatory.

use std::path::PathBuf;
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::{json, Value};

use crate::supervisor::{Phase, Supervisor};

#[derive(Serialize)]
struct Step {
    name: String,
    ok: bool,
    seconds: f64,
    detail: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Report {
    ok: bool,
    version: String,
    runtime_dir: String,
    data_dir: String,
    steps: Vec<Step>,
}

fn arg(args: &[String], name: &str) -> Option<String> {
    args.iter()
        .position(|a| a == name)
        .and_then(|i| args.get(i + 1))
        .cloned()
}

pub fn run_cli(args: &[String], default_runtime: Option<PathBuf>, version: &str) -> i32 {
    #[cfg(windows)]
    unsafe {
        // a GUI-subsystem binary has no console; borrow the caller's
        windows_sys::Win32::System::Console::AttachConsole(
            windows_sys::Win32::System::Console::ATTACH_PARENT_PROCESS,
        );
    }
    let Some(data_dir) = arg(args, "--data-dir").map(PathBuf::from) else {
        eprintln!("--smoke-test needs --data-dir <empty directory>");
        return 2;
    };
    let runtime_dir = arg(args, "--runtime-dir")
        .map(PathBuf::from)
        .or(default_runtime)
        .unwrap_or_default();
    let report = tauri::async_runtime::block_on(run(runtime_dir, data_dir, version));
    let text = serde_json::to_string_pretty(&report).unwrap_or_default();
    println!("{text}");
    if let Some(path) = arg(args, "--report") {
        let _ = std::fs::write(path, &text);
    }
    if report.ok {
        0
    } else {
        1
    }
}

async fn run(runtime_dir: PathBuf, data_dir: PathBuf, version: &str) -> Report {
    let mut report = Report {
        ok: false,
        version: version.to_string(),
        runtime_dir: runtime_dir.to_string_lossy().into_owned(),
        data_dir: data_dir.to_string_lossy().into_owned(),
        steps: Vec::new(),
    };
    let supervisor = Supervisor::new(runtime_dir, data_dir, version);
    let outcome = exercise(&supervisor, &mut report.steps).await;
    // whatever happened, leave nothing running
    supervisor.stop().await;
    report.ok = outcome.is_ok() && report.steps.iter().all(|s| s.ok);
    if let Err(e) = outcome {
        report.steps.push(Step {
            name: "aborted".into(),
            ok: false,
            seconds: 0.0,
            detail: e,
        });
        for service in [
            "supervisor",
            "postgres",
            "postgres-setup",
            "qdrant",
            "backend",
        ] {
            for line in supervisor.logs(service, 30) {
                eprintln!("[{service}] {}", line.line);
            }
        }
    }
    report
}

async fn step<T>(
    steps: &mut Vec<Step>,
    name: &str,
    f: impl std::future::Future<Output = Result<(T, String), String>>,
) -> Result<T, String> {
    let started = Instant::now();
    let result = f.await;
    let seconds = (started.elapsed().as_secs_f64() * 10.0).round() / 10.0;
    match result {
        Ok((value, detail)) => {
            eprintln!("[smoke] ok   {name} ({seconds}s) {detail}");
            steps.push(Step {
                name: name.into(),
                ok: true,
                seconds,
                detail,
            });
            Ok(value)
        }
        Err(e) => {
            eprintln!("[smoke] FAIL {name} ({seconds}s) {e}");
            steps.push(Step {
                name: name.into(),
                ok: false,
                seconds,
                detail: e.clone(),
            });
            Err(format!("{name}: {e}"))
        }
    }
}

async fn exercise(supervisor: &Supervisor, steps: &mut Vec<Step>) -> Result<(), String> {
    if !supervisor.available() {
        return Err(supervisor
            .status()
            .message
            .unwrap_or_else(|| "runtime unavailable".into()));
    }
    let client = reqwest::Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(300))
        .build()
        .map_err(|e| e.to_string())?;

    let api = step(
        steps,
        "first start (initdb, migrations, all services)",
        async {
            let status = supervisor.start().await?;
            let url = status.api_url.ok_or("no API URL")?;
            Ok((url.clone(), url))
        },
    )
    .await?;
    let (email, password) = supervisor
        .admin_credentials()
        .await
        .ok_or("no admin credentials")?;

    let token = step(steps, "health, sign-in and /me", async {
        let health: Value = get_json(&client, &format!("{api}/healthz"), None).await?;
        let token = sign_in(&client, &api, &email, &password).await?;
        let me = get_json(&client, &format!("{api}/api/v1/me"), Some(&token)).await?;
        if me["role"] != "admin" {
            return Err(format!("unexpected principal {me}"));
        }
        Ok((token, format!("version {}", health["version"])))
    })
    .await?;

    let collection = format!("smoke-{}", crate::supervisor::config::random_hex(4));
    step(steps, "knowledge round trip (PostgreSQL + Qdrant)", async {
        let created: Value = client
            .post(format!("{api}/api/v1/knowledge/collections"))
            .bearer_auth(&token)
            .json(&json!({"name": collection, "description": "desktop smoke test"}))
            .send()
            .await
            .map_err(|e| e.to_string())?
            .error_for_status()
            .map_err(|e| e.to_string())?
            .json()
            .await
            .map_err(|e| e.to_string())?;
        let id = created["id"]
            .as_str()
            .ok_or("no collection id")?
            .to_string();
        let form = reqwest::multipart::Form::new().part(
            "file",
            reqwest::multipart::Part::bytes(
                b"Lycosa standalone smoke test. The bundled vector store answers this sentence."
                    .to_vec(),
            )
            .file_name("smoke.txt"),
        );
        let doc: Value = client
            .post(format!("{api}/api/v1/knowledge/collections/{id}/documents"))
            .bearer_auth(&token)
            .multipart(form)
            .send()
            .await
            .map_err(|e| e.to_string())?
            .json()
            .await
            .map_err(|e| e.to_string())?;
        if doc["status"] != "embedded" {
            return Err(format!("document not embedded: {doc}"));
        }
        let found = retrieve(&client, &api, &token, &collection).await?;
        Ok(((), format!("{found} chunk(s) retrieved")))
    })
    .await?;

    step(steps, "clean stop", async {
        supervisor.stop().await;
        match supervisor.status().phase {
            Phase::Off => Ok(((), String::new())),
            other => Err(format!("phase after stop: {other:?}")),
        }
    })
    .await?;

    let api = step(steps, "second start (existing data)", async {
        let status = supervisor.start().await?;
        let url = status.api_url.ok_or("no API URL")?;
        Ok((url.clone(), url))
    })
    .await?;

    step(steps, "data survived the restart", async {
        let token = sign_in(&client, &api, &email, &password).await?;
        let found = retrieve(&client, &api, &token, &collection).await?;
        Ok(((), format!("{found} chunk(s) after restart")))
    })
    .await?;

    step(steps, "final stop", async {
        supervisor.stop().await;
        Ok(((), String::new()))
    })
    .await
}

async fn get_json(
    client: &reqwest::Client,
    url: &str,
    token: Option<&str>,
) -> Result<Value, String> {
    let mut request = client.get(url);
    if let Some(token) = token {
        request = request.bearer_auth(token);
    }
    request
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())
}

async fn sign_in(
    client: &reqwest::Client,
    api: &str,
    email: &str,
    password: &str,
) -> Result<String, String> {
    let body: Value = client
        .post(format!("{api}/api/v1/auth/login"))
        .json(&json!({"email": email, "password": password}))
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())?;
    body["access_token"]
        .as_str()
        .map(str::to_string)
        .ok_or_else(|| "no access token".into())
}

async fn retrieve(
    client: &reqwest::Client,
    api: &str,
    token: &str,
    collection: &str,
) -> Result<usize, String> {
    let body: Value = client
        .post(format!("{api}/api/v1/knowledge/retrieve"))
        .bearer_auth(token)
        .json(&json!({"query": "bundled vector store", "collection": collection, "top_k": 3}))
        .send()
        .await
        .map_err(|e| e.to_string())?
        .error_for_status()
        .map_err(|e| e.to_string())?
        .json()
        .await
        .map_err(|e| e.to_string())?;
    let count = body["chunks"].as_array().map_or(0, Vec::len);
    if count == 0 {
        return Err(format!("retrieval returned nothing: {body}"));
    }
    Ok(count)
}
