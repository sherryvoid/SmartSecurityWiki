import {expect,test} from "@playwright/test";

test("History renders and copies the complete Compare answer",async({page,context})=>{
  await context.grantPermissions(["clipboard-read","clipboard-write"]);
  await page.addInitScript(()=>localStorage.setItem("security_codewiki_token","test"));
  const full="# Decision\n\nOpening paragraph (E1).\n\n## Numbered path\n\n1. First stage\n2. Second stage\n\n## Details\n\n- bullet one\n- bullet two\n\nFinal paragraph preserved.";
  await page.route("**/api/models/health",r=>r.fulfill({json:{ollama:{available:true,status:"Ready",available_models:["local"],default_model:"local"},gemini:{available:true,status:"Ready",default_model:"cloud"},openai:{available:false,status:"Not configured"},embedding:{}}}));
  await page.route("**/api/projects/full/status",r=>r.fulfill({json:{status:"indexed",project:{id:"full",name:"Full",source_type:"local",local_path:"repo",status:"indexed",created_at:"now",updated_at:"now"}}}));
  await page.route("**/api/projects/full/files/tree",r=>r.fulfill({json:[]})); await page.route("**/api/projects/full/wiki",r=>r.fulfill({json:[]})); await page.route("**/api/projects/full/usage",r=>r.fulfill({json:{overview:{requests:0},recent_executions:[]}}));
  await page.route("**/api/projects/full/formal-runs",r=>r.fulfill({json:[{run_id:"run",operation:"compare",question:"generic",timestamp:new Date().toISOString(),provider_model_json:JSON.stringify([{provider:"gemini",model:"cloud"}]),answer_json:JSON.stringify([{evaluation_id:"e",provider:"gemini",model:"cloud",answer:"preview only",answer_preview:"preview only",full_answer:full,validation_status:"valid_json",supplied_source_count:1,cited_source_count:1}]),primary_evidence_json:"[]",wiki_context_json:"[]",comparison_metadata_json:JSON.stringify({rq2_comparison_eligible:true}),execution_status:"completed",run_purpose:"development"}]}));
  await page.goto("/projects/full"); await page.getByRole("button",{name:"History",exact:true}).click(); await page.getByText(/generic/).click();
  await expect(page.getByText("Final paragraph preserved.")).toBeVisible();
  await page.getByRole("button",{name:"Copy full answer"}).first().click();
  const copied=await page.evaluate(()=>navigator.clipboard.readText());
  expect(copied.replace(/\r\n/g,"\n")).toBe(full);
});
