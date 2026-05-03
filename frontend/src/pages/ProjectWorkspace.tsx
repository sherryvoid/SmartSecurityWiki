import Editor from "@monaco-editor/react";
import { FormEvent, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import {
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Clock,
  Eye,
  FileText,
  RefreshCcw,
  Search,
  Send,
  SplitSquareHorizontal,
} from "lucide-react";
import { api, downloadExport, type ModelsHealth } from "../services/api";
import type { Evidence, FileNode, ModuleCandidate, Project } from "../types";

const providers = ["ollama", "gemini", "openai", "deepseek"];
const providerLabels: Record<string, string> = {
  ollama: "Local Qwen via Ollama",
  gemini: "Gemini",
  openai: "OpenAI",
  deepseek: "DeepSeek",
};

export default function ProjectWorkspace() {
  const { projectId = "" } = useParams();
  const [project, setProject] = useState<Project | null>(null);
  const [tree, setTree] = useState<FileNode[]>([]);
  const [fileContent, setFileContent] = useState("");
  const [filePath, setFilePath] = useState("");
  const [language, setLanguage] = useState("text");
  const [goal, setGoal] = useState("");
  const [candidates, setCandidates] = useState<ModuleCandidate[]>([]);
  const [selectedModule, setSelectedModule] = useState("");
  const [provider, setProvider] = useState("ollama");
  const [model, setModel] = useState("");
  const [wiki, setWiki] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [answerId, setAnswerId] = useState("");
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [wikiContext, setWikiContext] = useState<Array<{ title?: string; section_title?: string; content?: string }>>([]);
  const [contextUsed, setContextUsed] = useState("");
  const [comparison, setComparison] = useState<Array<{ provider: string; model: string; answer: string; latency_ms: number }>>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [lastVerification, setLastVerification] = useState("");
  const [isCodeCollapsed, setIsCodeCollapsed] = useState(false);
  const [lastModelUsed, setLastModelUsed] = useState("");
  const [modelsHealth, setModelsHealth] = useState<ModelsHealth | null>(null);
  const editorRef = useRef<any>(null);
  const monacoRef = useRef<any>(null);
  const decorationIdsRef = useRef<string[]>([]);
  const pendingHighlightRef = useRef<{ startLine: number; endLine: number; criticalLines: number[] } | null>(null);

  useEffect(() => {
    loadProject();
    loadModelsHealth();
    const timer = window.setInterval(loadProject, 3000);
    return () => window.clearInterval(timer);
  }, [projectId]);

  async function loadModelsHealth() {
    try {
      const health = await api.modelsHealth();
      setModelsHealth(health);
    } catch {
      setModelsHealth(null);
    }
  }

  async function loadProject() {
    if (!projectId) return;
    try {
      const status = await api.status(projectId);
      setProject(status.project);
      setGoal((current) => current || status.project.security_goal || "");
      if (status.project.status === "indexed") {
        api.fileTree(projectId).then((data) => setTree(data as FileNode[])).catch(() => undefined);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load project status");
    }
  }

  useEffect(() => {
    applyPendingHighlight();
  }, [fileContent]);

  function handleEditorMount(editor: any, monaco: any) {
    editorRef.current = editor;
    monacoRef.current = monaco;
    applyPendingHighlight();
  }

  function applyPendingHighlight() {
    const editor = editorRef.current;
    const monaco = monacoRef.current;
    const highlight = pendingHighlightRef.current;
    if (!editor || !monaco || !highlight) return;

    const decorations = [
      {
        range: new monaco.Range(highlight.startLine, 1, highlight.endLine, 1),
        options: { isWholeLine: true, className: "evidence-line-highlight" },
      },
      ...highlight.criticalLines.map((line) => ({
        range: new monaco.Range(line, 1, line, 1),
        options: { isWholeLine: true, className: "critical-line-highlight", glyphMarginClassName: "critical-line-glyph" },
      })),
    ];
    decorationIdsRef.current = editor.deltaDecorations(decorationIdsRef.current, decorations);
    editor.revealLineInCenter(highlight.startLine);
  }

  async function openFile(path: string, range?: { startLine: number; endLine: number; criticalLines?: number[] }) {
    setError("");
    setBusy("Opening source file...");
    try {
      const response = await api.fileContent(projectId, path);
      setFilePath(path);
      setFileContent(response.content);
      setLanguage(response.language);
      setIsCodeCollapsed(false);
      pendingHighlightRef.current = range ? { startLine: range.startLine, endLine: range.endLine, criticalLines: range.criticalLines ?? [] } : null;
      setMessage(range ? `Opened ${path} at lines ${range.startLine}-${range.endLine}` : `Opened ${path}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open file");
    } finally {
      setBusy("");
    }
  }

  async function discover(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy("Scanning indexed chunks for security-relevant modules...");
    try {
      const results = await api.discover(projectId, goal);
      setCandidates(results);
      if (results[0]) setSelectedModule(results[0].module_path);
      setMessage(results.length ? `Discovery found ${results.length} candidate modules.` : "No security candidates found for this goal.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Security discovery failed");
    } finally {
      setBusy("");
    }
  }

  async function generateWiki() {
    if (!selectedModule) return;
    setError("");
    setBusy("Retrieving evidence and generating Security Wiki...");
    try {
      const result = await api.generateWiki(projectId, selectedModule, provider, model || undefined);
      setWiki(result.content_markdown);
      setEvidence(result.evidence as Evidence[]);
      setLastModelUsed(`${providerLabels[provider] ?? provider} / ${model || "default model"}`);
      setMessage("Security Wiki generated from retrieved source evidence.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Wiki generation failed");
    } finally {
      setBusy("");
    }
  }

  async function ask(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy("Retrieving evidence and asking selected model...");
    try {
      const result = await api.chat(projectId, question, provider, model || undefined, selectedModule || undefined);
      setAnswer(result.answer);
      setAnswerId(result.message_id);
      setEvidence(result.evidence as Evidence[]);
      setWikiContext(result.wiki_context as Array<{ title?: string; section_title?: string; content?: string }>);
      setContextUsed(result.context_used);
      setLastModelUsed(`${providerLabels[result.provider] ?? result.provider} / ${result.model}`);
      setLastVerification("");
      setMessage(`Answer ready with ${result.evidence.length} evidence block(s).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Chat request failed");
    } finally {
      setBusy("");
    }
  }

  async function compareModels() {
    setError("");
    setBusy("Retrieving one evidence package and comparing models...");
    try {
      const result = await api.compare(projectId, question, providers, selectedModule || undefined);
      setComparison(result.results);
      setEvidence(result.evidence as Evidence[]);
      setWikiContext(result.wiki_context as Array<{ title?: string; section_title?: string; content?: string }>);
      setContextUsed(result.wiki_context.length ? "raw code + wiki context" : "raw code evidence only");
      setMessage(`Comparison complete with ${result.evidence.length} shared evidence block(s).`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Model comparison failed");
    } finally {
      setBusy("");
    }
  }

  async function mark(verdict: string) {
    if (!answerId) return;
    setError("");
    setBusy(`Saving ${verdict} verification...`);
    try {
      await api.verify(projectId, "chat_message", answerId, verdict);
      setLastVerification(verdict);
      setMessage(`Saved auditor verdict: ${verdict}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Verification failed");
    } finally {
      setBusy("");
    }
  }

  const projectReady = project?.status === "indexed";
  const hasModule = Boolean(selectedModule);
  const hasQuestion = question.trim().length > 0;
  const actionDisabled = Boolean(busy);
  const chatDisabled = actionDisabled || !projectReady || !hasQuestion;
  const wikiDisabled = actionDisabled || !projectReady || !hasModule;
  const discoveryDisabled = actionDisabled || !projectReady;
  const evidenceConfidence = evidence.length >= 5 ? "High" : evidence.length >= 2 ? "Medium" : evidence.length === 1 ? "Low" : "No evidence";
  const selectedProviderHealth = modelsHealth?.[provider as keyof ModelsHealth] as any;
  const selectedProviderStatus = selectedProviderHealth?.status ?? "Unknown";
  const selectedProviderReady = selectedProviderStatus === "Ready";

  return (
    <main className={`workspace ${isCodeCollapsed ? "code-collapsed" : ""}`}>
      <aside className="sidebar">
        <section className="project-status">
          <h2>{project?.name ?? "Project"}</h2>
          <p>{project?.status ?? "loading"} / {project?.status_message}</p>
          <StatusSteps status={project?.status ?? "loading"} />
          <button onClick={loadProject} disabled={actionDisabled}><RefreshCcw size={16} /> Refresh</button>
        </section>
        <FileTree nodes={tree} onOpen={openFile} />
      </aside>

      <section className="editor-pane" aria-hidden={isCodeCollapsed}>
        <div className="pane-title">
          <FileText size={16} />
          <span>{filePath || "Select a source file"}</span>
          <button className="pane-toggle" onClick={() => setIsCodeCollapsed(true)} title="Collapse code viewer">
            <ChevronRight size={16} />
          </button>
        </div>
        <Editor height="calc(100vh - 150px)" language={language} value={fileContent} theme="vs-dark" onMount={handleEditorMount} options={{ readOnly: true, minimap: { enabled: false }, lineNumbers: "on", glyphMargin: true }} />
      </section>

      <aside className="audit-pane">
        {isCodeCollapsed && (
          <button className="restore-code" onClick={() => setIsCodeCollapsed(false)} title="Show code viewer">
            <ChevronLeft size={16} /> Show Code
          </button>
        )}

        <div className="audit-header">
          <div>
            <strong>{providerLabels[provider] ?? provider}</strong>
            <span>{model || "default model"} / {projectReady ? "ready" : "waiting for indexing"}</span>
          </div>
          <StatusBadge ready={projectReady} />
        </div>
        {busy && <div className="loading-banner"><Clock size={16} /> {busy}</div>}
        {error && <div className="error-banner"><CircleAlert size={16} /> {error}</div>}
        {message && <p className="status-line">{message}</p>}

        <div className="audit-scroll">
          <form className="tool-section" onSubmit={discover}>
            <SectionTitle title="Candidate Security Files / Modules" required={false} />
            <p className="field-help">Discovery groups security-relevant files by tags, symbols, and audit-goal terms. For MVP, a selected module means the selected candidate file and its related evidence.</p>
            <textarea value={goal} onChange={(event) => setGoal(event.target.value)} placeholder="Optional audit goal, e.g. Understand access control for account tokens" disabled={actionDisabled} />
            <button type="submit" disabled={discoveryDisabled}><Search size={16} /> {busy ? "Working..." : "Discover"}</button>
            <div className="candidate-list">
              {candidates.map((candidate) => (
                <article key={candidate.module_path} className={selectedModule === candidate.module_path ? "candidate selected" : "candidate"}>
                  <button type="button" onClick={() => setSelectedModule(candidate.module_path)} disabled={actionDisabled}>
                    <strong>{candidate.module_path}</strong>
                    <span>{candidate.language} / {candidate.confidence} / {candidate.reason}</span>
                    {candidate.security_tags.length > 0 && <small>Tags: {candidate.security_tags.join(", ")}</small>}
                    {candidate.matching_symbols && candidate.matching_symbols.length > 0 && <small>Symbols: {candidate.matching_symbols.join(", ")}</small>}
                    <small>Matching chunks: {candidate.matching_chunk_count ?? 0}</small>
                  </button>
                  <div className="candidate-actions">
                    <button type="button" className="ghost-button" onClick={() => openFile(candidate.module_path)} disabled={actionDisabled}>
                      <Eye size={14} /> Open
                    </button>
                    <button type="button" className="ghost-button" onClick={() => setSelectedModule(candidate.module_path)} disabled={actionDisabled}>
                      Select for Analysis
                    </button>
                    <button type="button" className="ghost-button" onClick={generateWiki} disabled={wikiDisabled || selectedModule !== candidate.module_path}>
                      Generate Wiki
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </form>

          <section className="tool-section">
            <SectionTitle title="Model" required />
            <select value={provider} onChange={(event) => setProvider(event.target.value)} disabled={actionDisabled}>
              <option value="ollama">Local Qwen via Ollama</option>
              <option value="gemini">Gemini</option>
              <option value="openai">OpenAI</option>
              <option value="deepseek">DeepSeek</option>
            </select>
            <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="Optional model override" disabled={actionDisabled} />
            <div className={selectedProviderReady ? "provider-health ready" : "provider-health warning"}>
              <strong>{providerLabels[provider] ?? provider}: {selectedProviderStatus}</strong>
              {provider === "ollama" && modelsHealth?.ollama && (
                <span>
                  {modelsHealth.ollama.reachable
                    ? `Default ${modelsHealth.ollama.default_model} ${modelsHealth.ollama.default_model_exists ? "found" : "not found"}`
                    : "Ollama is not reachable"}
                </span>
              )}
              {provider !== "ollama" && selectedProviderHealth && (
                <span>{selectedProviderHealth.api_key_configured ? "API key configured" : "API key not configured"}</span>
              )}
              {!selectedProviderReady && <span>Manual model override is allowed, but this provider may fail until configured.</span>}
            </div>
            {modelsHealth?.embedding && (
              <div className={modelsHealth.embedding.fallback_used ? "provider-health warning" : "provider-health ready"}>
                <strong>Embeddings: {modelsHealth.embedding.provider} / {modelsHealth.embedding.model}</strong>
                <span>{modelsHealth.embedding.fallback_used ? "Hash fallback is active; semantic embeddings are not active." : "Semantic local embeddings are active."}</span>
                {modelsHealth.embedding.warning && <span>{modelsHealth.embedding.warning}</span>}
              </div>
            )}
            <p className="field-help">Compulsory: provider. Optional: model override. Confidence is based on retrieved evidence count: {evidenceConfidence}.</p>
            {lastModelUsed && <p className="model-used">Last response used: {lastModelUsed}</p>}
          </section>

          <section className="tool-section">
            <SectionTitle title="Security Wiki" required={false} />
            <p className="field-help">Optional. Generates stored documentation for the selected module from retrieved evidence. Chat can work without this because it retrieves raw code evidence directly.</p>
            <button onClick={generateWiki} disabled={wikiDisabled}><FileText size={16} /> {busy ? "Working..." : "Generate Wiki"}</button>
            {wiki && <pre className="markdown-preview output-box">{wiki}</pre>}
          </section>

          <form className="tool-section" onSubmit={ask}>
            <SectionTitle title="Evidence Chat" required />
            <p className="field-help">Compulsory: question. Optional: selected module. If a module is selected, retrieval is focused on that file.</p>
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Where is access control enforced?" disabled={actionDisabled || !projectReady} />
            <div className="button-row">
              <button type="submit" disabled={chatDisabled}><Send size={16} /> {busy ? "Working..." : "Ask"}</button>
              <button type="button" onClick={compareModels} disabled={chatDisabled}><SplitSquareHorizontal size={16} /> Compare</button>
            </div>
            {answer && (
              <article className="answer output-box">
                <h4>Direct Answer</h4>
                {contextUsed && <p className="context-used">Context used: {contextUsed}</p>}
                <p>{answer}</p>
                <div className="button-row verification-row">
                  {["Verified", "Incomplete", "Incorrect", "Needs Review"].map((verdict) => (
                    <button
                      key={verdict}
                      type="button"
                      className={`verdict ${verdict.toLowerCase().replace(/\s+/g, "-")} ${lastVerification === verdict ? "active" : ""}`}
                      onClick={() => mark(verdict)}
                      disabled={actionDisabled}
                    >
                      <Check size={14} /> {verdict}
                    </button>
                  ))}
                </div>
              </article>
            )}
          </form>

          {evidence.length > 0 && (
            <section className="tool-section">
              <h3>Evidence / {evidenceConfidence}</h3>
              {evidence.map((item) => (
                <button
                  key={item.chunk_id}
                  className="evidence-card"
                  onClick={() => openFile(item.file_path, { startLine: item.start_line, endLine: item.end_line, criticalLines: item.critical_lines })}
                  disabled={actionDisabled}
                >
                  <strong>{item.file_path}</strong>
                  <span>{item.symbol_name || "block"} / lines {item.start_line}-{item.end_line}</span>
                  {item.critical_lines && item.critical_lines.length > 0 && <span>Critical lines: {item.critical_lines.join(", ")}</span>}
                  <code>{item.code_snippet.slice(0, 240)}</code>
                </button>
              ))}
            </section>
          )}

          {wikiContext.length > 0 && (
            <section className="tool-section">
              <details>
                <summary>Wiki context used ({wikiContext.length})</summary>
                {wikiContext.map((item, index) => (
                  <article className="comparison-card output-box" key={`${item.section_title}-${index}`}>
                    <strong>{item.title || "Security Wiki"} / {item.section_title || "Section"}</strong>
                    <p>{item.content}</p>
                  </article>
                ))}
              </details>
            </section>
          )}

          {comparison.length > 0 && (
            <section className="tool-section">
              <h3>Model Evaluation</h3>
              {comparison.map((item) => (
                <article className="comparison-card output-box" key={item.provider}>
                  <strong>{item.provider} / {item.model}</strong>
                  <span>{item.latency_ms} ms</span>
                  <p>{item.answer}</p>
                </article>
              ))}
            </section>
          )}

          <section className="tool-section">
            <h3>Export</h3>
            <div className="button-row">
              <button type="button" onClick={() => downloadExport(projectId, "markdown")} disabled={actionDisabled}>Markdown</button>
              <button type="button" onClick={() => downloadExport(projectId, "json")} disabled={actionDisabled}>JSON</button>
              <button type="button" onClick={() => downloadExport(projectId, "csv")} disabled={actionDisabled}>CSV</button>
            </div>
          </section>
        </div>
      </aside>
    </main>
  );
}

function SectionTitle({ title, required }: { title: string; required: boolean }) {
  return (
    <div className="section-title">
      <h3>{title}</h3>
      <span className={required ? "required" : "optional"}>{required ? "Required" : "Optional"}</span>
    </div>
  );
}

function StatusBadge({ ready }: { ready: boolean }) {
  return ready ? (
    <span className="ready-badge"><CircleCheck size={14} /> Ready</span>
  ) : (
    <span className="waiting-badge"><Clock size={14} /> Indexing</span>
  );
}

function StatusSteps({ status }: { status: string }) {
  const steps = [
    ["fetching", "Fetching repo"],
    ["indexing", "Indexing code"],
    ["indexed", "Ready"],
  ];
  const activeIndex = steps.findIndex(([key]) => key === status);
  const completedIndex = status === "indexed" ? steps.length - 1 : activeIndex - 1;
  return (
    <ol className="status-steps">
      {steps.map(([key, label], index) => (
        <li key={key} className={index <= completedIndex ? "done" : key === status ? "active" : ""}>{label}</li>
      ))}
    </ol>
  );
}

function FileTree({ nodes, onOpen }: { nodes: FileNode[]; onOpen: (path: string) => void }) {
  return (
    <nav className="file-tree">
      {nodes.map((node) => (
        <div key={node.path}>
          {node.type === "file" ? (
            <button onClick={() => onOpen(node.path)}>{node.name}</button>
          ) : (
            <details open>
              <summary>{node.name}</summary>
              <FileTree nodes={node.children ?? []} onOpen={onOpen} />
            </details>
          )}
        </div>
      ))}
    </nav>
  );
}
