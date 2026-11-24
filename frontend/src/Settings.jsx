import { useEffect, useRef, useState } from "react";
import { FileText, LoaderCircle, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { CloseButton } from "./Common";
import { api, reportError } from "./api";

function Panel({ id, title, close, busy, onEscape, children }) {
  const [opener] = useState(() => document.activeElement);
  return <Dialog open onOpenChange={open => { if (!open && !busy) close(); }}>
    <DialogContent id={id} showCloseButton={false} aria-describedby={undefined} className="settings-panel" onEscapeKeyDown={onEscape}
      onOpenAutoFocus={event => { if (id === "project-dialog") { event.preventDefault(); document.getElementById("p-name")?.focus(); } }}
      onCloseAutoFocus={event => { event.preventDefault(); if (opener?.isConnected) opener.focus(); }}>
      <DialogHeader className="panel-title"><DialogTitle>{title}</DialogTitle><CloseButton /></DialogHeader>{children}
    </DialogContent>
  </Dialog>;
}

const fileTypes = ".xlsx,.xls,.xlsm,.csv,.tsv,.txt,.md";
export function ProjectForm({ project, close, refresh }) {
  const [ref, setRef] = useState(project?.reference || null), [drag, setDrag] = useState(false), [busy, setBusy] = useState(false);
  const [folder, setFolder] = useState(project?.folder || "");
  const fileInput = useRef(null);
  function choose(file) {
    if (!file) return;
    if (!fileTypes.split(",").some(ext => file.name.toLowerCase().endsWith(ext))) {
      toast.error("请选择 Excel（XLSX、XLS 或 XLSM）、CSV、TSV、TXT 或 MD 文件。"); return;
    }
    setRef(file);
  }
  async function save(event) {
    event.preventDefault();
    if (busy) return;
    const fields = new FormData(event.currentTarget);
    const body = { id: project?.id, name: fields.get("name"), description: fields.get("description"),
      prompt: fields.get("prompt"), folder, removeReference: !ref };
    setBusy(true);
    try {
      if (ref instanceof File) {
        const form = new FormData();
        form.append("project", JSON.stringify(body)); form.append("reference", ref);
        await api("/api/project", form);
      } else await api("/api/project", body);
      await refresh(); close(); toast.success("项目已保存");
    } catch (err) { reportError(err); } finally { setBusy(false); }
  }
  return <Panel id="project-dialog" title={project ? "项目设置" : "新建项目"} close={close} busy={busy}>
    <form id="project-form" className="form-stack" onSubmit={save}>
      <div className="field"><Label htmlFor="p-name">项目名称</Label><Input id="p-name" name="name" required maxLength={80} defaultValue={project?.name} placeholder="例如：游戏客户端 · 简体中文" /></div>
      <div className="field"><Label htmlFor="p-desc">项目说明 <small>选填</small></Label><Input id="p-desc" name="description" maxLength={4000} defaultValue={project?.description} placeholder="测试范围、版本或目标语言" /></div>
      <div className="field"><Label htmlFor="p-folder">项目归档目录</Label><div className="input-row">
        <Input id="p-folder" value={folder} onChange={e => setFolder(e.target.value)} disabled={!!project} aria-describedby="archive-layout" placeholder="默认保存在本机工作区" />
        {!project && <Button type="button" variant="outline" id="pick-folder" onClick={() => api("/api/folder-picker", {}).then(({ folder }) => { if (folder) setFolder(folder); }).catch(reportError)}>选择文件夹…</Button>}
      </div><p id="archive-layout" className="hint">截图存入 captures/，参考文件存入同级的 references/。</p></div>
      <div className="field"><Label htmlFor="p-prompt">给 Codex 的要求 <small>选填</small></Label><Textarea id="p-prompt" name="prompt" rows={2} maxLength={16000} defaultValue={project?.prompt} placeholder="例如：优先检查按钮文字截断，附上匹配的词条 ID。" /></div>
      <div className="field"><Label htmlFor="p-ref">参考文本 <small id="ref-formats">选填 · Excel / CSV / TSV / TXT / MD</small></Label>
        <div className="input-row"><Button type="button" variant="outline" id="pick-ref" className="file-button" data-drag={drag} aria-labelledby="ref-action" aria-describedby="ref-formats ref-name"
          onClick={() => fileInput.current.click()} onDragOver={e => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); e.dataTransfer.dropEffect = "copy"; setDrag(true); } }}
          onDragLeave={e => { if (!e.currentTarget.contains(e.relatedTarget)) setDrag(false); }}
          onDrop={e => { e.preventDefault(); setDrag(false); const { files, items } = e.dataTransfer;
            if (files.length !== 1 || [...items].some(x => x.webkitGetAsEntry?.()?.isDirectory)) { toast.error("请拖入一个参考文本文件。"); return; }
            choose(files[0]); }}><FileText /><span id="ref-action">{ref ? "更换文件…" : "选择文件…"}</span><span id="ref-name" className="truncate text-muted-foreground" aria-live="polite" title={ref?.name}>{ref?.name || "或拖入术语表"}</span></Button>
          {ref && <Button id="remove-ref" type="button" variant="ghost" onClick={() => setRef(null)}>移除</Button>}</div>
        <input id="p-ref" ref={fileInput} type="file" accept={fileTypes} hidden onChange={e => { choose(e.target.files[0]); e.target.value = ""; }} />
      </div>
      <DialogFooter><Button type="button" variant="outline" data-close onClick={close} disabled={busy}>取消</Button><Button type="submit" disabled={busy}>{busy && <LoaderCircle className="animate-spin" />}{busy ? ref instanceof File ? "正在导入参考文件…" : "保存中…" : "保存项目"}</Button></DialogFooter>
    </form>
  </Panel>;
}

const shortcuts = { window: "截取当前窗口", region: "框选区域" };
let keysRequest;
export function CaptureSettings({ state, close, refresh, onQuit }) {
  const [keys, setKeys] = useState(state.hotkeys), [seconds, setSeconds] = useState(state.seconds);
  const [recording, setRecording] = useState(null), [catalog, setCatalog] = useState({}), [error, setError] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => {
    keysRequest ??= api("/api/hotkey-keys").catch(err => { keysRequest = null; throw err; });
    keysRequest.then(setCatalog).catch(err => setError(err.message));
  }, []);
  function record(e, action) {
    if (recording !== action || (e.type === "keyup" && e.code !== "PrintScreen")) return;
    const mods = [e.ctrlKey && "Ctrl", e.altKey && "Alt", e.shiftKey && "Shift", e.metaKey && "Win"].filter(Boolean);
    if (e.key === "Tab" && !mods.length) { setRecording(null); return; }
    e.preventDefault(); e.stopPropagation();
    if (e.key === "Escape" && !mods.length) { setRecording(null); return; }
    if (e.repeat || ["Control","Alt","AltGraph","Shift","Meta","OS"].includes(e.key)) return;
    const name = Object.keys(catalog).find(key => catalog[key] === e.keyCode) || Object.keys(catalog).find(key => key === e.code);
    if (!name) { setError(e.code === "F12" ? "F12 是 Windows 的调试保留键，请换一个键。" : "未识别这个键，请换一个键或在框内输入。"); return; }
    setKeys(prev => ({ ...prev, [action]: [...mods, name].join("+") })); setRecording(null); setError("");
    document.getElementById("hotkey-" + action).focus();
  }
  async function save(event) {
    event.preventDefault(); if (busy) return;
    setRecording(null); setBusy(true); setError("");
    try { await api("/api/capture-settings", { seconds: Number(seconds), hotkeys: keys }); await refresh(); close(); toast.success("截屏设置已保存"); }
    catch (err) { setError(err.message); } finally { setBusy(false); }
  }
  return <Panel id="settings-dialog" title="截屏设置" close={close} busy={busy} onEscape={e => { if (recording) { e.preventDefault(); setRecording(null); } }}>
    <form id="settings-form" className="form-stack" onSubmit={save} onInput={() => setError("")}>
      <fieldset className="field"><legend className="font-medium">截屏快捷键</legend>
        {Object.entries(shortcuts).map(([action, label]) => <div className="shortcut-row" key={action}>
          <Label htmlFor={"hotkey-" + action}>{label}</Label><Input id={"hotkey-" + action} value={keys[action]} onChange={e => setKeys({ ...keys, [action]: e.target.value })} list="hotkey-options" maxLength={75} required autoComplete="off" spellCheck={false} />
          <Button id={"record-" + action} type="button" disabled={!Object.keys(catalog).length} variant={recording === action ? "default" : "outline"} aria-label={"录入" + label + "快捷键"} aria-pressed={recording === action}
            onClick={() => setRecording(recording === action ? null : action)} onBlur={() => setRecording(null)} onKeyDown={e => record(e, action)} onKeyUp={e => record(e, action)}>{recording === action ? "请按键…" : "录入"}</Button>
        </div>)}<datalist id="hotkey-options">{Object.keys(catalog).map(key => <option key={key} value={key} />)}</datalist>
        <p className="hint">点「录入」后按想用的键，也可以直接输入 F8 或 Ctrl+Q。</p>
      </fieldset>
      <div className="field"><Label htmlFor="seconds">右上角预览停留时间（秒）</Label><Input className="w-28" id="seconds" type="number" min="1" max="30" step="1" required value={seconds} onChange={e => setSeconds(e.target.value)} /></div>
      <p className="hint" id="capture-status">{state.captureError || (state.capture ? "ShareX 已启动，截图会自动进入当前项目。" : state.active ? "截屏服务尚未启动。" : "创建项目后会自动启动 ShareX。")}</p>
      {error && <Alert variant="destructive" id="settings-error"><TriangleAlert /><AlertDescription>{error}</AlertDescription></Alert>}
      <DialogFooter><Button variant="ghost" className="sm:mr-auto" id="quit-app" type="button" onClick={() => api("/api/quit", {}).then(onQuit).catch(reportError)}>退出工作台</Button>
        {state.active && <Button id="open-folder" variant="outline" type="button" onClick={() => api("/api/open-folder", { project: state.active }).catch(reportError)}>打开项目目录</Button>}
        <Button type="submit" disabled={busy}>{busy ? "正在应用…" : "保存"}</Button></DialogFooter>
    </form>
  </Panel>;
}

export const agentLabels = { checking: "检查中", disconnected: "未连接", ready: "已连接", signed_out: "等待登录", missing: "未找到 CLI", unavailable: "暂不可用" };
const agentText = { ready: "Codex 已连接，截图会自动分析。", signed_out: "正在等待 Codex 登录。完成登录后，队列会自动继续。",
  missing: "本机未找到 Codex CLI。", unavailable: "暂时无法读取 Codex 登录状态。", disconnected: "连接本机 Codex 后，已保存的截图会按顺序分析。" };
export function AgentSettings({ state, close, refresh }) {
  const [busy, setBusy] = useState(false);
  async function action(path, body, message) {
    setBusy(true);
    try { await api(path, body); await refresh(); if (message) toast.success(message); }
    catch (err) { reportError(err); } finally { setBusy(false); }
  }
  return <Panel id="agent-dialog" title="Codex 设置" close={close} busy={busy}>
    <div className="form-stack"><div><p id="agent-detail">{agentText[state.agent] || "正在检查本机 Codex。"}</p><p className="hint mt-2">连接后，排队的截图、标注和项目参考文本将交给 Codex 分析。</p></div>
      <div className="field"><Label htmlFor="reasoning-effort">推理强度 <small>GPT-6.2 Sol</small></Label><NativeSelect id="reasoning-effort" disabled={busy} value={state.reasoningEffort} aria-describedby="effort-hint"
        onChange={e => action("/api/reasoning-effort", { reasoningEffort: e.target.value }, "推理强度已保存")}>{Object.entries(state.reasoningEfforts || {}).map(([value,label]) =>
          <NativeSelectOption key={value} value={value}>{label}</NativeSelectOption>)}</NativeSelect>
        <p className="hint" id="effort-hint">自动保存。所有项目从下一次截图分析开始使用所选强度。</p></div>
      <DialogFooter>{state.connected && <Button id="disconnect" variant="outline" disabled={busy} onClick={() => action("/api/disconnect", {})}>暂停后台分析</Button>}
        {state.agent !== "ready" && <Button id="connect" disabled={busy} onClick={() => action("/api/connect", {})}>{state.agent === "signed_out" ? "打开登录" : "连接 Codex"}</Button>}</DialogFooter>
    </div></Panel>;
}
