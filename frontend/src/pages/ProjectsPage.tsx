import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Github, ShieldCheck, Trash2, Upload } from "lucide-react";
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
    <main className="page">
      <section className="workspace-grid">
        <form className="panel" onSubmit={submit}>
          <h2>Create Project</h2>
          <div className="warning-note">
            For very large repositories like full Android AOSP, use selected subfolders or curated case-study packages. Full AOSP is not supported in this MVP.
          </div>
          <label>
            Project Name
            <input value={name} onChange={(event) => setName(event.target.value)} required />
          </label>
          <div className="segmented">
            <button type="button" className={sourceType === "github" ? "active" : ""} onClick={() => setSourceType("github")}><Github size={16} /> GitHub</button>
            <button type="button" className={sourceType === "zip" ? "active" : ""} onClick={() => setSourceType("zip")}><Upload size={16} /> ZIP</button>
            <button type="button" className={sourceType === "android" ? "active" : ""} onClick={() => setSourceType("android")}><ShieldCheck size={16} /> Android</button>
          </div>
          {sourceType === "github" && (
            <>
              <label>
                GitHub URL
                <input value={repoUrl} onChange={(event) => setRepoUrl(event.target.value)} placeholder="https://github.com/org/repo.git" />
              </label>
              <label>
                Optional Subfolder Path
                <input value={subfolderPath} onChange={(event) => setSubfolderPath(event.target.value)} placeholder="src/main/java" />
              </label>
            </>
          )}
          {sourceType === "zip" && (
            <label>
              ZIP Upload
              <input type="file" accept=".zip" onChange={(event) => setZipFile(event.target.files?.[0] ?? null)} />
            </label>
          )}
          {sourceType === "android" && (
            <>
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
            </>
          )}
          <label>
            Optional Security Goal
            <textarea value={securityGoal} onChange={(event) => setSecurityGoal(event.target.value)} placeholder="Understand access control for account tokens" />
          </label>
          {error && <p className="error">{error}</p>}
          {message && <p className="status-line">{message}</p>}
          <button type="submit">Create Project</button>
        </form>
        <section className="panel">
          <h2>Projects</h2>
          <div className="project-list">
            {projects.map((project) => (
              <article key={project.id} className="project-row">
                <button type="button" className="project-open" onClick={() => navigate(`/projects/${project.id}`)}>
                  <strong>{project.name}</strong>
                  <span>{project.source_type} / {project.status}</span>
                </button>
                <button
                  type="button"
                  className="danger-button"
                  onClick={() => removeProject(project)}
                  disabled={deletingProjectId === project.id}
                  title="Remove project"
                >
                  <Trash2 size={15} /> {deletingProjectId === project.id ? "Removing" : "Remove Project"}
                </button>
              </article>
            ))}
            {projects.length === 0 && <p className="empty-state">No projects yet.</p>}
          </div>
        </section>
      </section>
    </main>
  );
}
