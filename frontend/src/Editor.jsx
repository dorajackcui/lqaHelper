import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowUpRight, Crop, Square, StickyNote, Tag, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { AlertDialog, AlertDialogContent, AlertDialogHeader, AlertDialogTitle, AlertDialogDescription,
  AlertDialogFooter, AlertDialogCancel, AlertDialogAction } from "@/components/ui/alert-dialog";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Toggle } from "@/components/ui/toggle";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, displayTime, reportError } from "./api";
import { attachCanvas } from "./annotation";

const presets = ["超框", "UI 问题", "漏译", "术语"];
const tools = [["rect","红框",Square], ["arrow","箭头",ArrowUpRight], ["crop","裁剪",Crop], ["tag","标签",Tag], ["note","备注",StickyNote]];
const fromHash = () => /^#shot=([a-f0-9]{32})$/.exec(location.hash)?.[1] || null;
function replaceHash(id) {
  history.replaceState(null, "", location.pathname + location.search + (id ? "#shot=" + id : ""));
}

function Confirmation({ title, detail, action, cancel, confirm }) {
  return <AlertDialog open onOpenChange={open => { if (!open) cancel(); }}>
    <AlertDialogContent><AlertDialogHeader><AlertDialogTitle>{title}</AlertDialogTitle>
      <AlertDialogDescription>{detail}</AlertDialogDescription></AlertDialogHeader>
      <AlertDialogFooter><AlertDialogCancel>取消</AlertDialogCancel><AlertDialogAction variant="destructive" onClick={confirm}>{action}</AlertDialogAction></AlertDialogFooter>
    </AlertDialogContent></AlertDialog>;
}

export function EditorHost({ refresh }) {
  const [id, setId] = useState(fromHash), [pending, setPending] = useState(null);
  const current = useRef(id), opener = useRef(null), isDirty = useRef(() => false);
  const switchTo = useCallback(next => {
    current.current = next;
    replaceHash(next); setPending(null); setId(next);
    if (!next && opener.current?.isConnected) opener.current.focus({ preventScroll: true });
  }, []);
  const request = useCallback(next => {
    if (next === current.current) return;
    if (current.current && isDirty.current()) {
      replaceHash(current.current); setPending({ next }); return;
    }
    if (next && !current.current) opener.current = document.activeElement;
    switchTo(next);
  }, [switchTo]);
  const finished = useCallback(shotId => { if (current.current === shotId) switchTo(null); }, [switchTo]);
  useEffect(() => {
    const onHash = () => request(fromHash());
    const beforeUnload = e => { if (isDirty.current()) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("hashchange", onHash); window.addEventListener("beforeunload", beforeUnload);
    onHash();
    return () => { window.removeEventListener("hashchange", onHash); window.removeEventListener("beforeunload", beforeUnload); };
  }, [request]);
  return <>{id && <LeasedEditor key={id} id={id} refresh={refresh} finished={finished} close={() => request(null)} isDirty={isDirty} />}
    {pending && <Confirmation title="放弃这次标注？" detail="本次尚未保存的修改会丢失。" action="放弃修改" cancel={() => setPending(null)} confirm={() => switchTo(pending.next)} />}</>;
}

function LeasedEditor({ id, finished, ...props }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    let live = true;
    const session = { id, token: crypto.randomUUID() };
    const release = () => api("/api/editor/close", session, { keepalive: true }).catch(() => {});
    async function hold() {
      try { return await api("/api/editor/open", session); }
      finally { if (!live) release(); }
    }
    function failed(err) { if (live) { finished(id); reportError(err); } }
    async function open() {
      const { shot, project } = await hold();
      if (!live) return;
      const image = new Image();
      image.src = "/image/" + id + "/original";
      await image.decode();
      if (live) setData({ shot, project, image });
    }
    const renew = () => hold().catch(err => { if (err.status === 404) failed(err); });
    const onShow = e => { if (e.persisted) renew(); };
    open().catch(failed);
    const timer = setInterval(renew, 30000);
    window.addEventListener("pagehide", release); window.addEventListener("pageshow", onShow);
    return () => { live = false; clearInterval(timer); window.removeEventListener("pagehide", release); window.removeEventListener("pageshow", onShow); release(); };
  }, [id, finished]);
  return data && <EditorForm {...props} data={data} finished={() => finished(id)} />;
}

function EditorForm({ data: { shot, project, image }, close, finished, isDirty, refresh }) {
  const [tool, setTool] = useState("rect"), [tags, setTags] = useState(shot.tags), [tag, setTag] = useState("");
  const [note, setNote] = useState(shot.note), [deleting, setDeleting] = useState(false), [busy, setBusy] = useState(false);
  const canvas = useRef(null), tagInput = useRef(null), noteInput = useRef(null), saving = useRef(false);
  const bind = useCallback(node => {
    canvas.current?.detach(); canvas.current = node ? attachCanvas(node, image, shot, setTool) : null;
  }, [image, shot]);
  useLayoutEffect(() => {
    isDirty.current = () => canvas.current?.changed() || JSON.stringify(tags) !== JSON.stringify(shot.tags) || note !== shot.note || !!tag.trim();
    return () => { isDirty.current = () => false; };
  }, [tags, note, tag, shot, isDirty]);
  useEffect(() => { if (tool === "tag") tagInput.current?.focus(); if (tool === "note") noteInput.current?.focus(); }, [tool]);
  function addTag() {
    const value = tag.trim();
    if (value && !tags.includes(value)) setTags([...tags, value]);
    setTag("");
  }
  async function save(remove = false) {
    if (saving.current || !canvas.current) return;
    saving.current = true; setBusy(true);
    try {
      let message;
      if (remove) {
        const result = await api("/api/delete", { id: shot.id, revision: shot.revision });
        message = result.cleaned ? "截图已删除" : "截图已移除。部分文件暂时无法清理，下次启动时会重试。";
      } else {
        const evidence = canvas.current.result();
        const allTags = [...new Set([...tags, tag.trim()].filter(Boolean))];
        await api("/api/edit", { id: shot.id, revision: shot.revision, ...evidence, tags: allTags, note });
        message = "截图标注已保存";
      }
      finished(); await refresh(); toast.success(message);
    } catch (err) { reportError(err); } finally { saving.current = false; setBusy(false); }
  }
  return <><Dialog open onOpenChange={open => { if (!open && !saving.current) close(); }}>
    <DialogContent id="editor" showCloseButton={false} aria-labelledby="editor-title" aria-describedby={undefined} aria-busy={busy} inert={busy} className="editor-panel"
      onPointerDownOutside={e => e.preventDefault()} onCloseAutoFocus={e => e.preventDefault()}
      onOpenAutoFocus={e => { e.preventDefault(); document.getElementById("save-shot")?.focus(); }}
      onEscapeKeyDown={e => { if (busy || canvas.current?.cancelDrawing()) e.preventDefault(); }}
      onKeyDown={e => {
        if (busy || !e.ctrlKey || e.nativeEvent.isComposing) return;
        if (e.key === "Enter") { e.preventDefault(); save(); }
        else if (e.key.toLowerCase() === "z" && !e.target.closest("input,textarea")) { e.preventDefault(); canvas.current?.undo(); }
      }}>
      <div className="editor-heading"><div className="editor-title"><small id="editor-project" className="hint">{project}</small>
        <DialogTitle id="editor-title">截图 · {displayTime(shot)}</DialogTitle></div>
        <Button id="delete-shot" variant="ghost" size="sm" className="delete-action" disabled={busy} onClick={() => setDeleting(true)}><Trash2 />删除截图</Button>
        <Button id="save-shot" size="sm" title="Ctrl + Enter" disabled={busy} onClick={() => save()}>保存标注</Button>
        <Button id="editor-close" variant="ghost" size="icon" aria-label="关闭截图" disabled={busy} onClick={close}><X /></Button>
      </div>
      <div className="tools" role="group" aria-label="截图工具栏">{tools.map(([id,label,Icon]) =>
        <Toggle key={id} id={id} data-tool={id} pressed={tool === id} disabled={busy} aria-label={label}
          aria-expanded={["tag","note"].includes(id) ? tool === id : undefined} aria-controls={["tag","note"].includes(id) ? id + "-panel" : undefined}
          onPressedChange={() => canvas.current?.setTool(tool === id && ["tag","note"].includes(id) ? "rect" : id)}>
          <Icon />{label}{id === "tag" && !!tags.length && <Badge id="tag-count" variant="secondary">{tags.length}</Badge>}{id === "note" && !!note.trim() && <span id="note-dot" aria-label="已填写" />}</Toggle>)}</div>
      {tool === "tag" && <div id="tag-panel" className="tool-panel"><div className="actions">{[...new Set([...presets, ...tags])].map(t =>
        <Toggle key={t} size="sm" variant="outline" pressed={tags.includes(t)} data-tag={t} onPressedChange={on => setTags(on ? [...tags,t] : tags.filter(x => x !== t))}>{t}</Toggle>)}</div>
        <div className="input-row"><Input id="new-tag" ref={tagInput} value={tag} maxLength={40} aria-label="自定义标签" placeholder="自定义标签" onChange={e => setTag(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter" && !e.ctrlKey && !e.nativeEvent.isComposing) { e.preventDefault(); addTag(); } }} /><Button size="sm" variant="outline" id="add-tag" onClick={addTag}>添加</Button></div></div>}
      {tool === "note" && <div id="note-panel" className="tool-panel"><Textarea id="shot-note" ref={noteInput} aria-label="截图备注" rows={2} maxLength={4000} value={note} onChange={e => setNote(e.target.value)} placeholder="例如：按钮右侧文字被截断" /></div>}
      {tool === "crop" && <p id="crop-help" className="tool-panel hint">拖出要保留的范围。双击可恢复整张截图。</p>}
      <div className="stage" id="stage" inert={busy}><canvas id="canvas" ref={bind} aria-label="截图标注画布" /></div>
    </DialogContent></Dialog>
    {deleting && <Confirmation title="永久删除这张截图？" detail="原图、标注和这张截图对应的 LQA 问题都会一起删除。" action="删除截图" cancel={() => setDeleting(false)} confirm={() => save(true)} />}
  </>;
}
