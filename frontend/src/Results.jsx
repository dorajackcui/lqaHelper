import { useEffect, useState } from "react";
import { Camera, Download, FileText, ImageOff, ListChecks, LoaderCircle } from "lucide-react";
import { Accordion, AccordionItem, AccordionTrigger, AccordionContent } from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api, displayTime, openShot, reportError } from "./api";
import { EmptyState, Keycaps } from "./Common";

const statuses = { queued: "排队中", running: "分析中", done: "已分析", error: "分析失败" };
const severity = { critical: "严重", major: "主要", minor: "次要" };
const categories = { Untranslated: "漏译", Truncation: "截断", Overflow: "超框", "Encoding/Font": "字体",
  Terminology: "术语", Fluency: "表达", Placeholder: "占位符", "Reference Mismatch": "参考不符", Other: "其他" };

export function Shots({ state, refresh }) {
  if (!state.shots.length) return <EmptyState icon={Camera} title="记录第一张截图">切到测试窗口，按 <Keycaps value={state.hotkeys.window} />。点右上角预览即可标注。</EmptyState>;
  return <div className="shot-grid">{state.shots.map(shot => <Card key={shot.id} className="shot-card">
    <CardContent className="p-0"><button type="button" className="card shot-open" data-shot={shot.id} onClick={() => openShot(shot.id)}>
      <img className="thumb" loading="lazy" alt="" src={"/image/" + shot.id + "/" + (shot.marked ? "marked" : "original") + "?v=" + shot.revision} />
      <span className="shot-info">
        <span className="shot-heading"><strong>{displayTime(shot)}</strong><Badge variant="secondary" className={"status-" + shot.status}>{shot.status === "queued" && state.agent !== "ready" ? "等待连接" : statuses[shot.status]}</Badge></span>
        <span className="shot-caption">{[...shot.tags, shot.note].filter(Boolean).join(" · ") || shot.name}</span>
      </span>
    </button></CardContent>
    {["done", "error"].includes(shot.status) && <Accordion type="single" collapsible className="shot-review"><AccordionItem value="report">
      <AccordionTrigger>{shot.status === "error" ? "查看失败原因" : "分析详情"}</AccordionTrigger><AccordionContent>
        <p className="whitespace-pre-wrap">{shot.error || shot.summary}</p>
        {shot.reasoningEffort && <p className="hint mt-2">推理强度 · {state.reasoningEfforts?.[shot.reasoningEffort] || shot.reasoningEffort}</p>}
        <Button type="button" size="sm" variant="outline" className="mt-3" data-retry={shot.id} onClick={() => api("/api/retry", { id: shot.id }).then(refresh).catch(reportError)}>重新分析</Button>
      </AccordionContent></AccordionItem></Accordion>}
  </Card>)}</div>;
}

function Issue({ item: { shot, issue, index }, refresh }) {
  const [saving, setSaving] = useState(false);
  async function resolve() {
    setSaving(true);
    try {
      await api("/api/resolve", { id: shot.id, index, revision: shot.analyzedRevision, resolved: !issue.resolved });
      await refresh();
    } catch (err) { reportError(err); } finally { setSaving(false); }
  }
  return <article className="issue">
    <div className="issue-copy">
      <div className="issue-meta"><Badge variant="secondary" className={"severity-" + issue.severity}>{severity[issue.severity]}</Badge>
        <span>{categories[issue.category]} · {displayTime(shot)}{issue.resolved && " · 已处理"}</span></div>
      <h3>{issue.description}</h3>
      <dl className="evidence">{[["画面文字", issue.sourceText], ["修改建议", issue.suggestion], ["参考词条", issue.reference]].filter(([, text]) => text).map(([name, text]) =>
        <div key={name}><dt>{name}</dt><dd>{text}</dd></div>)}</dl>
    </div>
    <div className="actions"><Button variant="outline" size="sm" data-shot={shot.id} onClick={() => openShot(shot.id)}>查看截图</Button>
      <Button variant="secondary" size="sm" disabled={saving} data-resolve={shot.id} onClick={resolve}>{issue.resolved ? "重新打开" : "标为已处理"}</Button></div>
  </article>;
}

function ResultActions({ all, showResolved, toggleResolved, project, entries = false, disabled }) {
  const filterId = entries ? "entries-resolved" : "toggle-resolved";
  const query = new URLSearchParams({ project: project.id });
  if (entries && showResolved) query.set("includeResolved", "1");
  const url = (entries ? "/api/export-entries?" : "/api/export?") + query;
  return <div className="actions"><Button variant="ghost" size="sm" id={filterId} aria-pressed={showResolved} onClick={toggleResolved}>
    {showResolved ? "隐藏已处理" : "显示已处理"} ({all.filter(x => x.issue.resolved).length})</Button>
    <Button variant="outline" size="sm" id={entries ? "export-entries" : "export"} disabled={disabled} onClick={() => { location.href = url; }}><Download />导出 CSV</Button></div>;
}

export function Issues(props) {
  const visible = props.all.filter(x => props.showResolved || !x.issue.resolved);
  return <><div className="pane-head"><ResultActions {...props} disabled={!props.all.length} /></div>
    {visible.length ? visible.map(item => <Issue key={item.shot.id + "/" + item.index} item={item} refresh={props.refresh} />)
      : <EmptyState icon={ListChecks} title="暂无待处理问题">完成分析后，有证据支持的问题会出现在这里。</EmptyState>}</>;
}

export function RelatedEntries({ cache, editProject, ...props }) {
  const { all, project, showResolved, refresh } = props;
  const signature = JSON.stringify([project.id, project.reference,
    all.map(({ shot, issue, index }) => [shot.id, shot.analyzedRevision, index, issue.reference, issue.referenceKeys])]);
  const [result, setResult] = useState(null), [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true;
    if (cache.current?.signature !== signature) {
      cache.current = { signature, promise: api("/api/related-entries?project=" + encodeURIComponent(project.id)) };
    }
    const request = cache.current;
    request.promise.then(data => { if (current) setResult({ signature, data }); }).catch(error => {
      if (cache.current === request) cache.current = null;
      if (current) setResult({ signature, error });
    });
    return () => { current = false; };
  }, [cache, signature, project.id, attempt]);
  if (result?.signature !== signature) return <p className="hint flex items-center gap-2" role="status"><LoaderCircle className="size-4 animate-spin" />正在核对语言包中的词条…</p>;
  if (result.error) return <EmptyState icon={ImageOff} title="无法读取语言包" action={<Button variant="outline" onClick={() => { setResult(null); setAttempt(i => i + 1); }}>重试</Button>}>{result.error.message}</EmptyState>;
  const { data } = result;
  if (data.status !== "ok") return <EmptyState icon={FileText} title={data.status === "no_reference" ? "项目尚未添加语言包" : "未识别到 Key 和中文列"}
    action={<Button variant="outline" onClick={editProject}>项目设置</Button>}>请在项目设置中选择含词条 Key 和中文的 Excel、CSV 或 TSV 总表。</EmptyState>;
  const visible = all.filter(x => showResolved || !x.issue.resolved);
  const byId = new Map(visible.map(x => [x.shot.id + "/" + x.index, x]));
  const linked = new Set();
  const rows = data.entries.map(entry => {
    const items = entry.issues.flatMap(link => {
      const id = link.shot + "/" + link.index, item = byId.get(id);
      if (!item || item.shot.analyzedRevision !== link.revision) return [];
      linked.add(id); return [item];
    });
    return { entry, items };
  }).filter(row => row.items.length);
  return <><div className="pane-head"><span className="hint mr-auto">{rows.length} 个条目{data.name && " · " + data.name}</span><ResultActions {...props} entries disabled={!rows.length} /></div>
    {visible.length > linked.size && <p className="hint mb-4">{visible.length - linked.size} 个问题尚未匹配到语言包词条，可在「待处理问题」中查看。</p>}
    {rows.length ? <Accordion type="multiple" className="entries">{rows.map(({ entry, items }) => {
      const id = JSON.stringify([entry.key, entry.chinese]);
      return <AccordionItem key={id} value={id} data-entry={id}><AccordionTrigger className="entry-trigger">
        <span className="entry-copy"><code>{entry.key}</code><span>{entry.chinese}</span></span><Badge variant="secondary">{items.length} 个问题</Badge>
        </AccordionTrigger><AccordionContent><p className="hint">{entry.locations.map(loc => [loc.source, loc.sheet, "第 " + loc.row + " 行"].filter(Boolean).join(" · ")).join("；")}</p>
          {items.map(item => <Issue key={item.shot.id + "/" + item.index} item={item} refresh={refresh} />)}</AccordionContent></AccordionItem>;
    })}</Accordion> : <EmptyState icon={FileText} title="暂无相关条目">问题引用的词条经语言包核实后会显示在这里。</EmptyState>}</>;
}
