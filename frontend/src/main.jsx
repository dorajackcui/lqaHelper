import { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { Camera, Folder, FolderOpen, Keyboard, LoaderCircle, Plus, Settings2, SlidersHorizontal, Terminal, TriangleAlert, Upload } from "lucide-react";
import { Toaster, toast } from "sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { EmptyState, Keycaps } from "./Common";
import { AgentSettings, CaptureSettings, ProjectForm, agentLabels } from "./Settings";
import { Shots, Issues, RelatedEntries } from "./Results";
import { EditorHost } from "./Editor";
import { api, readFile, reportError } from "./api";

function Workspace() {
  const [state, setState] = useState(null), [loadError, setLoadError] = useState(null), [exited, setExited] = useState(false);
  const [panel, setPanel] = useState(null), [pane, setPane] = useState("shots"), [showResolved, setShowResolved] = useState(false);
  const seq = useRef(0), signature = useRef(""), fileInput = useRef(null), entryCache = useRef(null);
  const refresh = useCallback(async () => {
    const number = ++seq.current;
    const next = await api("/api/state");
    if (number !== seq.current) return;
    const value = JSON.stringify(next);
    if (signature.current !== value) { signature.current = value; setState(next); }
    setLoadError(null);
  }, []);
  useEffect(() => {
    if (exited) return;
    const update = () => { if (!document.hidden) refresh().catch(setLoadError); };
    refresh().catch(setLoadError);
    const timer = setInterval(update, 2200);
    document.addEventListener("visibilitychange", update);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", update); };
  }, [refresh, exited]);
  async function importFiles(event) {
    const files = [...event.target.files], project = state.active;
    event.target.value = "";
    try {
      for (const file of files) {
        if (file.size > 48 * 1024 * 1024) throw new Error("每张 PNG 最大为 48 MB。");
        await api("/api/import", { project, name: file.name, data: await readFile(file) });
      }
      if (files.length) toast.success("已归档 " + files.length + " 张截图");
    } catch (err) { reportError(err); } finally { refresh().catch(reportError); }
  }
  if (exited) return <main className="welcome"><h1>工作台已退出</h1><p>双击 Start.cmd 可重新打开。排队的截图会在下次启动时继续处理。</p></main>;
  const project = state?.projects.find(p => p.id === state.active);
  const all = state?.shots.filter(s => s.status === "done").flatMap(shot => shot.issues.map((issue, index) => ({ shot, issue, index }))) || [];
  const common = { project, all, refresh, showResolved, toggleResolved: () => setShowResolved(v => !v) };
  const closePanel = () => setPanel(null);
  return <><a className="skip-link" href="#work-area" onClick={e => { e.preventDefault(); document.getElementById("work-area").focus(); }}>跳到工作区</a>
    <div className="app-shell"><aside className="sidebar">
      <div className="brand">LQA Helper<span aria-hidden="true" className="brand-dot">.</span></div>
      <div className="project-nav"><div className="nav-heading"><span>项目</span><Button id="add-project" variant="ghost" size="icon-sm" disabled={!state} aria-label="新建项目" title="新建项目" onClick={() => setPanel("new")}><Plus /></Button></div>
        <nav id="projects" aria-label="项目">{state?.projects.map(p => {
          const active = p.id === state.active, Icon = active ? FolderOpen : Folder;
          return <Button key={p.id} variant={active ? "secondary" : "ghost"} className="project-link" data-project={p.id} aria-current={active ? "page" : undefined} title={p.name}
            onClick={() => api("/api/activate", { id: p.id }).then(refresh).catch(reportError)}><Icon /><span className="truncate">{p.name}</span></Button>;
        })}</nav></div>
      <div className="side-bottom"><Separator /><Button className="side-action" variant="ghost" id="settings-open" aria-label="截屏设置" title="截屏设置" disabled={!state} onClick={() => setPanel("capture")}><SlidersHorizontal /><span className="side-caption">截屏设置</span></Button>
        <Button className="side-action" variant="ghost" id="agent-open" title="Codex 设置" disabled={!state} onClick={() => setPanel("agent")}><Terminal />Codex <small id="agent-status" data-state={state?.agent}>{agentLabels[state?.agent] || "读取中"}</small></Button></div>
    </aside>
    <main id="work-area" tabIndex={-1}><header className="topbar"><div className="work-frame">
      <Button variant="ghost" size="sm" id="key-hint" disabled={!state} title="更改截屏快捷键" onClick={() => setPanel("capture")}><Keyboard />截屏快捷键 <span id="window-keys"><Keycaps value={state?.hotkeys?.window} /></span></Button></div></header>
      <div className="work-frame content">
        {(loadError || state?.warnings.length > 0 || state?.captureError) && <Alert variant="destructive" id="warnings"><TriangleAlert /><AlertDescription>{[loadError?.message, ...(state?.warnings || []), state?.captureError].filter(Boolean).join("　")}</AlertDescription></Alert>}
        {!state && <p className="hint flex items-center gap-2" role="status"><LoaderCircle className="size-4 animate-spin" />正在读取工作台…</p>}
        {state && !project && <section id="welcome"><EmptyState icon={Camera} title="开始一个 LQA 项目" action={<Button id="first-project" onClick={() => setPanel("new")}><Plus />新建项目</Button>}>选好项目目录和参考文本。测试时按快捷键截图，Codex 会在后台整理问题。</EmptyState></section>}
        {project && <section id="workspace">
          <div className="project-header"><div className="project-heading"><h1 id="project-title">{project.name}</h1>{project.description && <p id="project-desc">{project.description}</p>}</div>
            <div className="actions"><Button id="edit-project" variant="outline" onClick={() => setPanel("edit")}><Settings2 />项目设置</Button><Button id="import-button" onClick={() => fileInput.current.click()}><Upload />导入截图</Button></div>
          </div>
          <Tabs value={pane} onValueChange={setPane} className="workspace-tabs">
            <div className="tabs-rule"><TabsList variant="line" aria-label="项目视图">
              <TabsTrigger id="shots-tab" aria-controls="shots-pane" value="shots">截图 <Badge variant="secondary">{state.shots.length}</Badge></TabsTrigger>
              <TabsTrigger id="issues-tab" aria-controls="issues-pane" value="issues">待处理问题 <Badge variant="secondary">{all.filter(x => !x.issue.resolved).length}</Badge></TabsTrigger>
              <TabsTrigger id="entries-tab" aria-controls="entries-pane" value="entries">相关条目</TabsTrigger>
            </TabsList></div>
            <TabsContent value="shots" id="shots-pane" aria-labelledby="shots-tab"><Shots state={state} refresh={refresh} /></TabsContent>
            <TabsContent value="issues" id="issues-pane" aria-labelledby="issues-tab"><Issues {...common} /></TabsContent>
            <TabsContent value="entries" id="entries-pane" aria-labelledby="entries-tab"><RelatedEntries {...common} key={project.id} cache={entryCache} editProject={() => setPanel("edit")} /></TabsContent>
          </Tabs>
        </section>}
      </div></main></div>
    <input ref={fileInput} id="import-input" type="file" accept="image/png" multiple hidden onChange={importFiles} />
    {["new","edit"].includes(panel) && <ProjectForm key={panel + (panel === "edit" ? project?.id : "")} project={panel === "edit" ? project : null} close={closePanel} refresh={refresh} />}
    {panel === "capture" && <CaptureSettings state={state} close={closePanel} refresh={refresh} onQuit={() => setExited(true)} />}
    {panel === "agent" && <AgentSettings state={state} close={closePanel} refresh={refresh} />}
    <EditorHost refresh={refresh} />
  </>;
}

createRoot(document.getElementById("root")).render(<><Workspace /><Toaster theme="light" position="bottom-center" closeButton containerAriaLabel="通知" toastOptions={{ duration: 3500, closeButtonAriaLabel: "关闭通知" }} /></>);
