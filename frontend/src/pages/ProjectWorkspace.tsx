import Editor from "@monaco-editor/react";
import { type CSSProperties, type FormEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { ChevronLeft, ChevronRight, CircleAlert, CircleCheck, Clipboard, Download, ExternalLink, FileCode2, Filter, Folder, Info, Menu, Search, Settings, Trash2, X } from "lucide-react";
import { api, clearToken, downloadAuditReport, downloadExport, type ModelsHealth } from "../services/api";
import type { CompareResult, CompareRunSummary, Evidence, ExecutionDetails, FileNode, ModuleCandidate, Project } from "../types";

type Tab = "discover" | "security-wikis" | "ask" | "compare" | "history";
type WikiContext = { chunk_id?: string; wiki_id?: string; title?: string; section?: string; section_title?: string; source_focus?: string; module_id?: string; content?: string; relevance?: number; retrieval_rank?: number };
type Run = { id: string; timestamp: string; operation: "Ask" | "Compare" | "Wiki"; question: string; providerModel: string; status: string; latency: number; evidence: Evidence[]; wikiContext: WikiContext[]; answer?: string; comparison?: CompareResult[]; execution?: ExecutionDetails; compareSummary?: CompareRunSummary };

const providerOrder = ["gemini", "groq", "ollama", "openai"];
const providerNames: Record<string, string> = { gemini: "Gemini", groq: "Groq", ollama: "Local Ollama", openai: "OpenAI" };
const tabLabels: Record<Tab, string> = { discover: "Discover", "security-wikis": "Security Wikis", ask: "Ask", compare: "Compare", history: "History" };

function displayModel(provider: string, model?: string) {
  const clean = model || (provider === "gemini" ? "Gemini" : provider === "ollama" ? "Qwen" : providerNames[provider] || provider);
  return provider === "ollama" ? `${clean} · Local` : provider === "groq" ? `${clean} · Groq` : clean;
}

export default function ProjectWorkspace() {
  const { projectId = "" } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [tree, setTree] = useState<FileNode[]>([]);
  const [modelsHealth, setModelsHealth] = useState<ModelsHealth | null>(null);
  const [tab, setTab] = useState<Tab>("discover");
  const [provider, setProvider] = useState("gemini");
  const [model, setModel] = useState("");
  const [analysisFocus, setAnalysisFocus] = useState("");
  const [goal, setGoal] = useState("");
  const [question, setQuestion] = useState("");
  const [candidates, setCandidates] = useState<ModuleCandidate[]>([]);
  const [includeTests, setIncludeTests] = useState(false);
  const [includeDocs, setIncludeDocs] = useState(false);
  const [securityLevel, setSecurityLevel] = useState("all");
  const [discoveryLanguage, setDiscoveryLanguage] = useState("all");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsSection, setSettingsSection] = useState<"models" | "evaluation" | "usage" | "project" | null>(null);
  const [usage, setUsage] = useState<any>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(() => window.innerWidth > 820);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [sourceWidth, setSourceWidth] = useState(560);
  const [sidebarWidth, setSidebarWidth] = useState(280);
  const [fileContent, setFileContent] = useState("");
  const [filePath, setFilePath] = useState("");
  const [language, setLanguage] = useState("text");
  const [activeEvidence, setActiveEvidence] = useState<Evidence | null>(null);
  const [wikiPages, setWikiPages] = useState<Array<{ id: string; title: string; content_markdown: string; module_id: string; updated_at: string; model_provider?: string; model_name?: string; validation_status?: string }>>([]);
  const [viewedWiki, setViewedWiki] = useState<(typeof wikiPages)[number] | null>(null);
  const [answer, setAnswer] = useState("");
  const [answerId, setAnswerId] = useState("");
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [wikiContext, setWikiContext] = useState<WikiContext[]>([]);
  const [execution, setExecution] = useState<ExecutionDetails | null>(null);
  const [comparison, setComparison] = useState<CompareResult[]>([]);
  const [compareSummary, setCompareSummary] = useState<CompareRunSummary | null>(null);
  const [selectedCompareProviders, setSelectedCompareProviders] = useState<string[]>([]);
  const [history, setHistory] = useState<Run[]>([]);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState<{ message: string; technical?: string; retry?: () => void } | null>(null);
  const editorRef = useRef<any>(null);
  const monacoRef = useRef<any>(null);
  const decorationsRef = useRef<string[]>([]);
  const pendingRange = useRef<{ start: number; end: number; critical: number[] } | null>(null);

  useEffect(() => { void loadWorkspace(); }, [projectId]);
  useEffect(() => { const resize = () => { if (window.innerWidth <= 820) setSidebarOpen(false); }; window.addEventListener("resize", resize); resize(); return () => window.removeEventListener("resize", resize); }, []);
  useEffect(() => {
    if (!modelsHealth) return;
    const available = compareChoices.filter(name => modelAvailable(name));
    if (!modelAvailable(provider)) setProvider(available[0] || "ollama");
    setSelectedCompareProviders(available.slice(0, 2));
  }, [modelsHealth]);
  useEffect(() => { applyHighlight(); }, [fileContent, sourceOpen]);

  async function loadWorkspace() {
    try {
      const [status, health, pages] = await Promise.all([api.status(projectId), api.modelsHealth(), api.wikiPages(projectId)]);
      setProject(status.project); setModelsHealth(health); setWikiPages(pages);
      try { setUsage(await api.usage(projectId)); } catch { setUsage(null); }
      if (status.project.status === "indexed") setTree(await api.fileTree(projectId) as FileNode[]);
      setGoal(status.project.security_goal || "");
      try {
        const stored = await api.formalRuns(projectId);
        setHistory(stored.map((raw: any) => restoreDurableRun(raw)));
      } catch { /* history endpoint may be absent in older deployments */ }
    } catch (cause) { showError(cause, loadWorkspace); }
  }

  async function refreshUsage() { try { setUsage(await api.usage(projectId)); } catch { /* execution remains successful when observability refresh fails */ } }

  function baseProvider(name: string) { return name.split("::", 1)[0]; }
  function selectedModel(name: string) { return name.includes("::") ? name.split("::", 2)[1] : undefined; }
  function modelHealth(name: string): any { return modelsHealth?.[baseProvider(name) as keyof ModelsHealth]; }
  function modelAvailable(name: string) { const health = modelHealth(name); return Boolean(health?.available ?? health?.status === "Ready"); }
  function configuredModel(name: string) {
    if (selectedModel(name)) return selectedModel(name)!;
    const health = modelHealth(name);
    if (name === "ollama") return model || health?.default_model || health?.available_models?.[0] || "Qwen";
    return health?.default_model || providerNames[name];
  }
  const effectiveModel = configuredModel(provider);
  const compareChoices = useMemo(() => ["gemini", ...(modelsHealth?.groq?.available_models || []).map(name => `groq::${name}`), ...(modelsHealth?.ollama.available_models || []).map(name => `ollama::${name}`), "openai"], [modelsHealth]);

  function showError(cause: unknown, retry?: () => void) {
    let message = "Something went wrong. Please try again."; let technical = "";
    if (cause instanceof Error) {
      technical = cause.message;
      try { const parsed = JSON.parse(cause.message); const detail = parsed.detail || parsed; message = detail.user_message || detail.message || message; technical = detail.technical_message || cause.message; } catch { message = cause.message.replace(/^\{"detail":"?|"\}$/g, ""); }
    }
    setError({ message, technical, retry });
  }

  async function openSource(path: string, range?: { startLine: number; endLine: number; criticalLines?: number[] }, item?: Evidence) {
    try {
      pendingRange.current = range ? { start: range.startLine, end: range.endLine, critical: range.criticalLines || [] } : null;
      if (item) setActiveEvidence(item);
      const response = await api.fileContent(projectId, path);
      setFilePath(path); setFileContent(response.content); setLanguage(response.language); setSourceOpen(true);
      window.setTimeout(applyHighlight, 30);
    } catch (cause) { showError(cause, () => void openSource(path, range, item)); }
  }

  function applyHighlight() {
    const editor = editorRef.current, monaco = monacoRef.current, range = pendingRange.current;
    if (!editor || !monaco || !range) return;
    decorationsRef.current = editor.deltaDecorations(decorationsRef.current, [
      { range: new monaco.Range(range.start, 1, range.end, 1), options: { isWholeLine: true, className: "evidence-line-highlight" } },
      ...range.critical.map(line => ({ range: new monaco.Range(line, 1, line, 1), options: { isWholeLine: true, className: "critical-line-highlight", glyphMarginClassName: "critical-line-glyph" } })),
    ]);
    editor.revealLinesInCenter(range.start, range.end);
  }

  async function discover(event: FormEvent) {
    event.preventDefault(); setBusy("Finding security-relevant code…"); setError(null);
    try { const result = await api.discover(projectId, goal); setCandidates(result); }
    catch (cause) { showError(cause, () => void discover({ preventDefault() {} } as FormEvent)); }
    finally { setBusy(""); }
  }

  async function generateWiki() {
    if (!analysisFocus) return; setBusy("Generating Security Wiki…"); setError(null);
    try {
      const result = await api.generateWiki(projectId, analysisFocus, provider, effectiveModel);
      if (!result.stored) throw new Error(result.display_status || "The model returned a Security Wiki that could not be validated.");
      setWikiPages(await api.wikiPages(projectId));
      await refreshUsage();
      addRun({ id: result.execution?.execution_id || crypto.randomUUID(), timestamp: new Date().toISOString(), operation: "Wiki", question: analysisFocus, providerModel: displayModel(result.provider, result.model), status: result.display_status || "Ready", latency: Number(result.execution?.provider?.request_duration_ms || 0), evidence: result.evidence as Evidence[], wikiContext: [], answer: result.content_markdown, execution: result.execution });
    } catch (cause) { showError(cause, generateWiki); } finally { setBusy(""); }
  }

  async function runAsk(event?: FormEvent) {
    event?.preventDefault(); if (!question.trim()) return; setBusy("Retrieving evidence and running analysis…"); setError(null);
    try {
      const result = await api.chat(projectId, question, provider, effectiveModel, analysisFocus || undefined);
      await refreshUsage();
      setAnswer(result.answer); setAnswerId(result.message_id); setEvidence(result.evidence as Evidence[]); setWikiContext(result.wiki_context as WikiContext[]); setExecution(result.execution || null);
      addRun({ id: result.execution?.execution_id || result.message_id, timestamp: new Date().toISOString(), operation: "Ask", question, providerModel: displayModel(result.provider, result.model), status: result.display_status || result.validation_status, latency: Number(result.execution?.provider?.request_duration_ms || 0), evidence: result.evidence as Evidence[], wikiContext: result.wiki_context as WikiContext[], answer: result.answer, execution: result.execution });
    } catch (cause) { showError(cause, () => void runAsk()); } finally { setBusy(""); }
  }

  async function runCompare(event?: FormEvent) {
    event?.preventDefault(); if (!question.trim() || !selectedCompareProviders.length) return; setBusy("Running controlled comparison…"); setError(null);
    try {
      const result = await api.compare(projectId, question, selectedCompareProviders, analysisFocus || undefined);
      await refreshUsage();
      setComparison(result.results); setEvidence(result.evidence as Evidence[]); setWikiContext(result.wiki_context as WikiContext[]); setCompareSummary(result.run_summary);
      addRun({ id: result.run_summary.execution_id, timestamp: result.run_summary.completed_at || new Date().toISOString(), operation: "Compare", question, providerModel: result.results.map(item => displayModel(item.provider, item.model)).join(" · "), status: result.run_summary.comparison_valid ? "Completed" : "Completed with warnings", latency: result.run_summary.total_duration, evidence: result.evidence as Evidence[], wikiContext: result.wiki_context as WikiContext[], comparison: result.results, compareSummary: result.run_summary });
    } catch (cause) { showError(cause, () => void runCompare()); } finally { setBusy(""); }
  }

  function addRun(run: Run) { setHistory(current => [run, ...current.filter(item => item.id !== run.id)]); }
  function restoreRun(run: Run) {
    setQuestion(run.question); setEvidence(run.evidence); setWikiContext(run.wikiContext); setExecution(run.execution || null); setAnswer(run.answer || ""); setComparison(run.comparison || []); setCompareSummary(run.compareSummary || null);
    if (run.operation === "Compare") {
      const restoredProviders = (run.comparison || []).map(item => item.selection_id || item.provider).filter(Boolean);
      if (restoredProviders.length) setSelectedCompareProviders(Array.from(new Set(restoredProviders)));
      setTab("compare");
    } else if (run.operation === "Wiki") {
      const stored = wikiPages.find(page => page.module_id === run.question || page.title === run.question);
      setViewedWiki(stored || (run.answer ? { id: run.id, title: `${basename(run.question)} Security Wiki`, module_id: run.question, updated_at: run.timestamp, content_markdown: run.answer } : null));
      setTab("security-wikis");
    } else setTab("ask");
  }

  async function deleteWiki(id: string) { if (!window.confirm("Delete this generated Security Wiki?")) return; await api.deleteWikiPage(projectId, id); setWikiPages(current => current.filter(item => item.id !== id)); if (viewedWiki?.id === id) setViewedWiki(null); }

  const languages = Array.from(new Set(candidates.map(item => item.language))).sort();
  const filteredCandidates = candidates.filter(candidate => {
    const path = candidate.module_path.replace(/\\/g, "/").toLowerCase();
    const isTest = /(^|\/)(test|tests|spec)\//.test(path) || /(?:test|spec)\.[^.]+$/.test(path);
    const isDoc = /(^|\/)(docs?|readme)(\/|\.|$)/.test(path) || /\.(md|rst|txt)$/.test(path);
    return (includeTests || !isTest) && (includeDocs || !isDoc) && (securityLevel === "all" || candidate.confidence.toLowerCase() === securityLevel) && (discoveryLanguage === "all" || candidate.language === discoveryLanguage);
  });

  return <main className={`product-shell ${sourceOpen ? "source-visible" : ""} ${sidebarOpen ? "sidebar-visible" : ""}`} style={{ "--source-width": `${sourceWidth}px`, "--sidebar-width": `${sidebarWidth}px` } as CSSProperties}>
    <header className="app-header">
      <button className="icon-button mobile-sidebar" onClick={() => setSidebarOpen(value => !value)} aria-label="Toggle project files"><Menu /></button>
      <div className="brand"><strong>Security CodeWiki</strong><span>{project?.name || "Project"} · {project?.status === "indexed" ? "Indexed" : project?.status || "Loading"} · {project?.files_indexed ?? 0} files · {project?.chunks_indexed ?? 0} chunks</span></div>
      <label className="global-model"><span>Default model</span><select aria-label="Default model" value={provider} onChange={event => { setProvider(event.target.value); setModel(""); }}>{providerOrder.map(name => <option key={name} value={name} disabled={!modelAvailable(name)}>{displayModel(name, configuredModel(name))}{modelAvailable(name) ? "" : " — unavailable"}</option>)}</select>{provider === "ollama" && <select aria-label="Ollama model" value={effectiveModel} onChange={event => setModel(event.target.value)}>{modelsHealth?.ollama.available_models.map(name => <option key={name} value={name}>{name}</option>)}</select>}{provider === "groq" && <select aria-label="Groq model" value={effectiveModel} onChange={event => setModel(event.target.value)}>{modelsHealth?.groq.available_models.map(name => <option key={name} value={name}>{name}</option>)}</select>}</label>
      <div className="global-focus"><span>Analysis focus</span><div><strong>{analysisFocus ? basename(analysisFocus) : "None"}</strong>{analysisFocus && <button className="clear-focus" aria-label="Clear analysis focus" title="Clear analysis focus" onClick={() => setAnalysisFocus("")}><X/></button>}</div></div>
      <div className="header-actions">
        <button className="icon-button" aria-label="Model settings" onClick={() => setSettingsOpen(value => !value)}><Settings /></button>
        <button className="icon-button" aria-label="Export" onClick={() => setExportOpen(value => !value)}><Download /></button>
      </div>
      {settingsOpen && <Popover title="Settings" onClose={() => setSettingsOpen(false)}><nav className="settings-nav"><button onClick={() => { setSettingsSection("models"); setSettingsOpen(false); }}>Models &amp; Providers<span>Availability and deployed model IDs</span></button><button onClick={() => { setSettingsSection("evaluation"); setSettingsOpen(false); }}>Evaluation Configuration<span>Frozen methodology and inference settings</span></button><button onClick={() => { setSettingsSection("usage"); setSettingsOpen(false); }}>Usage &amp; Cost<span>Actual tokens, latency, and estimates</span></button><button onClick={() => { setSettingsSection("project"); setSettingsOpen(false); }}>Project Settings<span>Repository identity and session</span></button></nav></Popover>}
      {exportOpen && <Popover title="Export" onClose={() => setExportOpen(false)}><div className="menu-list"><button onClick={() => downloadExport(projectId, "markdown")}>Markdown</button><button onClick={() => downloadExport(projectId, "json")}>JSON</button><button onClick={() => downloadExport(projectId, "csv")}>CSV</button><button onClick={() => downloadAuditReport(projectId)}>HTML report / Print as PDF</button></div></Popover>}
    </header>
    {settingsSection && <SettingsModal title={({models:"Models & Providers",evaluation:"Evaluation Configuration",usage:"Usage & Cost",project:"Project Settings"} as Record<string,string>)[settingsSection]} onClose={() => setSettingsSection(null)}>{settingsSection === "usage" ? <UsageCostModal usage={usage}/> : settingsSection === "models" ? <dl className="settings-list">{providerOrder.map(name => <div key={name}><dt>{providerNames[name]}</dt><dd>{modelAvailable(name) ? displayModel(name, configuredModel(name)) : modelHealth(name)?.reason || "Not configured"}</dd></div>)}</dl> : settingsSection === "evaluation" ? <p>Effective model and evaluation configuration is recorded with every formal run and is inspectable in advanced Run details.</p> : <><dl className="settings-list"><div><dt>Repository</dt><dd>{project?.repo_url || project?.local_path}</dd></div><div><dt>Commit</dt><dd>{project?.commit_hash || "Unavailable"}</dd></div></dl><button onClick={() => { clearToken(); window.location.assign("/login"); }}>Sign out</button></>}</SettingsModal>}

    <aside className="file-sidebar">
      <div className="sidebar-resize" role="separator" aria-label="Resize project files" onPointerDown={event => beginSidebarResize(event, sidebarWidth, setSidebarWidth)} />
      <div className="sidebar-heading"><span>Project files</span><button className="icon-button" aria-label="Collapse project files" onClick={() => setSidebarOpen(false)}><ChevronLeft /></button></div>
      <div className="index-summary"><CircleCheck/><span><strong>Indexed successfully</strong><small>{project?.files_indexed ?? 0} files · {project?.chunks_indexed ?? 0} chunks</small></span><details><summary>View indexing details</summary><p>{project?.status_message}</p></details></div>
      <FileTree nodes={tree} onOpen={path => void openSource(path)} openedPath={filePath} focusPath={analysisFocus} />
    </aside>

    <section className="workflow-area">
      <nav className="primary-nav" aria-label="Primary workspace navigation">{(Object.keys(tabLabels) as Tab[]).map(name => <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>{tabLabels[name]}</button>)}</nav>
      {busy && <div className="busy-banner">{busy}</div>}
      {error && <ErrorNotice error={error} onClose={() => setError(null)} />}
      <div className="workflow-scroll">
        {tab === "discover" && <DiscoverView goal={goal} setGoal={setGoal} discover={discover} filtersOpen={filtersOpen} setFiltersOpen={setFiltersOpen} includeTests={includeTests} setIncludeTests={setIncludeTests} includeDocs={includeDocs} setIncludeDocs={setIncludeDocs} securityLevel={securityLevel} setSecurityLevel={setSecurityLevel} discoveryLanguage={discoveryLanguage} setDiscoveryLanguage={setDiscoveryLanguage} languages={languages} candidates={filteredCandidates} analysisFocus={analysisFocus} setAnalysisFocus={setAnalysisFocus} openSource={openSource} disabled={Boolean(busy)} />}
        {tab === "security-wikis" && <WikiView pages={wikiPages} viewed={viewedWiki} setViewed={setViewedWiki} focus={analysisFocus} model={displayModel(provider, effectiveModel)} generate={generateWiki} remove={deleteWiki} openSource={openSource} disabled={Boolean(busy)} />}
        {tab === "ask" && <AskView question={question} setQuestion={setQuestion} run={runAsk} model={displayModel(provider, effectiveModel)} focus={analysisFocus} answer={answer} evidence={evidence} wikiContext={wikiContext} execution={execution} answerId={answerId} projectId={projectId} openSource={openSource} activeEvidence={activeEvidence} disabled={Boolean(busy)} />}
        {tab === "compare" && <CompareView question={question} setQuestion={setQuestion} run={runCompare} providers={compareChoices} available={modelAvailable} reason={(name: string) => modelHealth(name)?.reason} modelName={configuredModel} selected={selectedCompareProviders} setSelected={setSelectedCompareProviders} focus={analysisFocus} results={comparison} evidence={evidence} wikiContext={wikiContext} summary={compareSummary} projectId={projectId} openSource={openSource} activeEvidence={activeEvidence} disabled={Boolean(busy)} />}
        {tab === "history" && <HistoryView runs={history} restore={restoreRun} />}
      </div>
    </section>

    {sourceOpen && <aside className="source-panel">
      <div className="source-resize" onPointerDown={event => beginResize(event, sourceWidth, setSourceWidth)} />
      <div className="source-header"><FileCode2/><strong className="source-breadcrumb">{activeEvidence ? `${basename(activeEvidence.file_path)} › ${activeEvidence.symbol_name || "source block"} › lines ${activeEvidence.start_line}–${activeEvidence.end_line}` : basename(filePath)}</strong><button onClick={() => setSourceOpen(false)}>Back to analysis</button><button className="icon-button" aria-label="Close source" onClick={() => setSourceOpen(false)}><X /></button></div>
      <Editor height="calc(100vh - 112px)" language={language} value={fileContent} theme="vs-dark" onMount={(editor, monaco) => { editorRef.current = editor; monacoRef.current = monaco; applyHighlight(); }} options={{ readOnly: true, minimap: { enabled: false }, glyphMargin: true, lineNumbers: "on" }} />
    </aside>}
  </main>;
}

function DiscoverView(props: any) {
  return <section className="workflow-page" aria-labelledby="discover-title"><PageHeading id="discover-title" title="Discover" purpose="Find security-relevant files, symbols, annotations, permissions, and configuration." />
    <div className="focus-banner"><div><span>Analysis focus</span><strong>{props.analysisFocus ? basename(props.analysisFocus) : "No file selected"}</strong><small>{props.analysisFocus ? "Questions and Wiki generation prioritize this file while still considering related security and helper files." : "Choose “Focus analysis” on a Discovery result to prioritize a file."}</small></div>{props.analysisFocus && <button className="tertiary-button" aria-label="Clear Discover analysis focus" onClick={() => props.setAnalysisFocus("")}>Clear</button>}</div>
    <form className="search-row" onSubmit={props.discover}><input aria-label="Discovery search" type="search" value={props.goal} onChange={(e: any) => props.setGoal(e.target.value)} placeholder="Search files, symbols, annotations, roles, permissions…"/><button disabled={props.disabled}><Search/> Search</button></form>
    <div className="filter-bar"><span>Showing: {props.includeTests || props.includeDocs ? "Custom file types" : "Implementation files"}</span><button className="secondary" onClick={() => props.setFiltersOpen(!props.filtersOpen)}><Filter/> Filters</button></div>
    {props.filtersOpen && <div className="filter-popover" role="region" aria-label="Discover filters"><label><input type="checkbox" checked={props.includeTests} onChange={(e: any) => props.setIncludeTests(e.target.checked)}/> Include test files</label><label><input type="checkbox" checked={props.includeDocs} onChange={(e: any) => props.setIncludeDocs(e.target.checked)}/> Include documentation files</label><label>Security relevance<select value={props.securityLevel} onChange={(e: any) => props.setSecurityLevel(e.target.value)}><option value="all">All</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label><label>Language<select value={props.discoveryLanguage} onChange={(e: any) => props.setDiscoveryLanguage(e.target.value)}><option value="all">All</option>{props.languages.map((item: string) => <option key={item}>{item}</option>)}</select></label><small>These filters change Discover results only. They do not change Ask or Compare retrieval.</small></div>}
    <div className="result-table" role="table" aria-label="Discovery results"><div className="result-header" role="row"><span>File</span><span>Why it matched</span><span>Symbols</span><span>Security relevance</span><span>Actions</span></div>{props.candidates.map((candidate: ModuleCandidate) => <div className="result-row" role="row" key={candidate.module_path} onDoubleClick={() => props.openSource(candidate.module_path)}><strong>{basename(candidate.module_path)}<small>{candidate.module_path}</small></strong><span>{candidate.reason}</span><span>{candidate.matching_symbols?.join(", ") || "—"}</span><span><Status text={candidate.confidence}/></span><span className="row-actions"><button onClick={() => props.openSource(candidate.module_path)}>Open source</button><button className={props.analysisFocus === candidate.module_path ? "selected" : ""} onClick={() => props.setAnalysisFocus(candidate.module_path)}>{props.analysisFocus === candidate.module_path ? "Focused" : "Focus analysis"}</button></span></div>)}{!props.candidates.length && <Empty text="Run a search to find security-relevant files."/>}</div>
  </section>;
}

function WikiView({ pages, viewed, setViewed, focus, model, generate, remove, openSource, disabled }: any) {
  if (viewed) return <section className="workflow-page wiki-detail"><button className="back-link" onClick={() => setViewed(null)}>← All Wikis</button><PageHeading title={viewed.title} purpose={`Security Wiki for ${basename(viewed.module_id || "")} · Generated with ${displayModel(viewed.model_provider || "", viewed.model_name) || "configured model"}`} /><WikiMarkdown content={viewed.content_markdown} openSource={openSource}/></section>;
  return <section className="workflow-page"><PageHeading title="Security Wikis" purpose="Generated security summaries for focused files or modules. Wikis help orient analysis but are not primary source evidence."/><div className="toolbar"><span>Analysis focus: <strong>{focus ? basename(focus) : "Not selected"}</strong></span><span>Model: <strong>{model}</strong></span><button disabled={disabled || !focus} onClick={generate}>Generate Wiki</button>{!focus && <small>Select an analysis focus before generating a Security Wiki.</small>}</div><div className="result-table wiki-library" role="table"><div className="result-header"><span>Wiki</span><span>Source focus</span><span>Model</span><span>Generated</span><span>Status</span><span>Actions</span></div>{pages.map((page: any) => <div className="result-row" key={page.id}><strong>{page.title}</strong><span>{basename(page.module_id)}</span><span>{displayModel(page.model_provider || "", page.model_name) || "Generated model"}</span><span>{formatDate(page.updated_at)}</span><span><Status text={page.validation_status || "Ready"}/></span><span className="row-actions"><button onClick={() => setViewed(page)}>View</button><button className="danger-text" onClick={() => remove(page.id)}><Trash2/> Delete</button></span></div>)}{!pages.length && <Empty text="No Security Wikis generated yet. Select an analysis focus to begin."/>}</div></section>;
}

function AskView(props: any) {
  return <section className="workflow-page"><PageHeading title="Ask" purpose="Ask one model about the repository using retrieved source evidence."/><Composer {...props} label="Run analysis"/><div className="result-layout">{props.answer && <article className="answer-panel"><ResultMeta model={props.model} execution={props.execution} content={props.answer}/><h2>Analysis result</h2><Markdown content={props.answer}/><details className="evaluation-panel"><summary>Evaluate result</summary><Evaluation projectId={props.projectId} evaluationId={props.answerId}/></details></article>}<ContextRail evidence={props.evidence} wikiContext={props.wikiContext} execution={props.execution} openSource={props.openSource} activeEvidence={props.activeEvidence}/></div></section>;
}

function Composer({ question, setQuestion, run, model, focus, label, disabled }: any) { return <form className="composer" onSubmit={run}><div className="composer-meta"><span>Model: <strong>{model}</strong></span><span>Analysis focus: <strong>{focus ? basename(focus) : "None"}</strong></span></div><textarea rows={3} value={question} onChange={event => setQuestion(event.target.value)} placeholder="Where is access control enforced?" aria-label="Question"/><button disabled={disabled || !question.trim()}>{label}</button></form>; }

function CompareView(props: any) {
  return <section className="workflow-page"><PageHeading title="Compare" purpose="Compare multiple models on the same question using identical evidence."/><fieldset className="model-choices"><legend>Models</legend>{props.providers.map((name: string) => { const base = name.split("::",1)[0]; return <label key={name} className={!props.available(name) ? "disabled" : ""}><input type="checkbox" aria-label={`${providerNames[base]} / ${props.modelName(name)}`} disabled={!props.available(name) || props.disabled} checked={props.selected.includes(name)} onChange={(e: any) => props.setSelected((current: string[]) => e.target.checked ? [...current, name] : current.filter(item => item !== name))}/><span><strong>{displayModel(base, props.modelName(name))}</strong><small>{props.available(name) ? "Ready" : props.reason(name) || "API key required"}</small></span></label>; })}</fieldset><Composer question={props.question} setQuestion={props.setQuestion} run={props.run} model={`${props.selected.length} models selected`} focus={props.focus} label="Compare models" disabled={props.disabled}/>{props.summary && <Integrity summary={props.summary} blocks={props.evidence.length}/>} {props.results.length > 0 && <div className="comparison-grid">{props.results.map((item: CompareResult) => { const failed = ["provider_unavailable","error","timeout"].includes(item.validation_status); const fullAnswer = item.full_answer || item.answer; return <article className={`model-answer ${failed ? "failed" : ""}`} key={item.selection_id || `${item.provider}-${item.model}`}><ResultMeta model={displayModel(item.provider, item.model)} execution={item.execution} status={item.display_status || item.validation_status} content={fullAnswer}/><small className="provider-kind">{item.provider === "ollama" ? "Local Ollama" : `${providerNames[item.provider] || item.provider} provider`}</small><p className="helper">Evidence supplied to model: {item.supplied_source_count ?? "Unavailable"} blocks · Evidence cited in answer: {item.cited_source_count ?? "Unavailable"} blocks</p><h2>{failed ? "Could not complete the request" : "Answer"}</h2><Markdown content={fullAnswer}/>{(item as any).error?.user_message && <p className="failure-reason"><strong>Reason:</strong> {(item as any).error.user_message}</p>}{item.warnings?.map(warning => <div className="grounding-warning" key={`${warning.code}-${warning.claim}`}><CircleAlert/> {warning.message}{warning.claim ? ` (${warning.claim})` : ""}</div>)}{failed && <button className="retry-model" onClick={() => props.run()}>Retry comparison</button>}{item.execution && <RunDetails execution={item.execution} warningCount={item.warnings?.length || 0}/>}<details className="evaluation-panel"><summary>Evaluate result</summary><Evaluation projectId={props.projectId} evaluationId={item.evaluation_id}/></details></article>; })}</div>}<ContextRail evidence={props.evidence} wikiContext={props.wikiContext} execution={props.results[0]?.execution} openSource={props.openSource} activeEvidence={props.activeEvidence} shared/></section>;
}

function ContextRail({ evidence, wikiContext, execution, openSource, activeEvidence, shared }: any) { if (!evidence?.length && !wikiContext?.length && !execution) return null; return <aside className="context-rail"><details className="evidence-section" open><summary>{shared ? "Shared primary evidence" : "Primary source evidence"} <span>{evidence.length} source blocks</span></summary><p className="helper"><Info/> Source-code and configuration blocks retrieved for this question. These are the primary evidence used to verify the answer.</p>{evidence.map((item: Evidence, index: number) => <EvidenceRow key={item.chunk_id} item={item} index={index} active={activeEvidence?.chunk_id === item.chunk_id} open={() => openSource(item.file_path, { startLine: item.start_line, endLine: item.end_line, criticalLines: item.critical_lines }, item)}/>)}</details><WikiContextView items={wikiContext}/>{execution && <RunDetails execution={execution} warningCount={0}/>}</aside>; }

function EvidenceRow({ item, index, active, open }: { item: Evidence; index: number; active: boolean; open: () => void }) { const role = priorityName(item.evidence_priority_class); return <article className={`evidence-row ${active ? "active" : ""}`} tabIndex={0} onKeyDown={e => { if (e.key === "Enter") open(); }}><span className="evidence-number">Evidence {index + 1}</span><div><strong>{basename(item.file_path)}</strong><span>{item.symbol_name || "Source block"} · lines {item.start_line}–{item.end_line}</span><small>{role}</small></div><button onClick={open}>Open source <ExternalLink/></button><details className="retrieval-details"><summary>Why selected</summary><ul>{item.selected_file_match && <li>Exact analysis-focus file</li>}{item.http_method && <li>HTTP endpoint declaration</li>}{item.security_tags && <li>Security annotation or permission match</li>}<li>{role}</li></ul><details><summary>Advanced retrieval details</summary><p><strong>Raw ranking score:</strong> {item.final_score ?? "Unavailable"}</p><p>Used internally to order evidence in this retrieval run. It is not a confidence, correctness, or probability score.</p><pre>{JSON.stringify({ vector_rank: item.retrieval_rank, lexical_score: item.lexical_score, base_similarity: item.base_similarity, file_weight: item.file_weight, chunk_type_weight: item.chunk_type_weight, test_file_penalty: item.test_file_penalty, security_boost: item.security_boost, selected_file_boost: item.selected_file_boost, final_score: item.final_score }, null, 2)}</pre></details></details></article>; }

function WikiContextView({ items }: { items: WikiContext[] }) { return <details className="wiki-context"><summary>Security Wiki context <span>{items.length} related Wiki sections used</span></summary><p className="helper">Generated Wiki sections may be included as supplementary context. Source code remains the evidence used to verify the answer.</p>{items.length ? <div className="mini-table"><div><strong>Wiki</strong><strong>Section</strong><strong>Source focus</strong><strong>Why selected</strong></div>{items.map((item, index) => <div key={item.chunk_id || index}><span>{item.title || "Security Wiki"}</span><span>{item.section || item.section_title || "Section"}</span><span>{basename(item.source_focus || item.module_id || "")}</span><span>{item.retrieval_rank ? `Semantic rank ${item.retrieval_rank}` : "Related to question"}</span></div>)}</div> : <Empty text="No related Wiki sections were used."/>}</details>; }

function RunDetails({ execution, warningCount }: { execution: ExecutionDetails; warningCount: number }) { const p = execution.provider || {}; const raw = JSON.stringify(execution, null, 2); return <details className="run-details"><summary>Run details</summary><dl><div><dt>Model</dt><dd>{String(p.model || "Unavailable")}</dd></div><div><dt>Provider</dt><dd>{providerNames[String(p.provider)] || String(p.provider || "Unavailable")}</dd></div><div><dt>Status</dt><dd>{readable(execution.status)}</dd></div><div><dt>Duration</dt><dd>{(Number(p.request_duration_ms || 0) / 1000).toFixed(1)} seconds</dd></div><div><dt>Evidence</dt><dd>{String(p.source_chunk_count || p.source_chunks_sent || 0)} source blocks · {String(p.wiki_chunks_sent || 0)} Wiki context sections</dd></div><div><dt>Grounding</dt><dd>{warningCount} source-reference warnings</dd></div></dl><details className="advanced"><summary>Advanced technical diagnostics</summary><div className="raw-content"><CopyButton content={raw} label="Copy raw content" compact/><pre>{raw}</pre></div></details></details>; }

function Evaluation({ projectId, evaluationId }: { projectId: string; evaluationId: string }) { const [values, setValues] = useState<Record<string, string>>({}); async function save() { await api.scoreEvaluation(projectId, evaluationId, { correctness: numberOrNull(values.correctness), evidence_discipline: numberOrNull(values.evidence), completeness: numberOrNull(values.completeness), explanation_quality: numberOrNull(values.explanation), source_reference_accuracy: numberOrNull(values.references), usefulness: numberOrNull(values.usefulness), hallucination: values.hallucination === "" ? null : values.hallucination === "true", verdict: values.verdict || null, notes: values.notes || null }); } return <div className="evaluation-form">{[["correctness","Correctness","0–3: correctness for question scope"],["evidence","Evidence discipline","0–3: grounding and stated uncertainty"],["completeness","Completeness","0–3: coverage of required answer"],["explanation","Explanation quality","0–3: clarity and precision"],["references","Source-reference accuracy","0–2: correct and inspectable references"],["usefulness","Usefulness","0–3: practical auditing value"]].map(([key,label,help]) => <label key={key}>{label}<small>{help}</small><select value={values[key] || ""} onChange={e => setValues(current => ({ ...current, [key]: e.target.value }))}><option value="">Not scored</option>{Array.from({ length: key === "references" ? 3 : 4 }, (_, i) => <option key={i} value={i}>{i}</option>)}</select></label>)}<label>Hallucination<select value={values.hallucination || ""} onChange={e => setValues(c => ({...c,hallucination:e.target.value}))}><option value="">Not assessed</option><option value="false">No</option><option value="true">Yes</option></select></label><label>Verdict<select value={values.verdict || ""} onChange={e => setValues(c => ({...c,verdict:e.target.value}))}><option value="">Select</option>{["Verified","Incomplete","Incorrect","Needs Review"].map(v => <option key={v}>{v}</option>)}</select></label><label className="notes">Evaluator notes<textarea value={values.notes || ""} onChange={e => setValues(c => ({...c,notes:e.target.value}))}/></label><button onClick={save}>Save evaluation</button></div>; }

function Integrity({ summary, blocks }: { summary: CompareRunSummary; blocks: number }) { const eligible = Boolean((summary as any).rq2_comparison_eligible ?? summary.comparison_valid); return <section className={`integrity ${eligible ? "valid" : "warning"}`}><div><strong>Controlled comparison</strong><span>{(summary as any).comparison_model_count || summary.selected_models.length} models · Identical primary evidence · {blocks} source blocks · {eligible ? "RQ2 eligible" : "Not RQ2 eligible"}</span></div><details><summary>View comparison details</summary><p>{summary.comparison_invalid_reason || "All completed models received identical ordered primary evidence and Wiki context."}</p><p>Evidence package hash: <code>{summary.shared_evidence_hash}</code></p><p>Wiki context hash: <code>{(summary as any).shared_wiki_context_hash || "No Wiki context"}</code></p></details></section>; }

function HistoryView({ runs, restore }: { runs: Run[]; restore: (run: Run) => void }) { const grouped = useMemo(() => groupRuns(runs), [runs]); return <section className="workflow-page"><PageHeading title="History" purpose="Review and reopen your saved runs."/>{Object.entries(grouped).map(([day, items]) => <section className="history-day" key={day}><h2>{day}</h2>{items.map(run => <div className="history-run" key={run.id}><button className="history-item" onClick={() => restore(run)}><time>{formatTime(run.timestamp)}</time><Status text={run.operation}/><span><strong>“{truncate(run.question, 100)}”</strong><small>{run.providerModel}</small></span><ChevronRight/></button></div>)}</section>)}{!runs.length && <Empty text="No saved runs yet."/>}</section>; }

function Markdown({ content, openSource }: { content: string; openSource?: (path: string, range?: any) => void }) { const lines = content.split(/\r?\n/); const output: ReactNode[] = []; let i = 0; while (i < lines.length) { const line = lines[i]; if (/^```/.test(line)) { const code: string[] = []; i++; while (i < lines.length && !/^```/.test(lines[i])) code.push(lines[i++]); output.push(<pre key={i}><code>{code.join("\n")}</code></pre>); i++; continue; } if (/^#{1,4}\s/.test(line)) { const level = line.match(/^#+/)![0].length; const heading = inline(line.replace(/^#+\s*/, "")); output.push(level <= 1 ? <h2 key={i}>{heading}</h2> : level === 2 ? <h3 key={i}>{heading}</h3> : <h4 key={i}>{heading}</h4>); i++; continue; } if (/^\|.*\|$/.test(line) && i + 1 < lines.length && /^\|?\s*:?-+/.test(lines[i + 1])) { const rows: string[][] = []; const headers = cells(line); i += 2; while (i < lines.length && /^\|.*\|$/.test(lines[i])) rows.push(cells(lines[i++])); output.push(<div className="markdown-table-wrap" key={i}><table><thead><tr>{headers.map((cell,j)=><th key={j}>{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row,r)=>{ const range = lineRangeForRow(row); return <tr key={r}>{row.map((cell,c)=><td key={c} title={looksLikePath(cell) ? cell.trim() : undefined}>{inline(cell)}{openSource && looksLikePath(cell) && <button className="inline-source" onClick={() => openSource(cell.trim(), range)}>Open source</button>}</td>)}</tr>; })}</tbody></table></div>); continue; } if (/^\s*[-*]\s+/.test(line)) { const items: string[] = []; while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*]\s+/, "")); output.push(<ul key={i}>{items.map((item,j)=><li key={j}>{inline(item)}</li>)}</ul>); continue; } if (/^\s*\d+\.\s+/.test(line)) { const items: string[]=[]; while(i<lines.length&&/^\s*\d+\.\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+\.\s+/,"")); output.push(<ol key={i}>{items.map((item,j)=><li key={j}>{inline(item)}</li>)}</ol>); continue; } if (line.trim()) output.push(<p key={i}>{inline(line)}</p>); i++; } return <div className="markdown">{output}</div>; }
function inline(text: string): ReactNode[] { const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g); return parts.map((part,i) => part.startsWith("**") ? <strong key={i}>{part.slice(2,-2)}</strong> : part.startsWith("`") ? <code key={i}>{part.slice(1,-1)}</code> : part); }

function PageHeading({ title, purpose, id }: { title: string; purpose: string; id?: string }) { return <header className="page-heading"><h1 id={id}>{title}</h1><p>{purpose}</p></header>; }
function CopyButton({ content, label, compact = false }: { content: string; label: string; compact?: boolean }) { const [state, setState] = useState<"idle"|"success"|"failure">("idle"); const timer = useRef<number | undefined>(undefined); useEffect(() => () => window.clearTimeout(timer.current), []); async function copy() { window.clearTimeout(timer.current); try { await navigator.clipboard.writeText(content); setState("success"); } catch { setState("failure"); } timer.current = window.setTimeout(() => setState("idle"), 1800); } const text = state === "success" ? "Copied" : state === "failure" ? "Copy failed" : label; const accessible = compact && state === "success" ? "Copied raw content" : compact && state === "failure" ? "Copy raw content failed" : text; return <button type="button" className={`copy-button ${compact ? "icon-button raw-copy" : ""} ${state}`} onClick={copy} aria-label={accessible} title={accessible}><span aria-live="polite">{state === "success" ? <CircleCheck/> : compact ? <Clipboard/> : null}{compact && state === "idle" ? <span className="sr-only">{label}</span> : !compact ? text : <span className="sr-only">{text}</span>}</span></button>; }

function ResultMeta({ model, execution, status, content }: { model: string; execution?: ExecutionDetails | null; status?: string; content: string }) { return <div className="result-meta"><strong>{model}</strong><span>{status || readable(execution?.status || "Completed")} · {(Number(execution?.provider?.request_duration_ms || 0) / 1000).toFixed(1)} s</span><CopyButton content={content} label="Copy full answer"/></div>; }
function Status({ text }: { text: string }) { return <span className={`status ${/ready|complete|high|ask/i.test(text) ? "success" : /warning|review|medium/i.test(text) ? "warning" : "neutral"}`}>{text.replace(/_/g, " ")}</span>; }
function Empty({ text }: { text: string }) { return <p className="empty-state">{text}</p>; }
function Popover({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) { return <section className="header-popover"><header><strong>{title}</strong><button className="icon-button" aria-label={`Close ${title}`} onClick={onClose}><X/></button></header>{children}</section>; }
function UsageCost({ usage }: { usage: any }) { const o = usage?.overview; const recent = usage?.recent_executions || []; if (!o) return <section><h3>Usage &amp; Cost</h3><p>Usage unavailable.</p></section>; const value = (v: any) => v == null ? "Unavailable" : Number(v).toLocaleString(); return <section className="usage-cost"><h3>Usage &amp; Cost</h3><h4>Overview</h4><dl className="settings-list"><div><dt>Requests</dt><dd>{value(o.requests)}</dd></div><div><dt>Actual input tokens</dt><dd>{value(o.actual_input_tokens)}</dd></div><div><dt>Actual output tokens</dt><dd>{value(o.actual_output_tokens)}</dd></div><div><dt>Actual total tokens</dt><dd>{value(o.actual_total_tokens)}</dd></div><div><dt>Cached tokens</dt><dd>{value(o.cached_tokens)}</dd></div><div><dt>Estimated cloud API cost</dt><dd>{o.estimated_cloud_api_cost == null ? "Unavailable" : o.estimated_cloud_api_cost}</dd></div><div><dt>Local generation time</dt><dd>{value(o.local_model_generation_time_ms)} ms</dd></div></dl><h4>By model</h4>{(usage.by_model || []).map((row:any)=><details key={row.model}><summary>{row.model} · {row.calls} calls · {row.total_tokens} tokens</summary><p>Input {row.input_tokens} · Output {row.output_tokens} · Average latency {Math.round(row.latency_ms / row.calls)} ms</p></details>)}<h4>By operation</h4>{(usage.by_operation || []).map((row:any)=><p key={row.operation}>{readable(row.operation)} · {row.calls} calls · {row.total_tokens} tokens</p>)}<h4>Recent executions</h4>{recent.slice(0,10).map((row:any)=><details key={row.execution_id}><summary>{readable(row.operation)} · {row.model} · {value(row.provider_reported_total_tokens)} tokens</summary><p>Provider-reported: input {value(row.provider_reported_input_tokens)}, output {value(row.provider_reported_output_tokens)}, cached {value(row.provider_reported_cached_input_tokens)}, reasoning/thinking {value(row.provider_reported_reasoning_tokens ?? row.provider_reported_thinking_tokens)}. Latency {value(row.request_duration_ms)} ms. Cost {row.api_cost == null ? "Unavailable" : row.api_cost}.</p><pre>{JSON.stringify(row.prompt_composition, null, 2)}</pre></details>)}</section>; }
function SettingsModal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) { return <div className="settings-modal-backdrop" onMouseDown={event => { if(event.target===event.currentTarget) onClose(); }}><section className="settings-modal" role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button className="icon-button" aria-label={`Close ${title}`} onClick={onClose}><X/></button></header><div className="settings-modal-body">{children}</div></section></div>; }
function UsageCostModal({ usage }: { usage:any }) {
  const [tab,setTab]=useState("overview"); const o=usage?.overview; const rows=tab==="models"?usage?.by_model:usage?.by_operation;
  const value=(v:any,s="")=>v==null?"Unavailable":`${Number(v).toLocaleString()}${s}`; const money=(v:any)=>v==null?"Unavailable":`$${Number(v).toFixed(6)}`;
  if(!o)return <p>Usage unavailable.</p>;
  return <section className="usage-cost-modal"><nav className="usage-tabs">{["overview","models","operations","executions"].map(name=><button className={tab===name?"active":""} onClick={()=>setTab(name)} key={name}>{readable(name)}</button>)}</nav>
    {tab==="overview"&&<><div className="usage-overview">{[["Requests",o.requests,"Actual"],["Actual input tokens",o.actual_input_tokens,"Actual"],["Actual output tokens",o.actual_output_tokens,"Actual"],["Actual total tokens",o.actual_total_tokens,"Actual"],["Cached tokens",o.cached_tokens,"Actual"],["Actual provider API cost",o.actual_provider_api_cost,"Actual"],["GPT-4o-mini equivalent estimate",o.gpt_equivalent_estimate,"Scenario estimate"],["Local generation time",o.local_model_generation_time_ms,"Actual"]].map(([label,v,kind])=><article key={String(label)}><small>{label}</small><strong>{String(label).includes("cost")||String(label).includes("estimate")?money(v):value(v,label==="Local generation time"&&v!=null?" ms":"")}</strong><span>{v==null?"Unavailable":kind}</span></article>)}</div><p className="pricing-note">Scenario estimate based on recorded token counts. Actual OpenAI tokenization, caching and model output length may differ. Pricing revision: <strong>{usage.scenario_pricing?.revision||"Unavailable"}</strong>, effective {usage.scenario_pricing?.effective_date||"Unavailable"}. Local hardware and electricity cost are outside the current measurement scope.</p></>}
    {(tab==="models"||tab==="operations")&&<div className="usage-table"><div><strong>{readable(tab.slice(0,-1))}</strong><strong>Provider</strong><strong>Calls</strong><strong>Actual input</strong><strong>Actual output</strong><strong>Reasoning</strong><strong>Total</strong><strong>Actual cost</strong><strong>Avg latency</strong></div>{(rows||[]).map((row:any)=><div key={row.model||row.operation}><span>{row.model||readable(row.operation)}</span><span>{row.provider||"Multiple"}</span><span>{row.calls}</span><span>{value(row.input_tokens)}</span><span>{value(row.output_tokens)}</span><span>{value(row.reasoning_tokens)}</span><span>{value(row.total_tokens)}</span><span>{money(row.actual_provider_api_cost)}</span><span>{value(row.calls?Math.round(row.latency_ms/row.calls):null," ms")}</span></div>)}</div>}
    {tab==="executions"&&<div className="usage-executions">{(usage.recent_executions||[]).map((row:any)=><details key={row.execution_id}><summary><time>{formatDate(row.created_at)}</time><strong>{readable(row.operation)}</strong><span>{row.model}</span><span>{value(row.provider_reported_input_tokens)} in</span><span>{value(row.provider_reported_output_tokens)} out</span><span>{value(row.provider_reported_total_tokens)} total</span><span>{value(row.request_duration_ms," ms")}</span></summary><div className="usage-detail"><p>{row.provider} / {row.model}</p><p><strong>Actual provider API cost:</strong> {money(row.api_cost)} · <strong>GPT-4o-mini equivalent estimate:</strong> {money(row.gpt_equivalent_estimate)}</p><p className="pricing-note">Scenario estimate based on this execution&apos;s recorded token counts. Actual OpenAI tokenization, caching and model output length may differ.</p><p>Source blocks: {row.supplied_source_chunk_ids?.length??"Unavailable"} · Wiki blocks: {row.supplied_wiki_chunk_ids?.length??"Unavailable"} · Cited blocks: {row.cited_source_chunk_ids?.length??"Unavailable"}</p><details className="advanced"><summary>Advanced usage and model configuration</summary>{Object.entries(row.model_configuration||{}).map(([key,item]:any)=><p key={key}><strong>{readable(key)}:</strong> {String(item??"Unavailable")}</p>)}{Object.entries(row.prompt_composition||{}).map(([key,item]:any)=><p key={key}><strong>{readable(key)}:</strong> {item?.characters??item} characters · {item?.token_count??"Unavailable"} estimated tokens</p>)}</details></div></details>)}</div>}
  </section>;
}
function ErrorNotice({ error, onClose }: { error: { message: string; technical?: string; retry?: () => void }; onClose: () => void }) { return <div className="error-notice" role="alert"><CircleAlert/><div><strong>{error.message}</strong>{error.technical && <details><summary>View technical details</summary><code>{error.technical}</code></details>}</div>{error.retry && <button onClick={error.retry}>Retry</button>}<button className="icon-button" aria-label="Dismiss error" onClick={onClose}><X/></button></div>; }
function FileTree({ nodes, onOpen, openedPath, focusPath }: { nodes: FileNode[]; onOpen: (path: string) => void; openedPath?: string; focusPath?: string }) { return <nav className="file-tree">{nodes.map(node => node.type === "file" ? <button className={`${openedPath === node.path ? "opened" : ""} ${focusPath === node.path ? "focused" : ""}`} key={node.path} title={node.path} onClick={() => onOpen(node.path)}><FileCode2/><span>{node.name}</span>{focusPath === node.path && <i className="focus-dot" title="Analysis focus"/>}</button> : <details key={node.path} open><summary title={node.path}><Folder/><span>{node.name}</span></summary><FileTree nodes={node.children || []} onOpen={onOpen} openedPath={openedPath} focusPath={focusPath}/></details>)}</nav>; }

function restoreDurableRun(raw: any): Run { const parse = (value: any, fallback: any) => { try { return typeof value === "string" ? JSON.parse(value) : value ?? fallback; } catch { return fallback; } }; const operation = String(raw.operation || "ask"); const answerData = parse(raw.answer_json, ""); const providers = parse(raw.provider_model_json, {}); const comparisonMetadata = parse(raw.comparison_metadata_json, {}); const selectedModels = Array.isArray(providers) ? providers : []; const compareSummary = operation === "compare" ? { execution_id: raw.run_id, question: raw.question || "", shared_evidence_package_id: comparisonMetadata.shared_evidence_package_id || "restored", shared_evidence_hash: comparisonMetadata.shared_evidence_hash || "unavailable", comparison_valid: Boolean(comparisonMetadata.rq2_comparison_eligible), comparison_invalid_reason: comparisonMetadata.rq2_comparison_eligible ? null : "Restored comparison was not RQ2 eligible.", started_at: raw.timestamp, completed_at: raw.timestamp, total_duration: 0, selected_models: selectedModels, ...comparisonMetadata } as CompareRunSummary : undefined; return { id: raw.run_id, timestamp: raw.timestamp, operation: operation === "compare" ? "Compare" : operation === "wiki" ? "Wiki" : "Ask", question: raw.question || "", providerModel: Array.isArray(providers) ? providers.map((p:any)=>displayModel(p.provider,p.model)).join(" · ") : displayModel(providers.provider || "", providers.model), status: raw.execution_status || "Completed", latency: 0, evidence: parse(raw.primary_evidence_json, []), wikiContext: parse(raw.wiki_context_json, []), answer: typeof answerData === "string" ? answerData : undefined, comparison: Array.isArray(answerData) ? answerData : undefined, compareSummary }; }
function WikiMarkdown({ content, openSource }: { content: string; openSource?: (path: string, range?: any) => void }) {
  const sections: Array<{ title: string; body: string }> = [];
  let current = { title: "Overview", body: "" };
  for (const line of content.split(/\r?\n/)) {
    const heading = line.match(/^#{1,3}\s+(.+)$/);
    if (heading) {
      if (current.body.trim()) sections.push(current);
      current = { title: heading[1].trim(), body: "" };
    } else current.body += `${line}\n`;
  }
  if (current.body.trim() || !sections.length) sections.push(current);
  return <div className="wiki-sections">{sections.map((section, index) => <section className={`wiki-section-card ${/limitations?/i.test(section.title) ? "limitations-card" : ""}`} key={`${section.title}-${index}`}><h2>{section.title}</h2><Markdown content={section.body} openSource={openSource}/></section>)}</div>;
}

function basename(path: string) { return path?.replace(/\\/g, "/").split("/").pop() || "Unavailable"; }
function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.valueOf()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); }
function formatTime(value: string) { const date = new Date(value); return Number.isNaN(date.valueOf()) ? "—" : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
function groupRuns(runs: Run[]) { return runs.reduce<Record<string, Run[]>>((all, run) => { const date = new Date(run.timestamp), now = new Date(); const key = date.toDateString() === now.toDateString() ? "Today" : date.toLocaleDateString([], { dateStyle: "long" }); (all[key] ||= []).push(run); return all; }, {}); }
function truncate(value: string, length: number) { return value.length > length ? `${value.slice(0,length)}…` : value; }
function readable(value: string) { return value.replace(/_/g, " ").replace(/^./, (c: string) => c.toUpperCase()); }
function priorityName(value?: string) { return ({ target_primary: "Target endpoint", required_supporting_role: "Required supporting evidence", route_or_class_context: "Route or class context", helper_or_execution_context: "Helper or execution context", optional_context: "Additional context" } as Record<string,string>)[value || ""] || "Relevant source evidence"; }
function cells(line: string) { return line.replace(/^\||\|$/g, "").split("|").map(cell => cell.trim()); }
function looksLikePath(value: string) { return /\.(java|kt|py|go|js|ts|cs|cpp|c|xml|ya?ml|properties)$/i.test(value.trim()); }
function lineRangeForRow(row: string[]) { for (const cell of row) { const match = cell.match(/\b(\d+)\s*[-–]\s*(\d+)\b/); if (match) return { startLine: Number(match[1]), endLine: Number(match[2]), criticalLines: [] }; } return undefined; }
function numberOrNull(value?: string) { return value == null || value === "" ? null : Number(value); }
function beginResize(event: React.PointerEvent, width: number, setWidth: (value: number) => void) { const start = event.clientX; const move = (e: PointerEvent) => setWidth(Math.max(420, Math.min(900, width + start - e.clientX))); const stop = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); }; window.addEventListener("pointermove", move); window.addEventListener("pointerup", stop); }
function beginSidebarResize(event: React.PointerEvent, width: number, setWidth: (value: number) => void) { const start = event.clientX; const move = (e: PointerEvent) => setWidth(Math.max(220, Math.min(420, width + e.clientX - start))); const stop = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); }; window.addEventListener("pointermove", move); window.addEventListener("pointerup", stop); }
