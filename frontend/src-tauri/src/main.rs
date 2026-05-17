#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use rand::{rngs::OsRng, RngCore};
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
const OPEN_WORKFLOW_FILE_EVENT: &str = "noofy-open-workflow-file";
const MAX_NOOFY_FILE_BYTES: u64 = 512 * 1024 * 1024;

struct BackendRuntime {
    child: Child,
    api_base_url: String,
    api_token: String,
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
    let backend_process: Arc<Mutex<Option<Child>>> = Arc::new(Mutex::new(None));
    let backend_for_setup = Arc::clone(&backend_process);
    let backend_for_window = Arc::clone(&backend_process);
    let backend_for_exit = Arc::clone(&backend_process);
    let pending_open_file = PendingNoofyOpenFile(Arc::new(Mutex::new(first_noofy_file_from_args(
        std::env::args_os().skip(1),
        None,
    ))));
    let pending_for_single_instance = pending_open_file.clone();
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
        .run(move |app_handle, event| {
            match event {
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
    let metadata = fs::metadata(path).map_err(|e| format!("workflow package is unavailable: {e}"))?;
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
    let launch = backend_launch_spec(&BackendLaunchContext::from_app(app)?)?;

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

    let mut child = command.spawn()?;

    let stdout = child.stdout.take().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::Other,
            "backend stdout pipe was not available",
        )
    })?;
    let stderr = child.stderr.take().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::Other,
            "backend stderr pipe was not available",
        )
    })?;

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
            packaged_mode,
        })
    }
}

fn backend_launch_spec(context: &BackendLaunchContext) -> io::Result<BackendLaunchSpec> {
    if let Some(sidecar) = env_path(context, "NOOFY_BACKEND_SIDECAR") {
        let developer_overrides_enabled =
            env_flag(&context.env, "NOOFY_ENABLE_DEVELOPER_BACKEND_OVERRIDES");
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

    if context.packaged_mode && !env_flag(&context.env, "NOOFY_ENABLE_DEVELOPER_BACKEND_OVERRIDES")
    {
        return packaged_backend_launch_spec(context);
    }

    source_backend_launch_spec(context)
}

fn packaged_backend_launch_spec(context: &BackendLaunchContext) -> io::Result<BackendLaunchSpec> {
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
    if let Some(path) = env_path(context, "NOOFY_PACKAGED_RUNTIME_DIR") {
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

fn apply_backend_environment(
    context: &BackendLaunchContext,
    spec: &mut BackendLaunchSpec,
    require_packaged_python: bool,
) -> io::Result<()> {
    set_env_default(
        context,
        spec,
        "COMFYUI_RUNTIME_MODE",
        OsString::from("managed"),
    );

    let layout = packaged_runtime_layout(context);
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

    set_env(spec, "PYTHONNOUSERSITE", OsString::from("1"));
    Ok(())
}

fn packaged_resource_dir(context: &BackendLaunchContext, root_dir: &Path) -> Option<PathBuf> {
    if let Some(resource_dir) = &context.resource_dir {
        return Some(resource_dir.clone());
    }
    root_dir
        .parent()
        .filter(|_| {
            root_dir
                .file_name()
                .is_some_and(|name| name == NOOFY_RUNTIME_RESOURCE_DIR)
        })
        .map(Path::to_path_buf)
}

fn packaged_env_removals() -> Vec<String> {
    [
        "COMFYUI_REPO_DIR",
        "COMFYUI_PYTHON_EXECUTABLE",
        "CONDA_PREFIX",
        "NOOFY_BACKEND_DIR",
        "NOOFY_BACKEND_PYTHON",
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

fn env_flag(env: &HashMap<String, OsString>, key: &str) -> bool {
    env.get(key)
        .and_then(|value| value.to_str())
        .map(|value| matches!(value, "1" | "true" | "TRUE" | "yes" | "YES"))
        .unwrap_or(false)
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

    fn context(resource_dir: PathBuf, packaged_mode: bool) -> BackendLaunchContext {
        BackendLaunchContext {
            env: HashMap::new(),
            manifest_dir: temp_dir("manifest").join("frontend").join("src-tauri"),
            current_exe: resource_dir.join("Noofy"),
            resource_dir: Some(resource_dir),
            packaged_mode,
        }
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
        touch(
            &runtime
                .join("backend")
                .join("app")
                .join("workflows")
                .join("packages")
                .join(".keep"),
        );
        touch(&runtime.join("comfyui").join("main.py"));

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
}
