#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use rand::{rngs::OsRng, RngCore};
use std::{
    ffi::OsString,
    io::{self, BufRead, BufReader},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{mpsc, Arc, Mutex},
    thread,
    time::Duration,
};
use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

const BACKEND_HANDOFF_PREFIX: &str = "NOOFY_BACKEND_API_BASE_URL=";

struct BackendRuntime {
    child: Child,
    api_base_url: String,
    api_token: String,
}

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct FrontendRuntimeConfig {
    api_base_url: String,
    api_token: String,
}

#[tauri::command]
fn noofy_runtime_config(config: tauri::State<'_, FrontendRuntimeConfig>) -> FrontendRuntimeConfig {
    config.inner().clone()
}

#[tauri::command]
fn open_external_url(url: String) -> Result<(), String> {
    let parsed = url::Url::parse(&url).map_err(|e| format!("invalid URL: {e}"))?;
    let scheme = parsed.scheme();
    if scheme != "https" && scheme != "http" {
        return Err(format!("scheme '{scheme}' is not allowed"));
    }
    open::that(url).map_err(|e| format!("failed to open URL: {e}"))
}

fn main() {
    let backend_process: Arc<Mutex<Option<Child>>> = Arc::new(Mutex::new(None));
    let backend_for_setup = Arc::clone(&backend_process);
    let backend_for_window = Arc::clone(&backend_process);
    let backend_for_exit = Arc::clone(&backend_process);

    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![noofy_runtime_config, open_external_url])
        .setup(move |app| {
            let runtime = start_backend()?;
            let init_script = runtime_config_script(&runtime.api_base_url, &runtime.api_token)?;
            app.manage(FrontendRuntimeConfig {
                api_base_url: runtime.api_base_url.clone(),
                api_token: runtime.api_token.clone(),
            });

            let mut backend_guard = backend_for_setup
                .lock()
                .map_err(|_| io::Error::new(io::ErrorKind::Other, "backend process lock is poisoned"))?;
            *backend_guard = Some(runtime.child);

            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Noofy")
                .inner_size(1180.0, 780.0)
                .min_inner_size(940.0, 640.0)
                .initialization_script(init_script)
                .disable_drag_drop_handler()
                .build()?;

            Ok(())
        })
        .on_window_event(move |_window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                terminate_backend(&backend_for_window);
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build Noofy Tauri application")
        .run(move |_app_handle, event| {
            if let RunEvent::ExitRequested { .. } = event {
                terminate_backend(&backend_for_exit);
            }
        });
}

fn start_backend() -> Result<BackendRuntime, Box<dyn std::error::Error>> {
    let api_token = generate_api_token();
    let backend_dir = backend_dir()?;
    let python = backend_python(&backend_dir);

    let mut child = Command::new(python)
        .arg("-m")
        .arg("app")
        .arg("--port")
        .arg("0")
        .env("NOOFY_API_TOKEN", &api_token)
        .current_dir(backend_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| io::Error::new(io::ErrorKind::Other, "backend stdout pipe was not available"))?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| io::Error::new(io::ErrorKind::Other, "backend stderr pipe was not available"))?;

    let (handoff_sender, handoff_receiver) = mpsc::channel();
    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().map_while(Result::ok) {
            if line.starts_with(BACKEND_HANDOFF_PREFIX) {
                let _ = handoff_sender.send(line);
            } else {
                eprintln!("[noofy-backend] {line}");
            }
        }
    });

    thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().map_while(Result::ok) {
            eprintln!("[noofy-backend] {line}");
        }
    });

    let handoff = match handoff_receiver.recv_timeout(Duration::from_secs(20)) {
        Ok(line) => line,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(Box::new(error));
        }
    };
    let api_base_url = handoff
        .strip_prefix(BACKEND_HANDOFF_PREFIX)
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::Other,
                "backend handoff did not include the API base URL",
            )
        })?
        .trim()
        .to_string();

    Ok(BackendRuntime {
        child,
        api_base_url,
        api_token,
    })
}

fn backend_dir() -> Result<PathBuf, Box<dyn std::error::Error>> {
    if let Ok(path) = std::env::var("NOOFY_BACKEND_DIR") {
        return Ok(PathBuf::from(path));
    }

    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let project_root = manifest_dir
        .parent()
        .and_then(|frontend_dir| frontend_dir.parent())
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::Other,
                "could not resolve project root from Tauri manifest directory",
            )
        })?;

    Ok(project_root.join("backend"))
}

fn backend_python(backend_dir: &Path) -> OsString {
    if let Ok(path) = std::env::var("NOOFY_BACKEND_PYTHON") {
        return OsString::from(path);
    }

    let venv_python = if cfg!(windows) {
        backend_dir.join(".venv").join("Scripts").join("python.exe")
    } else {
        backend_dir.join(".venv").join("bin").join("python")
    };
    if venv_python.exists() {
        return venv_python.into_os_string();
    }

    if cfg!(target_os = "macos") {
        let homebrew_python = PathBuf::from("/usr/local/opt/python@3.11/bin/python3.11");
        if homebrew_python.exists() {
            return homebrew_python.into_os_string();
        }
    }

    OsString::from("python3")
}

fn generate_api_token() -> String {
    let mut bytes = [0_u8; 32];
    OsRng.fill_bytes(&mut bytes);
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn runtime_config_script(
    api_base_url: &str,
    api_token: &str,
) -> Result<String, Box<dyn std::error::Error>> {
    let config = serde_json::json!({
        "apiBaseUrl": api_base_url,
        "apiToken": api_token,
    });
    Ok(format!("window.__NOOFY_RUNTIME_CONFIG__ = {config};"))
}

fn terminate_backend(process: &Arc<Mutex<Option<Child>>>) {
    let Ok(mut guard) = process.lock() else {
        return;
    };

    if let Some(mut child) = guard.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
}
