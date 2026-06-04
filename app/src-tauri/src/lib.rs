// Loreweave Studio (loom) — Tauri 2 desktop shell.
//
// M0 responsibilities (kb-loom-p0.md §3, R5/R74/R101):
//   * single-instance: a second launch focuses the existing window (R74).
//   * spawn the Python orchestrator as a sidecar child process.
//   * read the orchestrator's READY line (url + token) — the M0 handshake (R101).
//   * kill the sidecar when the app exits, so it never lingers holding the port
//     (review fix / P0-15 lifecycle): a leaked orchestrator would block the next
//     launch's spawn from binding 127.0.0.1:8765.

use std::io::{BufRead, BufReader};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};

use tauri::{Manager, RunEvent};

/// The orchestrator endpoint learned from its READY stdout line.
#[derive(Default, Clone, serde::Serialize)]
pub struct OrchestratorEndpoint {
    pub url: String,
    pub token: String,
}

/// Shared handle to the spawned sidecar so we can kill it on exit.
type ChildSlot = Arc<Mutex<Option<Child>>>;

struct AppState {
    orchestrator: Arc<Mutex<OrchestratorEndpoint>>,
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
fn spawn_orchestrator(app: &tauri::AppHandle, child_slot: ChildSlot, endpoint: Arc<Mutex<OrchestratorEndpoint>>) {
    let python = resolve_python();
    // cwd must be the app-repo root (it holds the `orchestrator/` package), i.e.
    // two levels up from src-tauri/ (src-tauri -> app -> <app repo root>). The
    // built exe should set LOOM_APP_REPO absolutely.
    let cwd = std::env::var("LOOM_APP_REPO").unwrap_or_else(|_| "../..".into());

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
        let app = app.clone();
        std::thread::spawn(move || {
            let reader = BufReader::new(stdout);
            for line in reader.lines().map_while(Result::ok) {
                if let Some(rest) = line.strip_prefix("LOOM_ORCH_READY ") {
                    let (mut url, mut token) = (String::new(), String::new());
                    for kv in rest.split_whitespace() {
                        if let Some(v) = kv.strip_prefix("url=") { url = v.into(); }
                        if let Some(v) = kv.strip_prefix("token=") { token = v.into(); }
                    }
                    *endpoint.lock().unwrap() =
                        OrchestratorEndpoint { url: url.clone(), token: token.clone() };
                    // Inject the loopback URL + token into the webview so the UI can send
                    // X-Loom-Token on /generate (review #1). serde_json-encoded for safety.
                    if let Some(win) = app.get_webview_window("main") {
                        let script = format!(
                            "window.__LOOM_ORCH_URL__={};window.__LOOM_TOKEN__={};",
                            serde_json::to_string(&url).unwrap_or_else(|_| "\"\"".into()),
                            serde_json::to_string(&token).unwrap_or_else(|_| "\"\"".into()),
                        );
                        let _ = win.eval(&script);
                    }
                    println!("[loom] orchestrator ready at {url}");
                }
            }
        });
    }

    // Keep the handle so we can kill it on app exit (no more mem::forget leak).
    *child_slot.lock().unwrap() = Some(child);
}

fn kill_orchestrator(child_slot: &ChildSlot) {
    if let Some(mut child) = child_slot.lock().unwrap().take() {
        let _ = child.kill();
        let _ = child.wait();
        println!("[loom] orchestrator sidecar terminated");
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let child_slot: ChildSlot = Arc::new(Mutex::new(None));
    let endpoint: Arc<Mutex<OrchestratorEndpoint>> = Arc::new(Mutex::new(OrchestratorEndpoint::default()));

    let setup_child = child_slot.clone();
    let setup_endpoint = endpoint.clone();

    let app = tauri::Builder::default()
        // Single instance: focus the existing window on a second launch (R74).
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            if let Some(win) = app.get_webview_window("main") {
                let _ = win.set_focus();
            }
        }))
        .manage(AppState { orchestrator: endpoint.clone() })
        .invoke_handler(tauri::generate_handler![orchestrator_endpoint])
        .setup(move |app| {
            spawn_orchestrator(&app.handle(), setup_child.clone(), setup_endpoint.clone());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Loreweave Studio");

    // Kill the sidecar when the app exits so it doesn't linger on the port.
    let exit_child = child_slot.clone();
    app.run(move |_app_handle, event| {
        if let RunEvent::Exit = event {
            kill_orchestrator(&exit_child);
        }
    });
}
