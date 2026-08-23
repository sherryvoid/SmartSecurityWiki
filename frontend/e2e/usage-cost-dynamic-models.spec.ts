import { expect, test } from "@playwright/test";

test("dynamic Ollama models and Usage & Cost use compact Settings navigation", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("security_codewiki_token", "test"));
  await page.route("**/api/models/health", r => r.fulfill({json:{ollama:{reachable:true,available:true,reason:"Ready",available_models:["nemotron-3.5-lightning:latest","qwen3.5:9b"],default_model:"qwen3.5:9b",default_model_exists:true,status:"Ready",base_url:"local"},gemini:{available:true,reason:"Ready",status:"Ready",api_key_configured:true,default_model_configured:true,default_model:"gemini-2.5-flash"},openai:{available:false,reason:"OPENAI_API_KEY not set",status:"Not configured",api_key_configured:false,default_model_configured:true,default_model:"gpt-4o-mini"},embedding:{provider:"hash",model:"test",semantic:false,fallback_used:true,label:"test"}}}));
  await page.route("**/api/projects/usage/status", r => r.fulfill({json:{status:"indexed",status_message:"Ready",project:{id:"usage",name:"Usage audit",source_type:"github",local_path:"repo",status:"indexed",status_message:"Ready",created_at:"now",updated_at:"now"}}}));
  await page.route("**/api/projects/usage/files/tree", r => r.fulfill({json:[]}));
  await page.route("**/api/projects/usage/wiki", r => r.fulfill({json:[]}));
  await page.route("**/api/projects/usage/formal-runs", r => r.fulfill({json:[]}));
  await page.route("**/api/projects/usage/usage", r => r.fulfill({json:{overview:{requests:2,actual_input_tokens:4100,actual_output_tokens:2120,actual_total_tokens:6220,cached_tokens:0,estimated_cloud_api_cost:null,local_model_generation_time_ms:60616},by_model:[{model:"qwen3.5:9b",calls:1,input_tokens:2050,output_tokens:1214,total_tokens:3264,latency_ms:36817}],by_operation:[{operation:"compare",calls:2,input_tokens:4100,output_tokens:2120,total_tokens:6220,latency_ms:173766}],recent_executions:[{execution_id:"qwen",operation:"compare",provider:"ollama",model:"qwen3.5:9b",provider_reported_input_tokens:2050,provider_reported_output_tokens:1214,provider_reported_total_tokens:3264,provider_reported_cached_input_tokens:null,provider_reported_reasoning_tokens:null,request_duration_ms:36817,api_cost:0,prompt_composition:{primary_source_content:{characters:10188,token_count:2547,token_count_type:"estimated"}}}]}}));
  await page.goto("/projects/usage");
  await page.getByRole("button", {name:"Compare", exact:true}).click();
  await expect(page.getByRole("checkbox", {name:/nemotron-3.5-lightning:latest/i})).toBeVisible();
  await expect(page.getByRole("checkbox", {name:/qwen3.5:9b/i})).toBeVisible();
  await expect(page.getByText(/DeepSeek/i)).toHaveCount(0);
  await page.getByLabel("Model settings").click();
  await expect(page.getByRole("button", {name:/Models & Providers/})).toBeVisible();
  await page.getByRole("button", {name:/Usage & Cost/}).click();
  await expect(page.getByRole("heading", {name:"Usage & Cost"})).toBeVisible();
  await expect(page.getByText("Actual input tokens")).toBeVisible();
  await expect(page.getByText("Unavailable", {exact:true}).first()).toBeVisible();
  await page.getByRole("button", {name:"Models", exact:true}).click();
  await expect(page.getByText("qwen3.5:9b", {exact:true})).toBeVisible();
  await page.getByRole("button", {name:"Executions", exact:true}).click();
  await expect(page.getByText("36,817 ms", {exact:true})).toBeVisible();
  await page.screenshot({path:"e2e/screenshots/usage-cost-settings.png",fullPage:true});
});
