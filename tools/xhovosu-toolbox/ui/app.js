/* ---------------------------------------------------------- 通信 */
let api = null;
let logCursor = 0;
let pollTimer = null;

function py(name, ...args) {
  if (!api) return Promise.resolve({ ok: false, msg: "python 未就绪" });
  return api[name](...args).catch((e) => ({ ok: false, msg: String(e) }));
}

/* ---------------------------------------------------------- UI 工具 */
const $ = (id) => document.getElementById(id);

function setStatus(t) { $("headStatus").textContent = t; }

let toastTimer = null;
function toast(msg, kind) {
  let el = document.querySelector(".toast");
  if (!el) {
    el = document.createElement("div");
    el.className = "toast";
    document.body.append(el);
  }
  el.textContent = msg;
  el.className = "toast " + (kind || "");
  requestAnimationFrame(() => el.classList.add("on"));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("on"), 2400);
}

function markStep(n, done) {
  const card = document.querySelector('.step[data-step="' + n + '"]');
  if (card) card.classList.toggle("done", !!done);
}

function busy(on) {
  document.querySelectorAll("button.act").forEach((b) => (b.disabled = !!on));
  document.querySelectorAll("button.mini").forEach((b) => (b.disabled = !!on));
  setStatus(on ? "执行中…" : "就绪");
}

/* 依次执行的包装：跑完自动解除 busy */
async function act(fn) {
  if (busy.locked) { toast("上一个操作还没结束", "err"); return; }
  busy.locked = true;
  busy(true);
  try { await fn(); }
  catch (e) { toast(String(e), "err"); }
  finally {
    busy.locked = false;
    busy(false);
  }
}

/* ---------------------------------------------------------- 日志轮询 */
async function pollLogs() {
  const r = await py("pull_logs", logCursor);
  if (!r || !r.ok) return;
  if (r.lines && r.lines.length) {
    const pre = $("log");
    pre.textContent += r.lines.join("\n") + "\n";
    pre.scrollTop = pre.scrollHeight;
    logCursor = r.total;
  }
}

/* ---------------------------------------------------------- 各步骤 */
async function doEnv() {
  const r = await py("check_env");
  $("envText").textContent = r.ok
    ? "adb 与 fastboot 就绪"
    : "缺少 " + (!r.adb ? "adb " : "") + (!r.fastboot ? "fastboot" : "");
  markStep(1, r.ok);
  toast(r.ok ? "环境正常" : "缺少工具", r.ok ? "ok" : "err");
}

async function doTools() {
  toast("开始下载 platform-tools");
  const r = await py("download_tools");
  toast(r.ok ? "下载完成" : "下载失败", r.ok ? "ok" : "err");
  if (r.ok) markStep(1, true);
}

async function doScan() {
  const r = await py("scan_device");
  if (!r.ok) { toast(r.msg || "没检测到设备", "err"); return; }
  const i = r.info;
  $("iSerial").textContent = i.serial || "—";
  $("iModel").textContent = (i.brand + " " + i.model).trim() || "—";
  $("iAndroid").textContent = i.android || "—";
  $("iSlot").textContent = i.is_ab ? "A/B " + i.slot : "仅 A";
  $("iKmi").textContent = i.kmi || "未匹配";
  $("iRoot").textContent = i.rooted ? "已 ROOT" : "未 ROOT";
  markStep(1, true);
  toast(i.kmi ? "设备识别完成" : "内核不在支持列表", i.kmi ? "ok" : "err");
}

async function doPickImage() {
  const f = await py("pick_file", "image");
  if (!f.ok) return;
  const r = await py("set_image", f.path);
  if (!r.ok) { toast(r.msg, "err"); return; }
  $("imageText").textContent = r.name + "  (" + (r.size / 1024 / 1024).toFixed(1) + " MB)";
  markStep(2, true);
  toast("镜像已选择", "ok");
}

async function doDump() {
  const f = await py("pick_file", "save");
  if (!f.ok) return;
  const r = await py("dump_image", f.path);
  if (!r.ok) { toast(r.msg || "导出失败", "err"); return; }
  $("imageText").textContent = r.name + "  (" + (r.size / 1024 / 1024).toFixed(1) + " MB)";
  markStep(2, true);
  toast("导出完成", "ok");
}

async function doPatch() {
  const r = await py("patch");
  if (r.ok) {
    $("patchText").textContent = "kernel 段 " + r.kernelSize.toFixed(2) + " MB，待替换";
    toast("已解包，等待替换内核", "ok");
  } else if (r.needMagiskboot) {
    $("patchText").textContent = "缺少 magiskboot.exe";
    toast("需要 magiskboot", "err");
  } else {
    toast(r.msg || "修补失败", "err");
  }
}

async function doBootloader() {
  toast("正在重启到 bootloader");
  const r = await py("reboot_bootloader");
  toast(r.ok ? "已进入 Fastboot" : (r.msg || "进入失败"), r.ok ? "ok" : "err");
}

async function doFlash() {
  const go = confirm("即将写入 init_boot。\n\n确认镜像是本机型的官方版本，电量 50% 以上。\n出问题需要能刷回官方固件。\n\n继续？");
  if (!go) return;
  const r = await py("flash");
  if (r.ok) {
    $("flashText").textContent = "已刷入并重启";
    markStep(4, true);
    toast("刷入成功", "ok");
  } else {
    toast(r.msg || "刷入失败", "err");
  }
}

async function doPickApk() {
  const f = await py("pick_file", "apk");
  if (!f.ok) return;
  const r = await py("install_app", f.path);
  if (r.ok) {
    $("appText").textContent = "安装完成";
    markStep(5, true);
    toast("面具已安装", "ok");
  } else {
    toast(r.msg || "安装失败", "err");
  }
}

/* ---------------------------------------------------------- 启动序列 */
function bootSequence() {
  const boot = $("boot");
  const app = $("app");

  // 欢迎文字：逐字浮入，然后整块淡出
  const line = $("bootLine");
  const text = line.textContent;
  line.textContent = "";
  [...text].forEach((ch, i) => {
    const s = document.createElement("span");
    s.textContent = ch;
    s.style.display = "inline-block";
    s.style.opacity = "0";
    s.style.transform = "translateY(14px) scale(.9)";
    s.style.transition = "opacity .42s var(--ease) " + (i * 52) + "ms, transform .5s var(--ease) " + (i * 52) + "ms";
    line.append(s);
    requestAnimationFrame(() => {
      s.style.opacity = "1";
      s.style.transform = "translateY(0) scale(1)";
    });
  });

  setTimeout(() => {
    boot.classList.add("gone");
    app.classList.add("in");
    app.setAttribute("aria-hidden", "false");
    setStatus("就绪");
  }, 2350);
}

/* ---------------------------------------------------------- 绑定 */
window.addEventListener("pywebviewready", () => {
  api = window.pywebview.api;
  pollTimer = setInterval(pollLogs, 600);
  doEnv();
});

document.addEventListener("DOMContentLoaded", () => {
  bootSequence();

  $("btnEnv").onclick = () => act(doEnv);
  $("btnTools").onclick = () => act(doTools);
  $("btnScan").onclick = () => act(doScan);
  $("btnImage").onclick = () => act(doPickImage);
  $("btnDump").onclick = () => act(doDump);
  $("btnPatch").onclick = () => act(doPatch);
  $("btnBootloader").onclick = () => act(doBootloader);
  $("btnFlash").onclick = () => act(doFlash);
  $("btnApp").onclick = () => act(doPickApk);
  $("btnClearLog").onclick = () => { $("log").textContent = ""; };

  // 玻璃卡轻微跟随指针倾斜，增强液态感
  document.querySelectorAll(".glass").forEach((card) => {
    card.addEventListener("mousemove", (e) => {
      const r = card.getBoundingClientRect();
      const dx = (e.clientX - r.left) / r.width - 0.5;
      const dy = (e.clientY - r.top) / r.height - 0.5;
      card.style.transform =
        "perspective(760px) rotateY(" + (dx * 3.2).toFixed(2) + "deg) " +
        "rotateX(" + (-dy * 2.6).toFixed(2) + "deg)";
    });
    card.addEventListener("mouseleave", () => { card.style.transform = ""; });
  });
});
