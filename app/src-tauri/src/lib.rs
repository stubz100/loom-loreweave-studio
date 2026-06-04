// Loreweave Studio (loom) — Tauri 2 desktop shell.
//
// M0 responsibilities (kb-loom-p0.md §3, R5/R74/R101):
//   * single-instance: a second launch focuses the existing window (R74).
//   * spawn the Python orchestrator as a sidecar child process.
//   * read the orchestrator's READY line (url + token) — the M0 handshake (R101).
//
// NOTE: this layer is UNBUILT — the dev box has no Rust toolchain yet. The code
// is written build-ready for `cargo`/`tauri` once `rustup` is installed. Until
// then, dev runs the orchestrator + `npm run dev` manually (the React shell
// probes /health directly).

use std::io::{BufRead, BufReader};
use std::process::{Command, Stdio};
use std::sync::Mutex;

use tauri::Manager;

/// The orchestrator endpoint learned from its READY stdout line.
#[derive(Default, Clone, serde::Serialize)]
pub struct OrchestratorEndpoint {
    pub url: String,
    pub token: String,
}

struct AppState {
    orchestrator: Mutex<OrchestratorEndpoint>,
}

/// Command the UI calls to learn where the orchestrator is + its token.
#[tauri::command]
fn orchestrator_endpoint(state: tauri::State<AppState>) -> OrchestratorEndpoint {
    state.orchestrator.lock().unwrap().clone()
}

/// Resolve the python interpreter for the orchestrator sidecar (R103).
fn resolve_python() -> String {
    std::env::var("LOOM_VENV_PYTHON").unwrap_or_else(|_| "python".into())
}

/// Spawn `python -m orchestrator.main` and capture its READY line.
/// cwd must be the app-repo root (it holds the `orchestrator/` package).
fn spawn_orchestrator(app: &tauri::AppHandle) {
    let python = resolve_python();
    // Dev cwd: the app-repo root. Overridable so packaging can point elsewhere.
    let cwd = std::env::var("LOOM_APP_REPO")
        .unwrap_or_else(|_| "..".into()); // app/src-tauri -> app ; adjust at runtime as needed

    let child = Command::new(&python)
        .args(["-m", "orchestrator.main"])
        .current_dir(&cwd)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn();

    let mut child = match child {
        Ok(c) => c,
        Err(e) => {
            eprintln!("[loom] failed to spawn orchestrator ({python}): {e}");
            return;
        }
    };

    if let Some(stdout) = child.stdout.take() {
        let handle = app.clone();
        std::thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                if let Some(rest) = line.strip_prefix("LOOM_ORCH_READY ") {
                    let (mut url, mut token) = (String::new(), String::new());
                    for kv in rest.split_whitespace() {
                        if let Some(v) = kv.strip_prefix("url=") { url = v.into(); }
                        if let Some(v) = kv.strip_prefix("token=") { token = v.into(); }
                    }
                    if let Some(state) = handle.try_state::<AppState>() {
                        *state.orchestrator.lock().unwrap() =
                            OrchestratorEndpoint { url: url.clone(), token };
                    }
                    println!("[loom] orchestrator ready at {url}");
                }
            }
        });
    }

    // Keep the child handle alive for the process lifetime (P0-15: lifecycle/
    // crash-recovery hardening lands with the queue work).
    std::mem::forget(child);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // Single instance: focus the existing window on a second launch (R74).
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.set_focus();
            }
        }))
        .manage(AppState {
            orchestrator: Mutex::new(OrchestratorEndpoint::default()),
        })
        .invoke_handler(tauri::generate_handler![orchestrator_endpoint])
        .setup(|app| {
            spawn_orchestrator(&app.handle());
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Loreweave Studio");
}
