import { FormEvent, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Upload, Github, ShieldCheck } from "lucide-react";
import { api } from "../services/api";
import type { Project } from "../types";

type ImportType = "github" | "zip" | "android";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [name, setName] = useState("");
  const [sourceType, setSourceType] = useState<ImportType>("github");
  const [repoUrl, setRepoUrl] = useState("");
  const [androidSourceUrl, setAndroidSourceUrl] = useState("");
  const [androidCaseStudy, setAndroidCaseStudy] = useState("account-manager-service");
  const [securityGoal, setSecurityGoal] = useState("");
  const [zipFile, setZipFile] = useState<File | null>(null);
  const [caseStudies, setCaseStudies] = useState<Array<{ id: string; name: string; hint: string }>>([]);
  const [error, setError] = useState("");
  const navigate = useNavigate();

  useEffect(() => {
    api.projects().then(setProjects).catch(() => undefined);
    api.androidCaseStudies().then(setCaseStudies).catch(() => undefined);
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
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
          security_goal: securityGoal
        });
      }
      navigate(`/projects/${project.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Project creation failed");
    }
  }

  return (
    <main className="page">
      <section className="workspace-grid">
        <form className="panel" onSubmit={submit}>
          <h2>Create Project</h2>
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
            <label>
              GitHub URL
              <input value={repoUrl} onChange={(event) => setRepoUrl(event.target.value)} placeholder="https://github.com/org/repo.git" />
            </label>
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
            </>
          )}
          <label>
            Optional Security Goal
            <textarea value={securityGoal} onChange={(event) => setSecurityGoal(event.target.value)} placeholder="Understand access control for account tokens" />
          </label>
          {error && <p className="error">{error}</p>}
          <button type="submit">Create Project</button>
        </form>
        <section className="panel">
          <h2>Projects</h2>
          <div className="project-list">
            {projects.map((project) => (
              <button key={project.id} onClick={() => navigate(`/projects/${project.id}`)}>
                <strong>{project.name}</strong>
                <span>{project.source_type} · {project.status}</span>
              </button>
            ))}
          </div>
        </section>
      </section>
    </main>
  );
}
