import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Github, Info, MoreVertical, ShieldCheck, Trash2, Upload } from "lucide-react";
import { api } from "../services/api";
import type { Project } from "../types";

type ImportType = "github" | "zip" | "android";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [name, setName] = useState("");
  const [sourceType, setSourceType] = useState<ImportType>("github");
  const [repoUrl, setRepoUrl] = useState("");
  const [subfolderPath, setSubfolderPath] = useState("");
  const [androidSourceUrl, setAndroidSourceUrl] = useState("");
  const [androidCaseStudy, setAndroidCaseStudy] = useState("account-manager-service");
  const [securityGoal, setSecurityGoal] = useState("");
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [caseStudies, setCaseStudies] = useState<Array<{ id: string; name: string; hint: string }>>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [deletingProjectId, setDeletingProjectId] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    api.projects().then(setProjects).catch(() => undefined);
    api.androidCaseStudies().then(setCaseStudies).catch(() => undefined);
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      let project: Project;
      if (sourceType === "zip") {
        if (!zipFile) throw new Error("Choose a ZIP file.");
        const form = new FormData();
        form.set("name", name);
        form.set("security_goal", securityGoal);
        form.set("file", zipFile);
        project = await api.createZipProject(form);
      } else {
        project = await api.createProject({
          name,
          source_type: sourceType,
          repo_url: sourceType === "github" ? repoUrl : undefined,
          android_source_url: sourceType === "android" ? androidSourceUrl : undefined,
          android_case_study: sourceType === "android" ? androidCaseStudy : undefined,
          subfolder_path: sourceType === "github" || sourceType === "android" ? subfolderPath : undefined,
          security_goal: securityGoal
        });
      }
      navigate(`/projects/${project.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Project creation failed");
    }
  }

  async function removeProject(project: Project) {
    if (!window.confirm(`Remove project "${project.name}" and delete its stored files, chunks, vectors, and evaluation data?`)) {
      return;
    }
    setError("");
    setMessage("");
    setDeletingProjectId(project.id);
    try {
      const result = await api.deleteProject(project.id);
      setProjects((current) => current.filter((item) => item.id !== project.id));
      setMessage(result.message || `Removed ${project.name}.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Project removal failed");
    } finally {
      setDeletingProjectId("");
    }
  }

  return (
    <main className="projects-page">
      <section className="projects-content">
        <form className="new-project" onSubmit={submit}>
          <div className="card-heading"><h2>New project</h2><p>Import a repository or upload source code for analysis.</p></div>
          <div className="info-callout"><Info/><span>For very large repositories such as Android AOSP, select a subfolder or curated case-study package. Full AOSP import is not supported in this prototype.</span></div>
          <label>
            Project name
            <input value={name} onChange={(event) => setName(event.target.value)} required />
          </label>
          <div className="segmented" role="group" aria-label="Repository source">
            <button type="button" className={sourceType === "github" ? "active" : ""} onClick={() => setSourceType("github")}><Github size={16} /> GitHub</button>
            <button type="button" className={sourceType === "zip" ? "active" : ""} onClick={() => setSourceType("zip")}><Upload size={16} /> ZIP</button>
            <button type="button" className={sourceType === "android" ? "active" : ""} onClick={() => setSourceType("android")}><ShieldCheck size={16} /> Android</button>
          </div>
          {sourceType === "github" && (
            <div className="form-grid">
              <label>
                Repository URL
                <input value={repoUrl} onChange={(event) => setRepoUrl(event.target.value)} placeholder="https://github.com/org/repo.git" />
              </label>
              <label>
                Optional Subfolder Path
                <input value={subfolderPath} onChange={(event) => setSubfolderPath(event.target.value)} placeholder="src/main/java" />
              </label>
            </div>
          )}
          {sourceType === "zip" && (
            <label>
              ZIP Upload
              <input type="file" accept=".zip" onChange={(event) => setZipFile(event.target.files?.[0] ?? null)} />
            </label>
          )}
          {sourceType === "android" && (
            <div className="form-grid">
              <label>
                Android Case Study
                <select value={androidCaseStudy} onChange={(event) => setAndroidCaseStudy(event.target.value)}>
                  {caseStudies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </label>
              <label>
                Android Project Link
                <input value={androidSourceUrl} onChange={(event) => setAndroidSourceUrl(event.target.value)} placeholder="GitHub URL or Android source package link" />
              </label>
              <label>
                Optional Subfolder Path
                <input value={subfolderPath} onChange={(event) => setSubfolderPath(event.target.value)} placeholder="services/core/java/com/android/server/accounts" />
              </label>
            </div>
          )}
          <label>
            Security goal <span className="optional-label">Optional</span>
            <textarea value={securityGoal} onChange={(event) => setSecurityGoal(event.target.value)} placeholder="Understand access control for account tokens" />
          </label>
          {error && <p className="form-error" role="alert">{error}</p>}
          {message && <p className="status-line">{message}</p>}
          <button className="primary-button form-submit" type="submit">Create Project</button>
        </form>
        <section className="existing-projects">
          <div className="card-heading"><h2>Existing projects</h2><p>Open an indexed repository to continue analysis.</p></div>
          <div className="project-list" role="table" aria-label="Existing projects">
            <div className="project-list-header" role="row"><span>Project</span><span>Source</span><span>Status</span><span>Created</span><span>Actions</span></div>
            {projects.map((project) => (
              <article key={project.id} className="project-row">
                <button type="button" className="project-open" onClick={() => navigate(`/projects/${project.id}`)}><strong>{project.name}</strong></button>
                <span className="source-type">{project.source_type}</span><span className={`status ${project.status === "indexed" ? "success" : "neutral"}`}>{project.status}</span><time>{project.created_at ? new Date(project.created_at).toLocaleDateString() : "Unavailable"}</time>
                <details className="project-actions"><summary aria-label={`Actions for ${project.name}`} title="Project actions"><MoreVertical/></summary><div><button type="button" onClick={() => navigate(`/projects/${project.id}`)}>Open</button><button type="button" className="danger-text" onClick={() => removeProject(project)} disabled={deletingProjectId === project.id}><Trash2/> {deletingProjectId === project.id ? "Removing…" : "Remove project"}</button></div></details>
              </article>
            ))}
            {projects.length === 0 && <p className="empty-state">No projects yet.</p>}
          </div>
        </section>
      </section>
    </main>
  );
}
