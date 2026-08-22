use std::process::{Command, Stdio};

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

fn spawn_core(core: &str, args: &[String], stdin: Option<String>) -> Result<String, String> {
    let mut cmd = Command::new(core);
    cmd.args(args)
        .stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env("PYTHONIOENCODING", "utf-8"); // Windows GBK 控制台下强制 UTF-8（spec 风险 4）
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        const DETACHED_PROCESS: u32 = 0x0000_0008;
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW | DETACHED_PROCESS);
    }
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
    let mut cmd = Command::new(core_path);
    cmd.args(["start", "--detach"])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .env("PYTHONIOENCODING", "utf-8");
    #[cfg(windows)]
    {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
    cmd.spawn().map(|_| ()).map_err(|e| e.to_string())
}

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![which_core, run_core, start_core_detached])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
