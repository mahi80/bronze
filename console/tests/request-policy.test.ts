// Run with: npm test   (Node's own test runner; no extra packages)
import assert from "node:assert/strict";
import { test } from "node:test";
import { backendTarget, ConsoleError, readJsonBody, requireSameOrigin } from "../src/lib/request-policy.ts";

const none = new URLSearchParams();
const refused = (run: () => unknown, status: number) => assert.throws(run, (error) => error instanceof ConsoleError && error.status === status);

test("only the routes the console uses are forwarded", () => {
  for (const path of ["overview", "runs", "runs/12", "quality", "data/profile", "data/rows", "gold/sla_performance_monthly", "tickets/TKT-1000", "review", "agents", "agents/job"]) {
    assert.equal(backendTarget("GET", path.split("/"), none), path);
  }
  for (const path of ["runs", "review/mappings", "review/rules", "agents/classify", "agents/dq"]) {
    assert.equal(backendTarget("POST", path.split("/"), none), path);
  }
  for (const [method, path] of [["GET", "docs"], ["GET", "openapi.json"], ["GET", "runs/1/../../docs"], ["GET", "gold/pg_user;select"],
    ["GET", "tickets/TKT 1"], ["POST", "overview"], ["POST", "agents/other"], ["DELETE", "runs"], ["PUT", "review/rules"]]) {
    refused(() => backendTarget(method, path.split("/"), none), 404);
  }
});

test("query parameters are allow-listed and numeric", () => {
  assert.equal(backendTarget("GET", ["runs"], new URLSearchParams("limit=5")), "runs?limit=5");
  assert.equal(backendTarget("GET", ["data", "rows"], new URLSearchParams("limit=100")), "data/rows?limit=100");
  refused(() => backendTarget("GET", ["data", "profile"], new URLSearchParams("limit=5")), 400);
  refused(() => backendTarget("GET", ["runs"], new URLSearchParams("limit=5&limit=6")), 400);
  refused(() => backendTarget("GET", ["runs"], new URLSearchParams("limit=5;drop")), 400);
  refused(() => backendTarget("GET", ["overview"], new URLSearchParams("limit=5")), 400);
});

test("a change must come from one of the console's own origins", () => {
  const allowed = ["http://localhost:3000"];
  const request = (headers: Record<string, string>) => new Request("http://localhost:3000/api/backend/runs", { method: "POST", headers });
  requireSameOrigin(request({ origin: "http://localhost:3000", "sec-fetch-site": "same-origin" }), allowed);
  refused(() => requireSameOrigin(request({}), allowed), 403);                                              // no Origin header
  refused(() => requireSameOrigin(request({ origin: "http://evil.example" }), allowed), 403);
  refused(() => requireSameOrigin(request({ origin: "http://localhost:3000", "sec-fetch-site": "cross-site" }), allowed), 403);
});

test("bodies must be JSON and within the size limit", async () => {
  const post = (body: string, type = "application/json") => new Request("http://localhost:3000/x", { method: "POST", body, headers: { "content-type": type } });
  assert.deepEqual(await readJsonBody(post('{"decision":"approve"}')), { decision: "approve" });
  await assert.rejects(readJsonBody(post("a=1", "application/x-www-form-urlencoded")), (error) => error instanceof ConsoleError && error.status === 415);
  await assert.rejects(readJsonBody(post("{not json")), (error) => error instanceof ConsoleError && error.status === 400);
  await assert.rejects(readJsonBody(post(JSON.stringify({ pad: "x".repeat(200) })), 100), (error) => error instanceof ConsoleError && error.status === 413);
});
