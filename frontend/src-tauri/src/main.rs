#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use rand::{rngs::OsRng, RngCore};
#[cfg(unix)]
use std::time::Instant;
use std::{
    collections::HashMap,
    ffi::OsString,
    fs,
    io::{self, BufRead, BufReader},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{mpsc, Arc, Mutex},
    thread,
    time::Duration,
};
use tauri::{Emitter, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent};

const BACKEND_HANDOFF_PREFIX: &str = "NOOFY_BACKEND_API_BASE_URL=";
const NOOFY_RUNTIME_RESOURCE_DIR: &str = "noofy-runtime";
const RUNTIME_MANIFEST_NAME: &str = "runtime-manifest.json";
const OPEN_WORKFLOW_FILE_EVENT: &str = "noofy-open-workflow-file";
const MAX_NOOFY_FILE_BYTES: u64 = 512 * 1024 * 1024;

struct BackendRuntime {
    process: BackendProcess,
    api_base_url: String,
    api_token: String,
}

struct BackendProcess {
    child: Option<Child>,
    lease_path: Option<PathBuf>,
    #[cfg(windows)]
    job_handle: isize,
}

impl BackendProcess {
    fn new(child: Child, lease_path: Option<PathBuf>) -> io::Result<Self> {
        #[cfg(windows)]
        let (child, job_handle) = {
            let mut child = child;
            let job_handle = match create_kill_on_close_job(&child) {
                Ok(handle) => handle,
                Err(error) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(error);
                }
            };
            (child, job_handle)
        };
        Ok(Self {
            child: Some(child),
            lease_path,
            #[cfg(windows)]
            job_handle,
        })
    }

    fn child_mut(&mut self) -> io::Result<&mut Child> {
        self.child
            .as_mut()
            .ok_or_else(|| io::Error::new(io::ErrorKind::Other, "backend process is unavailable"))
    }

    fn shutdown(&mut self) {
        if let Some(mut child) = self.child.take() {
            terminate_child_tree(&mut child);
        }
        if let Some(path) = &self.lease_path {
            let _ = fs::remove_file(path);
        }
        #[cfg(windows)]
        close_job_handle(&mut self.job_handle);
    }
}

impl Drop for BackendProcess {
    fn drop(&mut self) {
        self.shutdown();
    }
}

#[derive(Clone, Debug)]
struct PendingNoofyOpenFile(Arc<Mutex<Option<NoofyOpenFile>>>);

#[derive(Clone, Debug)]
struct BackendLaunchSpec {
    program: OsString,
    args: Vec<OsString>,
    current_dir: PathBuf,
    env: Vec<(String, OsString)>,
    remove_env: Vec<String>,
}

#[derive(Clone, Debug)]
struct BackendLaunchContext {
    env: HashMap<String, OsString>,
    manifest_dir: PathBuf,
    current_exe: PathBuf,
    resource_dir: Option<PathBuf>,
    app_data_dir: Option<PathBuf>,
    packaged_mode: bool,
}

#[derive(Clone, Debug)]
struct PackagedRuntimeLayout {
    root_dir: PathBuf,
    backend_dir: PathBuf,
    comfyui_dir: PathBuf,
    workflows_dir: PathBuf,
    python_executable: Option<PathBuf>,
    uv_executable: Option<PathBuf>,
    backend_sidecar: Option<PathBuf>,
}

#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct FrontendRuntimeConfig {
    api_base_url: String,
    api_token: String,
}

#[derive(Clone, Debug, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct NoofyOpenFile {
    path: String,
    filename: String,
}

#[derive(Clone, Debug, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct NativeNoofyFile {
    path: String,
    filename: String,
    bytes: Vec<u8>,
}

#[derive(serde::Deserialize, serde::Serialize)]
struct BackendProcessLease {
    schema_version: u32,
    pid: u32,
    #[serde(default)]
    process_identity: Option<String>,
    program: String,
    current_dir: String,
}

#[tauri::command]
fn noofy_runtime_config(config: tauri::State<'_, FrontendRuntimeConfig>) -> FrontendRuntimeConfig {
    config.inner().clone()
}

#[tauri::command]
fn pending_noofy_open_file(
    pending: tauri::State<'_, PendingNoofyOpenFile>,
) -> Result<Option<NoofyOpenFile>, String> {
    let mut guard = pending
        .0
        .lock()
        .map_err(|_| "pending workflow file lock is poisoned".to_string())?;
    Ok(guard.take())
}

#[tauri::command]
fn read_noofy_file(path: String) -> Result<NativeNoofyFile, String> {
    let path = PathBuf::from(path);
    let filename = validate_noofy_file_path(&path)?;
    let bytes = fs::read(&path).map_err(|e| format!("failed to read workflow package: {e}"))?;
    Ok(NativeNoofyFile {
        path: path.to_string_lossy().to_string(),
        filename,
        bytes,
    })
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

#[tauri::command]
fn select_folder() -> Result<Option<String>, String> {
    Ok(rfd::FileDialog::new()
        .pick_folder()
        .map(|path| path.to_string_lossy().to_string()))
}

#[tauri::command]
fn select_model_files() -> Result<Vec<String>, String> {
    Ok(rfd::FileDialog::new()
        .add_filter(
            "Model files",
            &["safetensors", "ckpt", "pt", "pth", "bin", "gguf", "onnx"],
        )
        .pick_files()
        .unwrap_or_default()
        .into_iter()
        .map(|path| path.to_string_lossy().to_string())
        .collect())
}

#[tauri::command]
fn select_save_file(default_filename: String) -> Result<Option<String>, String> {
    let filename = safe_dialog_filename(&default_filename);
    let extension = Path::new(&filename)
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    let mut dialog = rfd::FileDialog::new().set_file_name(filename.clone());

    dialog = match extension.as_str() {
        "noofy" => dialog.add_filter("Noofy workflow package", &["noofy"]),
        "json" => dialog.add_filter("JSON file", &["json"]),
        _ => dialog,
    };

    Ok(dialog
        .save_file()
        .map(|path| path.to_string_lossy().to_string()))
}

#[tauri::command]
fn save_binary_file(path: String, bytes: Vec<u8>) -> Result<String, String> {
    let target = PathBuf::from(path);
    let Some(parent) = target.parent() else {
        return Err("save location has no parent folder".to_string());
    };
    if !parent.is_dir() {
        return Err("save folder does not exist".to_string());
    }
    fs::write(&target, bytes).map_err(|e| format!("failed to save file: {e}"))?;
    Ok(target.to_string_lossy().to_string())
}

fn safe_dialog_filename(filename: &str) -> String {
    let name = Path::new(filename)
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("workflow.noofy")
        .trim();
    if name.is_empty() {
        "workflow.noofy".to_string()
    } else {
        name.to_string()
    }
}

#[tauri::command]
fn open_folder(path: String) -> Result<(), String> {
    let folder = PathBuf::from(path);
    if !folder.is_dir() {
        return Err("folder does not exist".to_string());
    }
    open::that(folder).map_err(|e| format!("failed to open folder: {e}"))
}

fn main() {
    let backend_process: Arc<Mutex<Option<BackendProcess>>> = Arc::new(Mutex::new(None));
    let backend_for_setup = Arc::clone(&backend_process);
    let backend_for_window = Arc::clone(&backend_process);
    let backend_for_exit = Arc::clone(&backend_process);
    let pending_open_file = PendingNoofyOpenFile(Arc::new(Mutex::new(first_noofy_file_from_args(
        std::env::args_os().skip(1),
        None,
    ))));
    let pending_for_single_instance = pending_open_file.clone();
    #[cfg(any(target_os = "macos", target_os = "ios"))]
    let pending_for_run_event = pending_open_file.clone();

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(move |app, args, cwd| {
            let cwd = PathBuf::from(cwd);
            if let Some(file) = first_noofy_file_from_strings(args, Some(&cwd)) {
                handle_noofy_open_request(app, &pending_for_single_instance, file);
            } else if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.show();
                let _ = window.set_focus();
            }
        }))
        .invoke_handler(tauri::generate_handler![
            noofy_runtime_config,
            pending_noofy_open_file,
            read_noofy_file,
            open_external_url,
            select_folder,
            select_model_files,
            select_save_file,
            save_binary_file,
            open_folder
        ])
        .setup(move |app| {
            let runtime = start_backend(app)?;
            let init_script = runtime_config_script(&runtime.api_base_url, &runtime.api_token)?;
            app.manage(FrontendRuntimeConfig {
                api_base_url: runtime.api_base_url.clone(),
                api_token: runtime.api_token.clone(),
            });
            app.manage(pending_open_file.clone());

            let mut backend_guard = backend_for_setup.lock().map_err(|_| {
                io::Error::new(io::ErrorKind::Other, "backend process lock is poisoned")
            })?;
            *backend_guard = Some(runtime.process);

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
        .run(move |app_handle, event| {
            #[cfg(not(any(target_os = "macos", target_os = "ios")))]
            let _ = app_handle;
            match event {
                #[cfg(any(target_os = "macos", target_os = "ios"))]
                RunEvent::Opened { urls } => {
                    if let Some(file) = first_noofy_file_from_urls(urls) {
                        handle_noofy_open_request(app_handle, &pending_for_run_event, file);
                    }
                }
                RunEvent::ExitRequested { .. } => {
                    terminate_backend(&backend_for_exit);
                }
                _ => {}
            }
        });
}

fn handle_noofy_open_request(
    app: &tauri::AppHandle,
    pending: &PendingNoofyOpenFile,
    file: NoofyOpenFile,
) {
    if let Ok(mut guard) = pending.0.lock() {
        *guard = Some(file.clone());
    }

    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        let _ = window.emit(OPEN_WORKFLOW_FILE_EVENT, file);
    }
}

fn first_noofy_file_from_args<I>(args: I, cwd: Option<&Path>) -> Option<NoofyOpenFile>
where
    I: IntoIterator<Item = OsString>,
{
    args.into_iter()
        .filter_map(|arg| arg.into_string().ok())
        .find_map(|arg| noofy_file_from_arg(&arg, cwd))
}

fn first_noofy_file_from_strings<I>(args: I, cwd: Option<&Path>) -> Option<NoofyOpenFile>
where
    I: IntoIterator<Item = String>,
{
    args.into_iter()
        .find_map(|arg| noofy_file_from_arg(&arg, cwd))
}

#[cfg(any(target_os = "macos", target_os = "ios"))]
fn first_noofy_file_from_urls(urls: Vec<url::Url>) -> Option<NoofyOpenFile> {
    urls.into_iter().find_map(|url| {
        let path = url.to_file_path().ok()?;
        noofy_open_file_from_path(path)
    })
}

fn noofy_file_from_arg(arg: &str, cwd: Option<&Path>) -> Option<NoofyOpenFile> {
    if arg.starts_with('-') {
        return None;
    }

    if let Ok(url) = url::Url::parse(arg) {
        if url.scheme() == "file" {
            return url.to_file_path().ok().and_then(noofy_open_file_from_path);
        }
    }

    let raw_path = PathBuf::from(arg);
    let path = if raw_path.is_absolute() {
        raw_path
    } else {
        cwd.map(|base| base.join(&raw_path)).unwrap_or(raw_path)
    };
    noofy_open_file_from_path(path)
}

fn noofy_open_file_from_path(path: PathBuf) -> Option<NoofyOpenFile> {
    let filename = validate_noofy_file_path(&path).ok()?;
    Some(NoofyOpenFile {
        path: path.to_string_lossy().to_string(),
        filename,
    })
}

fn validate_noofy_file_path(path: &Path) -> Result<String, String> {
    let filename = path
        .file_name()
        .and_then(|value| value.to_str())
        .ok_or_else(|| "workflow package path has no filename".to_string())?
        .to_string();
    let extension = path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or_default();
    if !extension.eq_ignore_ascii_case("noofy") {
        return Err("selected file is not a .noofy workflow package".to_string());
    }
    let metadata =
        fs::metadata(path).map_err(|e| format!("workflow package is unavailable: {e}"))?;
    if !metadata.is_file() {
        return Err("workflow package path is not a file".to_string());
    }
    if metadata.len() > MAX_NOOFY_FILE_BYTES {
        return Err("workflow package is too large to open from the desktop shell".to_string());
    }
    Ok(filename)
}

fn start_backend(app: &tauri::App) -> Result<BackendRuntime, Box<dyn std::error::Error>> {
    let api_token = generate_api_token();
    let context = BackendLaunchContext::from_app(app)?;
    let launch = backend_launch_spec(&context)?;
    let lease_path = context
        .app_data_dir
        .as_ref()
        .map(|path| path.join("launcher").join("backend-process.json"));
    if let Some(path) = &lease_path {
        recover_stale_backend(path, &launch)?;
    }

    let mut command = Command::new(&launch.program);
    command
        .args(&launch.args)
        .env("NOOFY_API_TOKEN", &api_token)
        .current_dir(&launch.current_dir)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for key in &launch.remove_env {
        command.env_remove(key);
    }
    for (key, value) in &launch.env {
        command.env(key, value);
    }
    configure_backend_process_tree(&mut command);

    let child = command.spawn()?;
    let mut process = BackendProcess::new(child, lease_path.clone())?;
    if let Some(path) = &lease_path {
        write_backend_process_lease(path, process.child_mut()?.id(), &launch)?;
    }

    let stdout = process.child_mut()?.stdout.take().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::Other,
            "backend stdout pipe was not available",
        )
    })?;
    let stderr = process.child_mut()?.stderr.take().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::Other,
            "backend stderr pipe was not available",
        )
    })?;

    let (handoff_sender, handoff_receiver) = mpsc::channel();
    let stdout_api_token = api_token.clone();
    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().map_while(Result::ok) {
            if line.starts_with(BACKEND_HANDOFF_PREFIX) {
                let _ = handoff_sender.send(line);
            } else {
                eprintln!(
                    "[noofy-backend] {}",
                    redact_backend_log_line(&line, &stdout_api_token)
                );
            }
        }
    });

    let stderr_api_token = api_token.clone();
    thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().map_while(Result::ok) {
            eprintln!(
                "[noofy-backend] {}",
                redact_backend_log_line(&line, &stderr_api_token)
            );
        }
    });

    let handoff = match handoff_receiver.recv_timeout(Duration::from_secs(20)) {
        Ok(line) => line,
        Err(error) => {
            process.shutdown();
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
        process,
        api_base_url,
        api_token,
    })
}

impl BackendLaunchContext {
    fn from_app(app: &tauri::App) -> io::Result<Self> {
        let env: HashMap<String, OsString> = std::env::vars_os()
            .filter_map(|(key, value)| key.into_string().ok().map(|key| (key, value)))
            .collect();
        let packaged_mode =
            !cfg!(debug_assertions) || env_flag(&env, "NOOFY_FORCE_PACKAGED_BACKEND");
        Ok(Self {
            env,
            manifest_dir: PathBuf::from(env!("CARGO_MANIFEST_DIR")),
            current_exe: std::env::current_exe()?,
            resource_dir: app.path().resource_dir().ok(),
            app_data_dir: app.path().app_data_dir().ok(),
            packaged_mode,
        })
    }
}

fn backend_launch_spec(context: &BackendLaunchContext) -> io::Result<BackendLaunchSpec> {
    let developer_overrides_enabled = developer_backend_overrides_enabled(context);
    if (!context.packaged_mode || developer_overrides_enabled)
        && env_path(context, "NOOFY_BACKEND_SIDECAR").is_some()
    {
        let sidecar = env_path(context, "NOOFY_BACKEND_SIDECAR").expect("checked above");
        let mut spec = BackendLaunchSpec {
            program: sidecar.into_os_string(),
            args: backend_sidecar_args(),
            current_dir: packaged_runtime_layout(context).root_dir,
            env: Vec::new(),
            remove_env: if context.packaged_mode && !developer_overrides_enabled {
                packaged_env_removals()
            } else {
                Vec::new()
            },
        };
        apply_backend_environment(
            context,
            &mut spec,
            context.packaged_mode && !developer_overrides_enabled,
        )?;
        return Ok(spec);
    }

    if context.packaged_mode && !developer_overrides_enabled {
        return packaged_backend_launch_spec(context);
    }

    source_backend_launch_spec(context)
}

fn packaged_backend_launch_spec(context: &BackendLaunchContext) -> io::Result<BackendLaunchSpec> {
    let expected_target = supported_packaged_runtime_target()?;
    let layout = packaged_runtime_layout(context);
    let python = layout.python_executable.clone().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::NotFound,
            format!(
                "Packaged Noofy is missing its bundled Python runtime. Expected a Python executable under {}.",
                layout.root_dir.join("python").display()
            ),
        )
    })?;
    let mut spec = if let Some(sidecar) = layout.backend_sidecar.clone() {
        BackendLaunchSpec {
            program: sidecar.into_os_string(),
            args: backend_sidecar_args(),
            current_dir: layout.root_dir.clone(),
            env: Vec::new(),
            remove_env: packaged_env_removals(),
        }
    } else {
        if !layout.backend_dir.exists() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!(
                    "Packaged Noofy is missing its backend application files. Expected {}.",
                    layout.backend_dir.display()
                ),
            ));
        }
        BackendLaunchSpec {
            program: python.into_os_string(),
            args: python_backend_args(),
            current_dir: layout.backend_dir.clone(),
            env: Vec::new(),
            remove_env: packaged_env_removals(),
        }
    };
    apply_backend_environment(context, &mut spec, true)?;
    validate_packaged_runtime_layout(&layout, expected_target)?;
    Ok(spec)
}

fn source_backend_launch_spec(context: &BackendLaunchContext) -> io::Result<BackendLaunchSpec> {
    let backend_dir = backend_dir(context)?;
    let python = backend_python(context, &backend_dir);
    let mut spec = BackendLaunchSpec {
        program: python,
        args: python_backend_args(),
        current_dir: backend_dir,
        env: Vec::new(),
        remove_env: Vec::new(),
    };
    apply_backend_environment(context, &mut spec, false)?;
    Ok(spec)
}

fn backend_sidecar_args() -> Vec<OsString> {
    vec![OsString::from("--port"), OsString::from("0")]
}

fn python_backend_args() -> Vec<OsString> {
    vec![
        OsString::from("-m"),
        OsString::from("app"),
        OsString::from("--port"),
        OsString::from("0"),
    ]
}

fn backend_dir(context: &BackendLaunchContext) -> io::Result<PathBuf> {
    if let Some(path) = env_path(context, "NOOFY_BACKEND_DIR") {
        return Ok(path);
    }

    let project_root = context
        .manifest_dir
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

fn backend_python(context: &BackendLaunchContext, backend_dir: &Path) -> OsString {
    if let Some(path) = env_path(context, "NOOFY_BACKEND_PYTHON") {
        return path.into_os_string();
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

fn packaged_runtime_layout(context: &BackendLaunchContext) -> PackagedRuntimeLayout {
    let root_dir = packaged_runtime_root(context);
    packaged_runtime_layout_for_root(context, root_dir)
}

fn packaged_runtime_layout_for_root(
    context: &BackendLaunchContext,
    root_dir: PathBuf,
) -> PackagedRuntimeLayout {
    let backend_dir = root_dir.join("backend");
    let comfyui_dir = root_dir.join("comfyui");
    let workflows_dir = backend_dir.join("app").join("workflows").join("packages");
    PackagedRuntimeLayout {
        python_executable: first_existing(packaged_python_candidates(&root_dir)),
        uv_executable: first_existing(packaged_uv_candidates(&root_dir)),
        backend_sidecar: first_existing(packaged_backend_sidecar_candidates(context, &root_dir)),
        root_dir,
        backend_dir,
        comfyui_dir,
        workflows_dir,
    }
}

fn packaged_runtime_root(context: &BackendLaunchContext) -> PathBuf {
    if context.packaged_mode && !developer_backend_overrides_enabled(context) {
        if let Some(active_runtime) = active_noofy_runtime_root(context) {
            return active_runtime;
        }
    }
    if (!context.packaged_mode || developer_backend_overrides_enabled(context))
        && env_path(context, "NOOFY_PACKAGED_RUNTIME_DIR").is_some()
    {
        let path = env_path(context, "NOOFY_PACKAGED_RUNTIME_DIR").expect("checked above");
        return path;
    }
    if let Some(resource_dir) = &context.resource_dir {
        return resource_dir.join(NOOFY_RUNTIME_RESOURCE_DIR);
    }
    if let Some(exe_dir) = context.current_exe.parent() {
        return exe_dir.join(NOOFY_RUNTIME_RESOURCE_DIR);
    }
    PathBuf::from(NOOFY_RUNTIME_RESOURCE_DIR)
}

fn active_noofy_runtime_root(context: &BackendLaunchContext) -> Option<PathBuf> {
    let data_dir = env_path(context, "NOOFY_DATA_DIR").or_else(|| context.app_data_dir.clone())?;
    let active_file = data_dir
        .join("runtime-store")
        .join("noofy-runtime")
        .join("active-runtime.json");
    let payload = fs::read_to_string(active_file).ok()?;
    let json: serde_json::Value = serde_json::from_str(&payload).ok()?;
    let runtime_path = json
        .get("runtime")
        .and_then(|value| value.get("runtime_path"))
        .and_then(|value| value.as_str())?;
    let runtime_root = PathBuf::from(runtime_path);
    let allowed_root = data_dir
        .join("runtime-store")
        .join("noofy-runtime")
        .join("runtimes");
    if !path_inside(&runtime_root, &allowed_root) {
        return None;
    }
    let layout = packaged_runtime_layout_for_root(context, runtime_root.clone());
    validate_packaged_runtime_layout(&layout, supported_packaged_runtime_target().ok()?).ok()?;
    Some(runtime_root)
}

fn packaged_python_candidates(root_dir: &Path) -> Vec<PathBuf> {
    let python_dir = root_dir.join("python");
    if cfg!(windows) {
        vec![
            python_dir.join("python.exe"),
            python_dir.join("Scripts").join("python.exe"),
            root_dir.join("backend-python").join("python.exe"),
            root_dir
                .join("backend-python")
                .join("Scripts")
                .join("python.exe"),
        ]
    } else {
        vec![
            python_dir.join("bin").join("python3"),
            python_dir.join("bin").join("python"),
            root_dir.join("backend-python").join("bin").join("python3"),
            root_dir.join("backend-python").join("bin").join("python"),
        ]
    }
}

fn packaged_uv_candidates(root_dir: &Path) -> Vec<PathBuf> {
    let python_dir = root_dir.join("python");
    if cfg!(windows) {
        vec![
            python_dir.join("uv.exe"),
            python_dir.join("Scripts").join("uv.exe"),
            root_dir.join("tools").join("uv.exe"),
        ]
    } else {
        vec![
            python_dir.join("bin").join("uv"),
            root_dir.join("tools").join("uv"),
        ]
    }
}

fn packaged_backend_sidecar_candidates(
    context: &BackendLaunchContext,
    root_dir: &Path,
) -> Vec<PathBuf> {
    let filename = if cfg!(windows) {
        "noofy-backend.exe"
    } else {
        "noofy-backend"
    };
    let mut candidates = vec![root_dir.join("bin").join(filename), root_dir.join(filename)];
    if let Some(resource_dir) = &context.resource_dir {
        candidates.push(resource_dir.join(filename));
    }
    if let Some(exe_dir) = context.current_exe.parent() {
        candidates.push(exe_dir.join(filename));
    }
    candidates
}

fn first_existing(candidates: Vec<PathBuf>) -> Option<PathBuf> {
    candidates.into_iter().find(|path| path.exists())
}

fn validate_packaged_runtime_layout(
    layout: &PackagedRuntimeLayout,
    expected_target: &str,
) -> io::Result<()> {
    let manifest_path = layout.root_dir.join(RUNTIME_MANIFEST_NAME);
    if !manifest_path.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!(
                "Packaged Noofy is missing its runtime manifest. Expected {}.",
                manifest_path.display()
            ),
        ));
    }

    let manifest_text = fs::read_to_string(&manifest_path)?;
    let manifest: serde_json::Value = serde_json::from_str(&manifest_text).map_err(|error| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!(
                "Packaged Noofy runtime manifest is not valid JSON at {}: {error}.",
                manifest_path.display()
            ),
        )
    })?;
    let manifest_target = manifest.get("target").and_then(|value| value.as_str());
    if manifest_target != Some(expected_target) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!(
                "Packaged Noofy runtime target mismatch. Expected {expected_target}, found {}.",
                manifest_target.unwrap_or("<missing>")
            ),
        ));
    }
    if manifest
        .pointer("/backend/packagedPath")
        .and_then(|value| value.as_str())
        != Some("backend")
        || manifest
            .pointer("/backend/appPath")
            .and_then(|value| value.as_str())
            != Some("backend/app")
        || manifest
            .pointer("/backend/pyprojectPath")
            .and_then(|value| value.as_str())
            != Some("backend/pyproject.toml")
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "Packaged Noofy runtime manifest backend paths do not match the bundled resource layout.",
        ));
    }
    let manifest_python = manifest_runtime_file(
        &manifest,
        "/python/executable",
        &layout.root_dir,
        "Packaged Python executable",
    )?;
    let manifest_uv = manifest_runtime_file(
        &manifest,
        "/uv/executable",
        &layout.root_dir,
        "Packaged uv executable",
    )?;
    let Some(layout_python) = &layout.python_executable else {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "Packaged Noofy runtime manifest lists Python, but no bundled Python candidate was found.",
        ));
    };
    let Some(layout_uv) = &layout.uv_executable else {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "Packaged Noofy runtime manifest lists uv, but no bundled uv candidate was found.",
        ));
    };
    if !same_file(&manifest_python, layout_python)? {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!(
                "Packaged Noofy runtime manifest Python path does not match the bundled launcher path. Manifest: {}; launcher: {}.",
                manifest_python.display(),
                layout_python.display(),
            ),
        ));
    }
    if !same_file(&manifest_uv, layout_uv)? {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!(
                "Packaged Noofy runtime manifest uv path does not match the bundled launcher path. Manifest: {}; launcher: {}.",
                manifest_uv.display(),
                layout_uv.display(),
            ),
        ));
    }

    for (path, label) in [
        (
            layout.backend_dir.join("app").join("__main__.py"),
            "backend module entrypoint",
        ),
        (
            layout.backend_dir.join("pyproject.toml"),
            "backend metadata",
        ),
        (
            layout.comfyui_dir.join("main.py"),
            "bundled ComfyUI entrypoint",
        ),
        (
            layout.workflows_dir.clone(),
            "bundled starter workflow packages",
        ),
    ] {
        if !path.exists() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                format!(
                    "Packaged Noofy is missing its {label}. Expected {}.",
                    path.display()
                ),
            ));
        }
    }

    Ok(())
}

fn manifest_runtime_file(
    manifest: &serde_json::Value,
    pointer: &str,
    root_dir: &Path,
    label: &str,
) -> io::Result<PathBuf> {
    let relative = manifest
        .pointer(pointer)
        .and_then(|value| value.as_str())
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!("{label} path is missing from the packaged runtime manifest."),
            )
        })?;
    let relative_path = Path::new(relative);
    if relative_path.is_absolute()
        || relative_path
            .components()
            .any(|component| matches!(component, std::path::Component::ParentDir))
    {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("{label} path must stay inside the packaged runtime root: {relative}."),
        ));
    }
    let path = root_dir.join(relative_path);
    if !path.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!("{label} is missing: {}.", path.display()),
        ));
    }
    Ok(path)
}

fn same_file(left: &Path, right: &Path) -> io::Result<bool> {
    Ok(left.canonicalize()? == right.canonicalize()?)
}

#[cfg(all(test, target_os = "macos", target_arch = "x86_64"))]
fn supported_packaged_runtime_target() -> io::Result<&'static str> {
    Ok("macos-arm64")
}

#[cfg(not(all(test, target_os = "macos", target_arch = "x86_64")))]
fn supported_packaged_runtime_target() -> io::Result<&'static str> {
    if cfg!(all(target_os = "macos", target_arch = "aarch64")) {
        return Ok("macos-arm64");
    }
    if cfg!(all(target_os = "windows", target_arch = "x86_64")) {
        return Ok("windows-x64");
    }
    if cfg!(all(target_os = "linux", target_arch = "x86_64")) {
        return Ok("linux-x64");
    }
    Err(io::Error::new(
        io::ErrorKind::Unsupported,
        "This Noofy desktop build does not include a supported packaged runtime for this platform. Supported packaged runtimes are macOS Apple Silicon, Windows x64, and Linux x64.",
    ))
}

fn apply_backend_environment(
    context: &BackendLaunchContext,
    spec: &mut BackendLaunchSpec,
    require_packaged_python: bool,
) -> io::Result<()> {
    if require_packaged_python {
        set_env(spec, "COMFYUI_RUNTIME_MODE", OsString::from("managed"));
    } else {
        set_env_default(
            context,
            spec,
            "COMFYUI_RUNTIME_MODE",
            OsString::from("managed"),
        );
    }

    let layout = packaged_runtime_layout(context);
    let use_packaged_runtime =
        require_packaged_python || env_path(context, "NOOFY_PACKAGED_RUNTIME_DIR").is_some();
    if use_packaged_runtime {
        if let Some(repo) = option_env!("NOOFY_RUNTIME_UPDATE_REPO") {
            set_env(spec, "NOOFY_RUNTIME_UPDATE_REPO", OsString::from(repo));
        }
        if let Some(resource_dir) = packaged_resource_dir(context, &layout.root_dir) {
            set_env(
                spec,
                "NOOFY_BUNDLED_RESOURCE_DIR",
                resource_dir.into_os_string(),
            );
        }
        if layout.comfyui_dir.exists() {
            set_env(
                spec,
                "NOOFY_BUNDLED_COMFYUI_DIR",
                layout.comfyui_dir.clone().into_os_string(),
            );
        }
        if layout.workflows_dir.exists() {
            set_env(
                spec,
                "NOOFY_BUNDLED_WORKFLOWS_DIR",
                layout.workflows_dir.clone().into_os_string(),
            );
        }
        if let Some(python) = layout.python_executable {
            set_env(
                spec,
                "COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE",
                python.into_os_string(),
            );
        } else if require_packaged_python {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "Packaged Noofy requires a bundled Python executable for managed ComfyUI runtime preparation.",
            ));
        }
        if let Some(uv) = layout.uv_executable {
            set_env(spec, "NOOFY_UV_EXECUTABLE", uv.into_os_string());
        } else if require_packaged_python {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "Packaged Noofy requires a bundled uv executable for isolated workflow dependency environments.",
            ));
        }
    }

    set_env(spec, "PYTHONNOUSERSITE", OsString::from("1"));
    Ok(())
}

fn packaged_resource_dir(context: &BackendLaunchContext, root_dir: &Path) -> Option<PathBuf> {
    if let Some(resource_dir) = &context.resource_dir {
        if root_dir.starts_with(resource_dir) {
            return Some(resource_dir.clone());
        }
    }
    if root_dir
        .file_name()
        .is_some_and(|name| name == NOOFY_RUNTIME_RESOURCE_DIR)
    {
        return root_dir.parent().map(Path::to_path_buf);
    }
    if let Some(resource_dir) = &context.resource_dir {
        return Some(resource_dir.clone());
    }
    None
}

fn packaged_env_removals() -> Vec<String> {
    [
        "COMFYUI_BASE_URL",
        "COMFYUI_MANAGED_HOST",
        "COMFYUI_MANAGED_PORT",
        "COMFYUI_PYTHON_EXECUTABLE",
        "COMFYUI_REPO_DIR",
        "COMFYUI_RUNTIME_MODE",
        "COMFYUI_WS_URL",
        "CONDA_PREFIX",
        "NOOFY_BACKEND_DIR",
        "NOOFY_BACKEND_PYTHON",
        "NOOFY_BACKEND_SIDECAR",
        "NOOFY_ENABLE_DEVELOPER_BACKEND_OVERRIDES",
        "NOOFY_FORCE_PACKAGED_BACKEND",
        "NOOFY_PACKAGED_RUNTIME_DIR",
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    ]
    .into_iter()
    .map(String::from)
    .collect()
}

fn set_env(spec: &mut BackendLaunchSpec, key: &str, value: OsString) {
    spec.env.push((key.to_string(), value));
}

fn set_env_default(
    context: &BackendLaunchContext,
    spec: &mut BackendLaunchSpec,
    key: &str,
    value: OsString,
) {
    if !context.env.contains_key(key) {
        set_env(spec, key, value);
    }
}

fn env_path(context: &BackendLaunchContext, key: &str) -> Option<PathBuf> {
    context
        .env
        .get(key)
        .map(|value| PathBuf::from(value.clone()))
}

fn path_inside(child: &Path, parent: &Path) -> bool {
    let child = child.canonicalize().unwrap_or_else(|_| child.to_path_buf());
    let parent = parent
        .canonicalize()
        .unwrap_or_else(|_| parent.to_path_buf());
    child.starts_with(parent)
}

fn env_flag(env: &HashMap<String, OsString>, key: &str) -> bool {
    env.get(key)
        .and_then(|value| value.to_str())
        .map(|value| matches!(value, "1" | "true" | "TRUE" | "yes" | "YES"))
        .unwrap_or(false)
}

fn developer_backend_overrides_enabled(context: &BackendLaunchContext) -> bool {
    cfg!(debug_assertions) && env_flag(&context.env, "NOOFY_ENABLE_DEVELOPER_BACKEND_OVERRIDES")
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

fn redact_backend_log_line(line: &str, api_token: &str) -> String {
    if api_token.is_empty() {
        return line.to_string();
    }
    line.replace(api_token, "[redacted]")
}

#[cfg(unix)]
fn configure_backend_process_tree(command: &mut Command) {
    use std::os::unix::process::CommandExt;
    unsafe {
        command.pre_exec(|| {
            if libc::setsid() == -1 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
}

#[cfg(windows)]
fn configure_backend_process_tree(_command: &mut Command) {}

fn write_backend_process_lease(
    path: &Path,
    pid: u32,
    launch: &BackendLaunchSpec,
) -> io::Result<()> {
    let Some(parent) = path.parent() else {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "backend process lease has no parent directory",
        ));
    };
    fs::create_dir_all(parent)?;
    let process_identity = process_creation_identity(pid).ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::Other,
            format!("could not establish stable Noofy backend process identity (PID {pid})"),
        )
    })?;
    let lease = BackendProcessLease {
        schema_version: 2,
        pid,
        process_identity: Some(process_identity),
        program: PathBuf::from(&launch.program)
            .canonicalize()
            .unwrap_or_else(|_| PathBuf::from(&launch.program))
            .to_string_lossy()
            .to_string(),
        current_dir: launch
            .current_dir
            .canonicalize()
            .unwrap_or_else(|_| launch.current_dir.clone())
            .to_string_lossy()
            .to_string(),
    };
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, serde_json::to_vec(&lease)?)?;
    fs::rename(temporary, path)
}

fn recover_stale_backend(path: &Path, launch: &BackendLaunchSpec) -> io::Result<()> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    let lease: BackendProcessLease = match serde_json::from_slice(&bytes) {
        Ok(lease) => lease,
        Err(_) => {
            let _ = fs::remove_file(path);
            return Ok(());
        }
    };
    if !backend_process_tree_is_running(lease.pid) {
        let _ = fs::remove_file(path);
        return Ok(());
    }
    if stale_backend_matches(&lease, launch) {
        eprintln!(
            "[noofy-launcher] stopping validated stale backend process group (PID {})",
            lease.pid
        );
        terminate_stale_backend(lease.pid);
        let _ = fs::remove_file(path);
        return Ok(());
    }
    Err(io::Error::new(
        io::ErrorKind::AlreadyExists,
        format!(
            "Recorded Noofy backend PID {} is still running but could not be safely identified.",
            lease.pid
        ),
    ))
}

#[cfg(all(unix, test))]
fn process_is_running(pid: u32) -> bool {
    unsafe { libc::kill(pid as i32, 0) == 0 }
}

#[cfg(unix)]
fn backend_process_tree_is_running(pid: u32) -> bool {
    unsafe { libc::kill(-(pid as i32), 0) == 0 }
}

#[cfg(windows)]
fn backend_process_tree_is_running(_pid: u32) -> bool {
    false
}

#[cfg(unix)]
fn stale_backend_matches(lease: &BackendProcessLease, launch: &BackendLaunchSpec) -> bool {
    let expected_program = PathBuf::from(&launch.program)
        .canonicalize()
        .unwrap_or_else(|_| PathBuf::from(&launch.program))
        .to_string_lossy()
        .to_string();
    let expected_cwd = launch
        .current_dir
        .canonicalize()
        .unwrap_or_else(|_| launch.current_dir.clone());
    if lease.process_identity.as_deref() != process_creation_identity(lease.pid).as_deref()
        || lease.process_identity.is_none()
        || lease.program != expected_program
        || PathBuf::from(&lease.current_dir) != expected_cwd
        || unsafe { libc::getpgid(lease.pid as i32) } != lease.pid as i32
    {
        return false;
    }
    let command = unix_process_command(lease.pid);
    let cwd = unix_process_cwd(lease.pid);
    command_matches_backend_launch(&command, &expected_program, launch)
        && cwd.as_ref() == Some(&expected_cwd)
}

#[cfg(unix)]
fn command_matches_backend_launch(
    command: &str,
    expected_program: &str,
    launch: &BackendLaunchSpec,
) -> bool {
    if command.contains(expected_program) {
        return true;
    }

    #[cfg(target_os = "macos")]
    {
        // Framework Python builds can appear in ps as Python.app even when
        // launched through bin/python3.x.
        if command.contains("/Python.app/Contents/MacOS/Python")
            && command_contains_launch_args(command, &launch.args)
        {
            return true;
        }
    }

    false
}

#[cfg(unix)]
fn command_contains_launch_args(command: &str, args: &[OsString]) -> bool {
    let expected_args = args
        .iter()
        .map(|arg| arg.to_string_lossy())
        .collect::<Vec<_>>()
        .join(" ");
    !expected_args.is_empty() && command.contains(&expected_args)
}

#[cfg(unix)]
fn process_creation_identity(pid: u32) -> Option<String> {
    #[cfg(target_os = "macos")]
    if let Some(identity) = macos_process_creation_identity(pid) {
        return Some(identity);
    }
    if let Ok(stat) = fs::read_to_string(format!("/proc/{pid}/stat")) {
        if let Some(start_time) = linux_proc_stat_start_time(&stat) {
            return Some(format!("proc-start:{start_time}"));
        }
    }
    Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "lstart="])
        .output()
        .ok()
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
        .filter(|started| !started.is_empty())
        .map(|started| format!("ps-start:{started}"))
}

#[cfg(unix)]
fn linux_proc_stat_start_time(stat: &str) -> Option<&str> {
    // The second field is parenthesized and may contain spaces or parentheses.
    let command_end = stat.rfind(')')?;
    stat[command_end + 1..].split_whitespace().nth(19)
}

#[cfg(target_os = "macos")]
fn macos_process_creation_identity(pid: u32) -> Option<String> {
    #[repr(C)]
    struct ProcBsdInfo {
        pbi_flags: u32,
        pbi_status: u32,
        pbi_xstatus: u32,
        pbi_pid: u32,
        pbi_ppid: u32,
        pbi_uid: u32,
        pbi_gid: u32,
        pbi_ruid: u32,
        pbi_rgid: u32,
        pbi_svuid: u32,
        pbi_svgid: u32,
        rfu_1: u32,
        pbi_comm: [u8; 16],
        pbi_name: [u8; 32],
        pbi_nfiles: u32,
        pbi_pgid: u32,
        pbi_pjobc: u32,
        e_tdev: u32,
        e_tpgid: u32,
        pbi_nice: i32,
        pbi_start_tvsec: u64,
        pbi_start_tvusec: u64,
    }

    unsafe extern "C" {
        fn proc_pidinfo(
            pid: i32,
            flavor: i32,
            arg: u64,
            buffer: *mut std::ffi::c_void,
            buffersize: i32,
        ) -> i32;
    }

    let mut info: ProcBsdInfo = unsafe { std::mem::zeroed() };
    let size = std::mem::size_of::<ProcBsdInfo>() as i32;
    let written = unsafe {
        proc_pidinfo(
            pid as i32,
            3,
            0,
            &mut info as *mut _ as *mut std::ffi::c_void,
            size,
        )
    };
    if written != size {
        return None;
    }
    Some(format!(
        "macos-start:{}:{}",
        info.pbi_start_tvsec, info.pbi_start_tvusec
    ))
}

#[cfg(windows)]
fn process_creation_identity(pid: u32) -> Option<String> {
    use windows_sys::Win32::{
        Foundation::{CloseHandle, FILETIME},
        System::Threading::{GetProcessTimes, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION},
    };

    unsafe {
        let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
        if handle.is_null() {
            return None;
        }
        let mut creation: FILETIME = std::mem::zeroed();
        let mut exit_time: FILETIME = std::mem::zeroed();
        let mut kernel: FILETIME = std::mem::zeroed();
        let mut user: FILETIME = std::mem::zeroed();
        let result = GetProcessTimes(
            handle,
            &mut creation,
            &mut exit_time,
            &mut kernel,
            &mut user,
        );
        CloseHandle(handle);
        if result == 0 {
            return None;
        }
        let ticks = ((creation.dwHighDateTime as u64) << 32) | creation.dwLowDateTime as u64;
        Some(format!("windows-created:{ticks}"))
    }
}

#[cfg(unix)]
fn unix_process_command(pid: u32) -> String {
    let proc_cmdline = PathBuf::from(format!("/proc/{pid}/cmdline"));
    if let Ok(bytes) = fs::read(proc_cmdline) {
        return String::from_utf8_lossy(&bytes)
            .replace('\0', " ")
            .trim()
            .to_string();
    }
    Command::new("ps")
        .args(["-p", &pid.to_string(), "-o", "command="])
        .output()
        .ok()
        .map(|output| String::from_utf8_lossy(&output.stdout).trim().to_string())
        .unwrap_or_default()
}

#[cfg(unix)]
fn unix_process_cwd(pid: u32) -> Option<PathBuf> {
    if let Ok(path) = fs::read_link(format!("/proc/{pid}/cwd")) {
        return path.canonicalize().ok();
    }
    Command::new("lsof")
        .args(["-a", "-p", &pid.to_string(), "-d", "cwd", "-Fn"])
        .output()
        .ok()
        .and_then(|output| {
            String::from_utf8_lossy(&output.stdout)
                .lines()
                .find_map(|line| line.strip_prefix('n').map(PathBuf::from))
        })
        .and_then(|path| path.canonicalize().ok())
}

#[cfg(windows)]
fn stale_backend_matches(_lease: &BackendProcessLease, _launch: &BackendLaunchSpec) -> bool {
    false
}

#[cfg(unix)]
fn terminate_stale_backend(pid: u32) {
    unsafe {
        libc::kill(-(pid as i32), libc::SIGTERM);
    }
    let deadline = Instant::now() + Duration::from_secs(8);
    while backend_process_tree_is_running(pid) && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(100));
    }
    if backend_process_tree_is_running(pid) {
        unsafe {
            libc::kill(-(pid as i32), libc::SIGKILL);
        }
    }
}

#[cfg(windows)]
fn terminate_stale_backend(_pid: u32) {}

#[cfg(unix)]
fn terminate_child_tree(child: &mut Child) {
    let pid = child.id() as i32;
    unsafe {
        libc::kill(-pid, libc::SIGTERM);
    }
    let deadline = Instant::now() + Duration::from_secs(8);
    while backend_process_tree_is_running(pid as u32) && Instant::now() < deadline {
        let _ = child.try_wait();
        thread::sleep(Duration::from_millis(100));
    }
    if backend_process_tree_is_running(pid as u32) {
        unsafe {
            libc::kill(-pid, libc::SIGKILL);
        }
    }
    let _ = child.wait();
}

#[cfg(windows)]
fn terminate_child_tree(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(windows)]
fn create_kill_on_close_job(child: &Child) -> io::Result<isize> {
    use std::mem::size_of;
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    unsafe {
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return Err(io::Error::last_os_error());
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const _,
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        ) == 0
            || AssignProcessToJobObject(job, child.as_raw_handle() as _) == 0
        {
            windows_sys::Win32::Foundation::CloseHandle(job);
            return Err(io::Error::last_os_error());
        }
        Ok(job as isize)
    }
}

#[cfg(windows)]
fn close_job_handle(job_handle: &mut isize) {
    if *job_handle != 0 {
        unsafe {
            windows_sys::Win32::Foundation::CloseHandle(*job_handle as _);
        }
        *job_handle = 0;
    }
}

fn terminate_backend(process: &Arc<Mutex<Option<BackendProcess>>>) {
    let Ok(mut guard) = process.lock() else {
        return;
    };

    if let Some(mut backend) = guard.take() {
        backend.shutdown();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        time::{SystemTime, UNIX_EPOCH},
    };

    fn temp_dir(name: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time before epoch")
            .as_nanos();
        let path = std::env::temp_dir().join(format!("noofy-tauri-{name}-{nonce}"));
        fs::create_dir_all(&path).expect("create temp dir");
        path
    }

    fn touch(path: &Path) {
        fs::create_dir_all(path.parent().expect("path should have parent")).expect("create parent");
        fs::write(path, "").expect("write file");
    }

    fn write_runtime_manifest(runtime: &Path) {
        let target = supported_packaged_runtime_target().expect("supported test host");
        let python = if cfg!(windows) {
            "python/python.exe"
        } else {
            "python/bin/python3"
        };
        let uv = if cfg!(windows) {
            "python/Scripts/uv.exe"
        } else {
            "python/bin/uv"
        };
        fs::write(
            runtime.join(RUNTIME_MANIFEST_NAME),
            format!(
                r#"{{
  "schemaVersion": 1,
  "layoutVersion": 1,
  "target": "{target}",
  "python": {{
    "executable": "{python}"
  }},
  "uv": {{
    "executable": "{uv}"
  }},
  "backend": {{
    "packagedPath": "backend",
    "appPath": "backend/app",
    "pyprojectPath": "backend/pyproject.toml"
  }}
}}
"#
            ),
        )
        .expect("write runtime manifest");
    }

    fn context(resource_dir: PathBuf, packaged_mode: bool) -> BackendLaunchContext {
        let app_data_dir = temp_dir("app-data");
        BackendLaunchContext {
            env: HashMap::new(),
            manifest_dir: temp_dir("manifest").join("frontend").join("src-tauri"),
            current_exe: resource_dir.join("Noofy"),
            resource_dir: Some(resource_dir),
            app_data_dir: Some(app_data_dir),
            packaged_mode,
        }
    }

    fn context_with_env(
        resource_dir: PathBuf,
        packaged_mode: bool,
        env: HashMap<String, OsString>,
    ) -> BackendLaunchContext {
        BackendLaunchContext {
            env,
            manifest_dir: temp_dir("manifest").join("frontend").join("src-tauri"),
            current_exe: resource_dir.join("Noofy"),
            resource_dir: Some(resource_dir),
            app_data_dir: Some(temp_dir("app-data")),
            packaged_mode,
        }
    }

    fn create_packaged_runtime(root: &Path) -> (PathBuf, PathBuf) {
        let python = if cfg!(windows) {
            root.join("python").join("python.exe")
        } else {
            root.join("python").join("bin").join("python3")
        };
        let uv = if cfg!(windows) {
            root.join("python").join("Scripts").join("uv.exe")
        } else {
            root.join("python").join("bin").join("uv")
        };
        touch(&python);
        touch(&uv);
        touch(&root.join("backend").join("app").join("__main__.py"));
        touch(&root.join("backend").join("pyproject.toml"));
        touch(
            &root
                .join("backend")
                .join("app")
                .join("workflows")
                .join("packages")
                .join(".keep"),
        );
        touch(&root.join("comfyui").join("main.py"));
        write_runtime_manifest(root);
        (python, uv)
    }

    fn write_active_runtime(app_data_dir: &Path, runtime_root: &Path) {
        let active_file = app_data_dir
            .join("runtime-store")
            .join("noofy-runtime")
            .join("active-runtime.json");
        fs::create_dir_all(active_file.parent().expect("active parent"))
            .expect("create active parent");
        fs::write(
            active_file,
            format!(
                r#"{{
  "schema_version": "0.1.0",
  "runtime": {{
    "runtime_id": "test-runtime",
    "tag": "v1.0.0",
    "target": "{}",
    "runtime_path": "{}",
    "manifest_sha256": "test"
  }}
}}
"#,
                supported_packaged_runtime_target().expect("supported test host"),
                runtime_root.display()
            ),
        )
        .expect("write active runtime");
    }

    fn env_value<'a>(spec: &'a BackendLaunchSpec, key: &str) -> Option<&'a OsString> {
        spec.env
            .iter()
            .find_map(|(candidate, value)| (candidate == key).then_some(value))
    }

    #[test]
    fn packaged_backend_requires_bundled_python() {
        let resource_dir = temp_dir("missing-python");
        let ctx = context(resource_dir, true);

        let error = backend_launch_spec(&ctx).expect_err("packaged launch should fail");

        assert_eq!(error.kind(), io::ErrorKind::NotFound);
        assert!(error.to_string().contains("bundled Python runtime"));
    }

    #[test]
    fn packaged_backend_uses_bundled_python_and_resource_paths() {
        let resource_dir = temp_dir("python-runtime");
        let runtime = resource_dir.join(NOOFY_RUNTIME_RESOURCE_DIR);
        let (python, uv) = create_packaged_runtime(&runtime);

        let spec = backend_launch_spec(&context(resource_dir.clone(), true))
            .expect("packaged launch spec");

        assert_eq!(PathBuf::from(spec.program.clone()), python);
        assert_eq!(spec.args, python_backend_args());
        assert_eq!(spec.current_dir, runtime.join("backend"));
        assert_eq!(
            env_value(&spec, "COMFYUI_RUNTIME_MODE"),
            Some(&OsString::from("managed"))
        );
        assert_eq!(
            env_value(&spec, "NOOFY_BUNDLED_RESOURCE_DIR"),
            Some(&resource_dir.into_os_string())
        );
        assert_eq!(
            env_value(&spec, "NOOFY_BUNDLED_COMFYUI_DIR"),
            Some(&runtime.join("comfyui").into_os_string())
        );
        assert_eq!(
            env_value(&spec, "COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE"),
            Some(&python.into_os_string())
        );
        assert_eq!(
            env_value(&spec, "NOOFY_UV_EXECUTABLE"),
            Some(&uv.into_os_string())
        );
        assert!(spec.remove_env.contains(&"COMFYUI_REPO_DIR".to_string()));
        assert!(spec.remove_env.contains(&"PYTHONPATH".to_string()));
    }

    #[test]
    fn packaged_backend_prefers_valid_active_app_data_runtime() {
        let resource_dir = temp_dir("active-runtime-resource");
        let bundled = resource_dir.join(NOOFY_RUNTIME_RESOURCE_DIR);
        create_packaged_runtime(&bundled);
        let app_data_dir = temp_dir("active-runtime-data");
        let active = app_data_dir
            .join("runtime-store")
            .join("noofy-runtime")
            .join("runtimes")
            .join("test-runtime")
            .join(NOOFY_RUNTIME_RESOURCE_DIR);
        let (active_python, _) = create_packaged_runtime(&active);
        write_active_runtime(&app_data_dir, &active);
        let ctx = BackendLaunchContext {
            env: HashMap::new(),
            manifest_dir: temp_dir("manifest").join("frontend").join("src-tauri"),
            current_exe: resource_dir.join("Noofy"),
            resource_dir: Some(resource_dir),
            app_data_dir: Some(app_data_dir.clone()),
            packaged_mode: true,
        };

        let spec = backend_launch_spec(&ctx).expect("packaged launch spec");

        let expected_resource_dir = active
            .parent()
            .expect("active parent")
            .to_path_buf()
            .into_os_string();
        assert_eq!(PathBuf::from(spec.program.clone()), active_python);
        assert_eq!(spec.current_dir, active.join("backend"));
        assert_eq!(
            env_value(&spec, "NOOFY_BUNDLED_RESOURCE_DIR"),
            Some(&expected_resource_dir)
        );
    }

    #[test]
    fn packaged_backend_falls_back_when_active_runtime_is_outside_app_data() {
        let resource_dir = temp_dir("invalid-active-resource");
        let bundled = resource_dir.join(NOOFY_RUNTIME_RESOURCE_DIR);
        let (bundled_python, _) = create_packaged_runtime(&bundled);
        let app_data_dir = temp_dir("invalid-active-data");
        let outside = temp_dir("outside-active").join(NOOFY_RUNTIME_RESOURCE_DIR);
        create_packaged_runtime(&outside);
        write_active_runtime(&app_data_dir, &outside);
        let ctx = BackendLaunchContext {
            env: HashMap::new(),
            manifest_dir: temp_dir("manifest").join("frontend").join("src-tauri"),
            current_exe: resource_dir.join("Noofy"),
            resource_dir: Some(resource_dir),
            app_data_dir: Some(app_data_dir),
            packaged_mode: true,
        };

        let spec = backend_launch_spec(&ctx).expect("packaged launch spec");

        assert_eq!(PathBuf::from(spec.program.clone()), bundled_python);
        assert_eq!(spec.current_dir, bundled.join("backend"));
    }

    #[test]
    fn packaged_backend_ignores_release_unsafe_developer_overrides() {
        let resource_dir = temp_dir("release-overrides");
        let runtime = resource_dir.join(NOOFY_RUNTIME_RESOURCE_DIR);
        let python = if cfg!(windows) {
            runtime.join("python").join("python.exe")
        } else {
            runtime.join("python").join("bin").join("python3")
        };
        let uv = if cfg!(windows) {
            runtime.join("python").join("Scripts").join("uv.exe")
        } else {
            runtime.join("python").join("bin").join("uv")
        };
        touch(&python);
        touch(&uv);
        touch(&runtime.join("backend").join("app").join("__main__.py"));
        touch(&runtime.join("backend").join("pyproject.toml"));
        touch(
            &runtime
                .join("backend")
                .join("app")
                .join("workflows")
                .join("packages")
                .join(".keep"),
        );
        touch(&runtime.join("comfyui").join("main.py"));
        write_runtime_manifest(&runtime);

        let alternate_runtime = temp_dir("alternate-runtime");
        let mut env = HashMap::new();
        env.insert(
            "NOOFY_BACKEND_SIDECAR".to_string(),
            OsString::from("/tmp/noofy-dev-sidecar"),
        );
        env.insert(
            "NOOFY_PACKAGED_RUNTIME_DIR".to_string(),
            alternate_runtime.into_os_string(),
        );
        env.insert(
            "NOOFY_ENABLE_DEVELOPER_BACKEND_OVERRIDES".to_string(),
            OsString::from("0"),
        );
        env.insert(
            "COMFYUI_RUNTIME_MODE".to_string(),
            OsString::from("external"),
        );
        env.insert(
            "NOOFY_BACKEND_DIR".to_string(),
            OsString::from("/tmp/noofy-repo/backend"),
        );

        let spec = backend_launch_spec(&context_with_env(resource_dir, true, env))
            .expect("packaged launch spec");

        assert_eq!(PathBuf::from(spec.program.clone()), python);
        assert_eq!(
            env_value(&spec, "COMFYUI_RUNTIME_MODE"),
            Some(&OsString::from("managed"))
        );
        assert!(spec
            .remove_env
            .contains(&"NOOFY_PACKAGED_RUNTIME_DIR".to_string()));
        assert!(spec
            .remove_env
            .contains(&"NOOFY_BACKEND_SIDECAR".to_string()));
        assert!(spec
            .remove_env
            .contains(&"COMFYUI_RUNTIME_MODE".to_string()));
    }

    #[test]
    fn packaged_backend_prefers_sidecar_when_available() {
        let resource_dir = temp_dir("sidecar-runtime");
        let runtime = resource_dir.join(NOOFY_RUNTIME_RESOURCE_DIR);
        let python = if cfg!(windows) {
            runtime.join("python").join("python.exe")
        } else {
            runtime.join("python").join("bin").join("python3")
        };
        let uv = if cfg!(windows) {
            runtime.join("python").join("Scripts").join("uv.exe")
        } else {
            runtime.join("python").join("bin").join("uv")
        };
        let sidecar = if cfg!(windows) {
            runtime.join("bin").join("noofy-backend.exe")
        } else {
            runtime.join("bin").join("noofy-backend")
        };
        touch(&python);
        touch(&uv);
        touch(&sidecar);
        touch(&runtime.join("backend").join("app").join("__main__.py"));
        touch(&runtime.join("backend").join("pyproject.toml"));
        touch(
            &runtime
                .join("backend")
                .join("app")
                .join("workflows")
                .join("packages")
                .join(".keep"),
        );
        touch(&runtime.join("comfyui").join("main.py"));
        write_runtime_manifest(&runtime);

        let spec = backend_launch_spec(&context(resource_dir, true)).expect("packaged launch spec");

        assert_eq!(PathBuf::from(spec.program.clone()), sidecar);
        assert_eq!(spec.args, backend_sidecar_args());
        assert_eq!(spec.current_dir, runtime);
        assert_eq!(
            env_value(&spec, "COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE"),
            Some(&python.into_os_string())
        );
    }

    #[test]
    fn packaged_backend_requires_bundled_uv() {
        let resource_dir = temp_dir("missing-uv");
        let runtime = resource_dir.join(NOOFY_RUNTIME_RESOURCE_DIR);
        let python = if cfg!(windows) {
            runtime.join("python").join("python.exe")
        } else {
            runtime.join("python").join("bin").join("python3")
        };
        touch(&python);
        touch(&runtime.join("backend").join("app").join("__main__.py"));

        let error = backend_launch_spec(&context(resource_dir, true))
            .expect_err("packaged launch should fail");

        assert_eq!(error.kind(), io::ErrorKind::NotFound);
        assert!(error.to_string().contains("bundled uv executable"));
    }

    #[test]
    fn packaged_backend_rejects_manifest_python_path_mismatch() {
        let resource_dir = temp_dir("manifest-python-mismatch");
        let runtime = resource_dir.join(NOOFY_RUNTIME_RESOURCE_DIR);
        let python = if cfg!(windows) {
            runtime.join("python").join("python.exe")
        } else {
            runtime.join("python").join("bin").join("python3")
        };
        let uv = if cfg!(windows) {
            runtime.join("python").join("Scripts").join("uv.exe")
        } else {
            runtime.join("python").join("bin").join("uv")
        };
        touch(&python);
        touch(&uv);
        touch(&runtime.join("python").join("other-python"));
        touch(&runtime.join("backend").join("app").join("__main__.py"));
        touch(&runtime.join("backend").join("pyproject.toml"));
        touch(
            &runtime
                .join("backend")
                .join("app")
                .join("workflows")
                .join("packages")
                .join(".keep"),
        );
        touch(&runtime.join("comfyui").join("main.py"));
        let target = supported_packaged_runtime_target().expect("supported test host");
        let manifest_python = "python/other-python";
        let manifest_uv = if cfg!(windows) {
            "python/Scripts/uv.exe"
        } else {
            "python/bin/uv"
        };
        fs::write(
            runtime.join(RUNTIME_MANIFEST_NAME),
            format!(
                r#"{{
  "schemaVersion": 1,
  "layoutVersion": 1,
  "target": "{target}",
  "python": {{
    "executable": "{manifest_python}"
  }},
  "uv": {{
    "executable": "{manifest_uv}"
  }},
  "backend": {{
    "packagedPath": "backend",
    "appPath": "backend/app",
    "pyprojectPath": "backend/pyproject.toml"
  }}
}}
"#
            ),
        )
        .expect("write runtime manifest");

        let error = backend_launch_spec(&context(resource_dir, true))
            .expect_err("packaged launch should reject mismatched manifest paths");

        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert!(error.to_string().contains("manifest Python path"));
    }

    #[test]
    fn backend_log_redaction_removes_local_api_token() {
        assert_eq!(
            redact_backend_log_line(
                "GET /api/jobs/job-1/events?token=secret-token HTTP/1.1",
                "secret-token",
            ),
            "GET /api/jobs/job-1/events?token=[redacted] HTTP/1.1"
        );
    }

    #[cfg(unix)]
    #[test]
    fn backend_process_tree_shutdown_terminates_descendants() {
        let root = temp_dir("process-tree");
        let child_pid_file = root.join("child.pid");
        let script = format!("sleep 30 & echo $! > '{}'; wait", child_pid_file.display());
        let mut command = Command::new("/bin/sh");
        command
            .args(["-c", &script])
            .current_dir(&root)
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        configure_backend_process_tree(&mut command);
        let mut child = command.spawn().expect("spawn process tree");
        let deadline = Instant::now() + Duration::from_secs(5);
        while !child_pid_file.exists() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        let descendant_pid: u32 = fs::read_to_string(&child_pid_file)
            .expect("read descendant pid")
            .trim()
            .parse()
            .expect("parse descendant pid");

        terminate_child_tree(&mut child);

        assert!(!process_is_running(child.id()));
        assert!(!process_is_running(descendant_pid));
    }

    #[cfg(unix)]
    #[test]
    fn stale_backend_identity_requires_expected_program_cwd_and_process_group() {
        let root = temp_dir("stale-backend");
        let lease_path = root.join("launcher").join("backend-process.json");
        let mut command = Command::new("/bin/sh");
        command
            .args(["-c", "while :; do sleep 1; done"])
            .current_dir(&root)
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        configure_backend_process_tree(&mut command);
        let mut child = command.spawn().expect("spawn stale backend");
        let pid = child.id();
        let launch = BackendLaunchSpec {
            program: OsString::from("/bin/sh"),
            args: vec![
                OsString::from("-c"),
                OsString::from("while :; do sleep 1; done"),
            ],
            current_dir: root.clone(),
            env: Vec::new(),
            remove_env: Vec::new(),
        };
        write_backend_process_lease(&lease_path, pid, &launch).expect("write lease");
        let lease: BackendProcessLease =
            serde_json::from_slice(&fs::read(&lease_path).expect("read lease"))
                .expect("parse lease");

        assert!(lease.process_identity.is_some());
        #[cfg(target_os = "macos")]
        assert!(lease
            .process_identity
            .as_deref()
            .expect("macOS process identity")
            .starts_with("macos-start:"));
        assert!(stale_backend_matches(&lease, &launch));
        let reused = BackendProcessLease {
            process_identity: Some("different-process-creation-time".to_string()),
            ..lease
        };
        assert!(!stale_backend_matches(&reused, &launch));

        terminate_child_tree(&mut child);
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn backend_command_match_accepts_framework_python_app_display_path() {
        let launch = BackendLaunchSpec {
            program: OsString::from(
                "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12",
            ),
            args: python_backend_args(),
            current_dir: PathBuf::from("/tmp/noofy-backend"),
            env: Vec::new(),
            remove_env: Vec::new(),
        };
        let command = "/Library/Frameworks/Python.framework/Versions/3.12/Resources/Python.app/Contents/MacOS/Python -m app --port 0";

        assert!(command_matches_backend_launch(
            command,
            "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12",
            &launch,
        ));
    }

    #[cfg(unix)]
    #[test]
    fn linux_proc_identity_parser_accepts_spaces_and_parentheses_in_command() {
        let stat =
            "123 (worker name) extra) S 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 424242 20";
        assert_eq!(linux_proc_stat_start_time(stat), Some("424242"));
    }

    #[cfg(windows)]
    #[test]
    fn backend_process_uses_kill_on_close_job_object() {
        let child = Command::new("cmd")
            .args(["/C", "ping -n 30 127.0.0.1 >NUL"])
            .spawn()
            .expect("spawn backend fixture");
        let pid = child.id();
        let mut backend = BackendProcess::new(child, None).expect("assign Job Object");

        assert_ne!(backend.job_handle, 0);
        backend.shutdown();
        assert!(backend.child.is_none());
        assert_eq!(backend.job_handle, 0);
        assert!(process_creation_identity(pid).is_none());
    }

    #[test]
    fn source_backend_can_use_backend_venv_python() {
        let root = temp_dir("source-runtime");
        let manifest_dir = root.join("frontend").join("src-tauri");
        let backend_dir = root.join("backend");
        let python = if cfg!(windows) {
            backend_dir.join(".venv").join("Scripts").join("python.exe")
        } else {
            backend_dir.join(".venv").join("bin").join("python")
        };
        touch(&python);
        let ctx = BackendLaunchContext {
            env: HashMap::new(),
            manifest_dir,
            current_exe: root
                .join("frontend")
                .join("src-tauri")
                .join("target")
                .join("debug")
                .join("noofy"),
            resource_dir: None,
            app_data_dir: Some(temp_dir("source-app-data")),
            packaged_mode: false,
        };

        let spec = backend_launch_spec(&ctx).expect("source launch spec");

        assert_eq!(PathBuf::from(spec.program.clone()), python);
        assert_eq!(spec.args, python_backend_args());
        assert_eq!(spec.current_dir, backend_dir);
        assert_eq!(
            env_value(&spec, "COMFYUI_RUNTIME_MODE"),
            Some(&OsString::from("managed"))
        );
        assert!(spec.remove_env.is_empty());
    }

    #[test]
    fn source_backend_does_not_use_tauri_resource_dir_as_packaged_runtime() {
        let root = temp_dir("source-runtime-with-resource-dir");
        let manifest_dir = root.join("frontend").join("src-tauri");
        let backend_dir = root.join("backend");
        let python = if cfg!(windows) {
            backend_dir.join(".venv").join("Scripts").join("python.exe")
        } else {
            backend_dir.join(".venv").join("bin").join("python")
        };
        touch(&python);
        let resource_dir = root.join("target").join("debug").join("resources");
        touch(
            &resource_dir
                .join(NOOFY_RUNTIME_RESOURCE_DIR)
                .join("python")
                .join("bin")
                .join("python3"),
        );
        let ctx = BackendLaunchContext {
            env: HashMap::new(),
            manifest_dir,
            current_exe: root
                .join("frontend")
                .join("src-tauri")
                .join("target")
                .join("debug")
                .join("noofy"),
            resource_dir: Some(resource_dir),
            app_data_dir: Some(temp_dir("source-app-data")),
            packaged_mode: false,
        };

        let spec = backend_launch_spec(&ctx).expect("source launch spec");

        assert_eq!(PathBuf::from(spec.program.clone()), python);
        assert_eq!(env_value(&spec, "NOOFY_BUNDLED_RESOURCE_DIR"), None);
        assert_eq!(env_value(&spec, "NOOFY_BUNDLED_COMFYUI_DIR"), None);
        assert_eq!(
            env_value(&spec, "COMFYUI_BOOTSTRAP_PYTHON_EXECUTABLE"),
            None
        );
    }
}
