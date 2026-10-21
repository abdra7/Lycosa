//! Spawning the services. Every process gets explicit arguments (never a
//! shell line built by us), a minimal allowlisted environment, no console
//! window, and is tied to the app's lifetime where the OS allows it: a
//! kill-on-close Job Object on Windows, a parent-death signal on Linux.
//! macOS has neither; leftovers are cleaned up through `pids.json` on the
//! next start.

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use tokio::io::{AsyncBufReadExt, AsyncRead, BufReader};
use tokio::process::{Child, Command};

use super::logs::LogBook;

/// Host variables a service may need (OS plumbing, temp dirs, home, locale);
/// everything else, including any PYTHON*, PG* or QDRANT* the user has set,
/// is left out.
const INHERITED: &[&str] = &[
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "COMPUTERNAME",
    "USERNAME",
    "USERDOMAIN",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "XDG_RUNTIME_DIR",
    "PATH",
];

pub type Env = Vec<(OsString, OsString)>;

pub fn base_env() -> Env {
    INHERITED
        .iter()
        .filter_map(|name| std::env::var_os(name).map(|value| (OsString::from(name), value)))
        .collect()
}

pub fn with(env: &Env, extra: &[(&str, String)]) -> Env {
    let mut out: Env = env
        .iter()
        .filter(|(k, _)| !extra.iter().any(|(name, _)| k == name))
        .cloned()
        .collect();
    out.extend(
        extra
            .iter()
            .map(|(k, v)| (OsString::from(k), OsString::from(v))),
    );
    out
}

/// A command with a clean environment and no console window.
pub fn command(program: &Path, args: &[&str], cwd: &Path, env: &Env) -> Command {
    let mut cmd = Command::new(program);
    cmd.args(args)
        .current_dir(cwd)
        .env_clear()
        .envs(env.iter().map(|(k, v)| (k, v)))
        .stdin(Stdio::null())
        .kill_on_drop(true);
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

/// Linux: the child gets SIGTERM when the app dies, even on a crash. The
/// spawning thread must outlive the child, which holds for the tokio worker
/// threads services are spawned from.
#[cfg(target_os = "linux")]
fn tie_to_parent(cmd: &mut Command) {
    unsafe {
        cmd.pre_exec(|| {
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
}

#[cfg(not(target_os = "linux"))]
fn tie_to_parent(_cmd: &mut Command) {}

/// A long-running service process whose output feeds the log book.
pub struct ServiceProcess {
    pub child: Child,
    pub pid: u32,
}

pub fn spawn_service(
    mut cmd: Command,
    service: &str,
    logs: &Arc<LogBook>,
) -> std::io::Result<ServiceProcess> {
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    tie_to_parent(&mut cmd);
    let mut child = cmd.spawn()?;
    let pid = child.id().unwrap_or(0);
    #[cfg(windows)]
    job::assign(&child);
    if let Some(stdout) = child.stdout.take() {
        pump(stdout, service.to_string(), logs.clone());
    }
    if let Some(stderr) = child.stderr.take() {
        pump(stderr, service.to_string(), logs.clone());
    }
    Ok(ServiceProcess { child, pid })
}

fn pump(stream: impl AsyncRead + Unpin + Send + 'static, service: String, logs: Arc<LogBook>) {
    tokio::spawn(async move {
        let mut lines = BufReader::new(stream).lines();
        loop {
            match lines.next_line().await {
                Ok(Some(line)) => logs.append(&service, &line),
                Ok(None) => break,
                // invalid UTF-8: keep the service running, note it once
                Err(_) => {
                    logs.append(&service, "[supervisor] unreadable output skipped");
                    break;
                }
            }
        }
    });
}

/// Outcome of a short-lived tool run (initdb, pg_ctl, alembic, ...).
pub struct Finished {
    pub success: bool,
    pub code: Option<i32>,
    pub stdout: String,
}

/// Run a tool to completion with a timeout, logging its output under
/// `service`. Its stdout is also returned (for psql queries).
pub async fn run(
    mut cmd: Command,
    service: &str,
    logs: &Arc<LogBook>,
    timeout: Duration,
) -> std::io::Result<Finished> {
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    tie_to_parent(&mut cmd);
    let child = cmd.spawn()?;
    #[cfg(windows)]
    job::assign(&child);
    let output = match tokio::time::timeout(timeout, child.wait_with_output()).await {
        Ok(result) => result?,
        Err(_) => {
            return Err(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                format!("timed out after {}s", timeout.as_secs()),
            ))
        }
    };
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    for line in stdout
        .lines()
        .chain(String::from_utf8_lossy(&output.stderr).lines())
    {
        if !line.trim().is_empty() {
            logs.append(service, line);
        }
    }
    Ok(Finished {
        success: output.status.success(),
        code: output.status.code(),
        stdout,
    })
}

/// Run a tool that starts a daemon and returns (`pg_ctl start`). Its output
/// is discarded: on Windows the daemon inherits the tool's handles, and a
/// pipe kept open by the daemon would never reach end-of-file.
pub async fn run_detached(mut cmd: Command, timeout: Duration) -> std::io::Result<Finished> {
    cmd.stdout(Stdio::null()).stderr(Stdio::null());
    let mut child = cmd.spawn()?;
    let status = match tokio::time::timeout(timeout, child.wait()).await {
        Ok(status) => status?,
        Err(_) => {
            let _ = child.kill().await;
            return Err(std::io::Error::new(
                std::io::ErrorKind::TimedOut,
                format!("timed out after {}s", timeout.as_secs()),
            ));
        }
    };
    Ok(Finished {
        success: status.success(),
        code: status.code(),
        stdout: String::new(),
    })
}

/// Stop a service: SIGTERM and a grace period on Unix, then a hard kill.
/// Windows has no signal a GUI app can send to a console-less child, so the
/// process is terminated; the controller's leases (ADR-034..036) fail any
/// run, task or ingestion it left behind.
pub async fn terminate(process: &mut ServiceProcess, grace: Duration) {
    #[cfg(unix)]
    {
        if process.pid != 0 {
            unsafe {
                libc::kill(process.pid as libc::pid_t, libc::SIGTERM);
            }
            if tokio::time::timeout(grace, process.child.wait())
                .await
                .is_ok()
            {
                return;
            }
        }
    }
    #[cfg(not(unix))]
    let _ = grace;
    let _ = process.child.kill().await;
    let _ = process.child.wait().await;
}

/// Is a process with this id still alive?
pub fn pid_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    #[cfg(unix)]
    {
        // signal 0 checks existence and permission without delivering one
        unsafe { libc::kill(pid as libc::pid_t, 0) == 0 }
    }
    #[cfg(windows)]
    {
        windows_pid_alive(pid)
    }
}

#[cfg(windows)]
fn windows_pid_alive(pid: u32) -> bool {
    use windows_sys::Win32::Foundation::{CloseHandle, STILL_ACTIVE};
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return false;
        }
        let mut code = 0u32;
        let ok = GetExitCodeProcess(handle, &mut code);
        CloseHandle(handle);
        ok != 0 && code == STILL_ACTIVE as u32
    }
}

/// Kill a leftover process from an earlier run, but only if its executable
/// is the one we would have started (a recycled pid belongs to someone else).
pub fn kill_stale(pid: u32, expected_exe: &Path) {
    if !pid_alive(pid) || !exe_matches(pid, expected_exe) {
        return;
    }
    #[cfg(unix)]
    unsafe {
        libc::kill(pid as libc::pid_t, libc::SIGKILL);
    }
    #[cfg(windows)]
    unsafe {
        use windows_sys::Win32::Foundation::CloseHandle;
        use windows_sys::Win32::System::Threading::{
            OpenProcess, TerminateProcess, PROCESS_TERMINATE,
        };
        let handle = OpenProcess(PROCESS_TERMINATE, 0, pid);
        if !handle.is_null() {
            TerminateProcess(handle, 1);
            CloseHandle(handle);
        }
    }
}

fn exe_matches(pid: u32, expected: &Path) -> bool {
    let Some(actual) = process_exe(pid) else {
        return false;
    };
    let normalize = |p: &Path| {
        std::fs::canonicalize(p)
            .unwrap_or_else(|_| p.to_path_buf())
            .to_string_lossy()
            .to_lowercase()
    };
    normalize(&actual) == normalize(expected)
}

#[cfg(target_os = "linux")]
fn process_exe(pid: u32) -> Option<PathBuf> {
    std::fs::read_link(format!("/proc/{pid}/exe")).ok()
}

#[cfg(target_os = "macos")]
fn process_exe(pid: u32) -> Option<PathBuf> {
    // `ps` prints the command line, whose first word is the executable
    let output = std::process::Command::new("/bin/ps")
        .args(["-o", "comm=", "-p", &pid.to_string()])
        .output()
        .ok()?;
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    (!text.is_empty()).then(|| PathBuf::from(text))
}

#[cfg(windows)]
fn process_exe(pid: u32) -> Option<PathBuf> {
    use std::os::windows::ffi::OsStringExt;
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::Threading::{
        OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
    };
    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return None;
        }
        let mut buf = [0u16; 1024];
        let mut len = buf.len() as u32;
        let ok = QueryFullProcessImageNameW(handle, 0, buf.as_mut_ptr(), &mut len);
        CloseHandle(handle);
        (ok != 0).then(|| PathBuf::from(OsString::from_wide(&buf[..len as usize])))
    }
}

#[cfg(not(any(unix, windows)))]
fn process_exe(_pid: u32) -> Option<PathBuf> {
    None
}

#[cfg(windows)]
mod job {
    //! One Job Object for the app, created on first use and never closed by
    //! us: when the app exits (or crashes) Windows closes it and kills every
    //! process assigned to it.
    use std::sync::OnceLock;

    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    struct Job(HANDLE);
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    static JOB: OnceLock<Option<Job>> = OnceLock::new();

    fn job() -> Option<HANDLE> {
        JOB.get_or_init(|| unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() {
                return None;
            }
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let ok = SetInformationJobObject(
                handle,
                JobObjectExtendedLimitInformation,
                &info as *const _ as *const core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            );
            (ok != 0).then_some(Job(handle))
        })
        .as_ref()
        .map(|job| job.0)
    }

    pub fn assign(child: &tokio::process::Child) {
        if let (Some(job), Some(raw)) = (job(), child.raw_handle()) {
            unsafe {
                AssignProcessToJobObject(job, raw as HANDLE);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_environment_is_allowlisted() {
        std::env::set_var("PYTHONPATH", "/evil");
        std::env::set_var("PGPASSWORD", "leak");
        let env = base_env();
        assert!(env
            .iter()
            .all(|(k, _)| k != "PYTHONPATH" && k != "PGPASSWORD"));
    }

    #[test]
    fn extra_variables_replace_inherited_ones() {
        let base: Env = vec![("TEMP".into(), "a".into()), ("HOME".into(), "h".into())];
        let env = with(&base, &[("TEMP", "b".to_string())]);
        assert_eq!(
            env,
            vec![
                (OsString::from("HOME"), OsString::from("h")),
                (OsString::from("TEMP"), OsString::from("b"))
            ]
        );
    }

    #[test]
    fn our_own_pid_is_alive_and_matches_our_exe() {
        let pid = std::process::id();
        assert!(pid_alive(pid));
        let exe = std::env::current_exe().unwrap();
        assert!(exe_matches(pid, &exe));
        assert!(!exe_matches(pid, Path::new("/definitely/not/this")));
    }
}
