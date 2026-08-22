use std::process::{Command, Stdio};
use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::Emitter;
use tauri::Manager;

#[tauri::command]
fn which_core() -> Option<String> {
    // 顺序：用户显式路径（前端传入并缓存） -> PATH 上的 vision-relay(.exe)
    let name = if cfg!(windows) { "vision-relay.exe" } else { "vision-relay" };
    if let Some(dir) = std::env::var_os("PATH") {
        for d in std::env::split_paths(&dir) {
            let cand = d.join(name);
            if cand.is_file() {
                return cand.to_str().map(|s| s.to_string());
            }
        }
    }
    None
}

fn core_command(core: &str) -> Command {
    let mut cmd = Command::new(core);
    // Windows GBK 控制台下强制子进程 UTF-8（spec 风险 4）；CREATE_NO_WINDOW 隐藏控制台闪窗。
    // 只用 CREATE_NO_WINDOW：与 DETACHED_PROCESS 属同类"控制台创建"标志（reconcile.py 亦注明
    // 互斥），对等待型/分离型子进程各自单独使用即可。
    cmd.env("PYTHONIOENCODING", "utf-8");
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    cmd
}

fn spawn_core(core: &str, args: &[String], stdin: Option<String>) -> Result<String, String> {
    let mut cmd = core_command(core);
    cmd.args(args)
        .stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = cmd.spawn().map_err(|e| format!("spawn {core}: {e}"))?;
    if let Some(data) = stdin {
        use std::io::Write;
        if let Some(mut si) = child.stdin.take() {
            let _ = si.write_all(data.as_bytes());
            let _ = si.flush();
        }
    }
    let out = child.wait_with_output().map_err(|e| e.to_string())?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    if stdout.trim().is_empty() {
        let stderr = String::from_utf8_lossy(&out.stderr).to_string();
        return Err(format!("core produced no output; stderr: {stderr}"));
    }
    Ok(stdout)
}

#[tauri::command]
fn run_core(core_path: String, args: Vec<String>, stdin: Option<String>) -> Result<String, String> {
    spawn_core(&core_path, &args, stdin)
}

#[tauri::command]
fn start_core_detached(core_path: String) -> Result<(), String> {
    let mut cmd = core_command(&core_path);
    cmd.args(["start", "--detach"])
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
    cmd.spawn().map(|_| ()).map_err(|e| e.to_string())
}

#[tauri::command]
fn open_path(path: String) -> Result<(), String> {
    // 用系统默认程序打开文件/定位（2026-08-23 决策③：只到文件，不做行号）
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        std::process::Command::new("cmd")
            .args(["/C", "start", "", &path])
            .creation_flags(CREATE_NO_WINDOW)
            .spawn()
            .map_err(|e| e.to_string())?;
    }
    #[cfg(not(windows))]
    {
        let opener = if cfg!(target_os = "macos") { "open" } else { "xdg-open" };
        std::process::Command::new(opener).arg(&path).spawn().map_err(|e| e.to_string())?;
    }
    Ok(())
}

pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let open = MenuItem::with_id(app, "open", "打开主界面", true, None::<&str>)?;
            let toggle = MenuItem::with_id(app, "toggle", "路由：开/关", true, None::<&str>)?;
            let diag = MenuItem::with_id(app, "diag", "诊断报告", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出（停止服务）", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &toggle, &diag, &quit])?;
            TrayIconBuilder::with_id("main")
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .on_menu_event(|app, event| {
                    match event.id().as_ref() {
                        "open" => { if let Some(w) = app.get_webview_window("main") { let _ = w.show(); let _ = w.set_focus(); } }
                        "toggle" | "diag" => { if let Some(w) = app.get_webview_window("main") { let _ = w.emit("tray", event.id().as_ref()); let _ = w.show(); } }
                        "quit" => { if let Some(core) = which_core() { let _ = spawn_core(&core, &["stop".into()], None); } app.exit(0); }
                        _ => {}
                    }
                })
                .build(app)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![which_core, run_core, start_core_detached, open_path])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
